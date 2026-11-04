# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical grocery item matching and parse persistence (I6, ADR 0019)."""

import re
import sqlite3
import tomllib
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib import resources

from larder import db, ingredients_db
from larder.ingredients.parse import parse_line
from larder.ingredients.units import REVIEW_THRESHOLD

_DESCRIPTORS = frozenset(
    {"fresh", "chopped", "sliced", "diced", "minced", "grated", "small", "large"}
)


@dataclass(frozen=True)
class Match:
    """Best canonical candidate; None means no shared normalized token."""

    item_id: int | None
    confidence: float

    @property
    def needs_review(self) -> bool:
        return self.confidence < REVIEW_THRESHOLD


def normalize_item(text: str) -> str:
    """Lowercase, drop preparation descriptors and singularize conservatively."""
    words = re.findall(r"\w+", text.casefold())
    return " ".join(_singular(word) for word in words if word not in _DESCRIPTORS)


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("atoes", "toes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def match_item(conn: sqlite3.Connection, item_text: str) -> Match:
    """Match canonical names, then aliases, then token-overlapping candidates.

    Exact normalized names/aliases score 1. Fuzzy candidates are scored with
    difflib, capped below REVIEW_THRESHOLD so an uncertain name stays in review.
    Equal fuzzy scores use canonical-name/id order. No cache hides learned aliases.
    """
    normalized = normalize_item(item_text)
    if not normalized:
        return Match(None, 0.0)
    catalog = ingredients_db.items(conn)
    for item in catalog:
        if normalized == normalize_item(item["canonical_name"]):
            return Match(item["id"], 1.0)
    for alias in ingredients_db.aliases(conn):
        if normalized == normalize_item(alias["alias"]):
            return Match(alias["item_id"], 1.0)
    tokens = set(normalized.split())
    best_id = None
    best_score = 0.0
    for item in catalog:
        name = normalize_item(item["canonical_name"])
        if tokens.intersection(name.split()):
            score = SequenceMatcher(None, normalized, name).ratio()
            if score > best_score:
                best_id, best_score = item["id"], score
    return Match(best_id, min(best_score, REVIEW_THRESHOLD - 0.01))


def seed_items(conn: sqlite3.Connection) -> None:
    """Insert missing seed rows at startup; never update or delete user edits."""
    source = resources.files("larder") / "data" / "seed_items.toml"
    catalog = tomllib.loads(source.read_text(encoding="utf-8"))
    items = [
        (item["name"], item["category"], item.get("aliases", []))
        for item in catalog["items"]
    ]
    with db.transaction(conn):
        ingredients_db.insert_seed(conn, catalog["categories"], items)


def parse_and_match_recipe(conn: sqlite3.Connection, recipe_id: int) -> None:
    """Atomically parse unconfirmed raw lines and replace their parsed fields.

    Confidence is the minimum of parsing and all choice matches. The primary
    candidate is retained even for review; unmatched alternatives keep NULL ids.
    S18 wires this callable into indexing and applies cached parse overrides.
    """
    with db.transaction(conn):
        for line in ingredients_db.unconfirmed_lines(conn, recipe_id):
            parsed = parse_line(line["raw_text"])
            primary = match_item(conn, parsed.item_text)
            choices = [(text, match_item(conn, text)) for text in parsed.alternatives]
            confidence = min(
                parsed.confidence,
                primary.confidence,
                *(match.confidence for _, match in choices),
            )
            ingredients_db.write_parse(
                conn,
                line["id"],
                parsed,
                primary.item_id,
                confidence,
                [(text, match.item_id) for text, match in choices],
            )
