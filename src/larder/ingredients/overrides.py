# SPDX-License-Identifier: AGPL-3.0-only
"""Permanent human answers keyed by the same raw ingredient line (I5)."""

import math
import sqlite3
from collections.abc import Mapping
from dataclasses import replace

from larder import db, ingredients_db
from larder.ingredients.parse import ParsedLine, parse_line


def normalize_line(raw: str) -> str:
    """Casefold and collapse whitespace, preserving every other raw token."""
    return " ".join(raw.casefold().split())


def apply(conn: sqlite3.Connection, line_id: int, raw: str) -> bool:
    """Apply a cached answer in the caller's transaction."""
    return ingredients_db.apply_override(conn, line_id, normalize_line(raw))


def _quantity(text: str) -> float | None:
    if not text.strip():
        return None
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Quantity must be finite and nonnegative")
    return value


def answer(
    conn: sqlite3.Connection, line_id: int, action: str, fields: Mapping[str, str]
) -> None:
    """Validate and save one answer atomically, including optional new item.

    Confirm uses stored suggested fields; edit replaces the line fields and
    first choice. Both preserve the existing alternative list, including NULL ids.
    """
    with db.transaction(conn):
        line = ingredients_db.review_line(conn, line_id)
        if line is None:
            raise LookupError("Line no longer available")
        original = parse_line(line["raw_text"])
        parsed = ParsedLine(
            line["raw_text"],
            line["qty"],
            line["qty_max"],
            line["unit"],
            original.item_text,
            (),
            line["notes"] or "",
            bool(line["optional"]),
            bool(line["is_food"]),
            1.0,
        )
        item_id = line["item_id"]
        if action == "edit":
            qty = _quantity(fields.get("qty", ""))
            qty_max = _quantity(fields.get("qty_max", ""))
            if qty_max is not None and (qty is None or qty_max < qty):
                raise ValueError("Invalid quantity range")
            unit = fields.get("unit", "") or None
            if unit is not None and unit not in {
                row["name"] for row in ingredients_db.review_units(conn)
            }:
                raise ValueError("Unknown unit")
            name = fields.get("new_item", "").strip()
            selected = "" if name else fields.get("item_id", "")
            item_id = int(selected) if selected else None
            if item_id is not None and item_id not in {
                row["id"] for row in ingredients_db.items(conn)
            }:
                raise ValueError("Unknown item")
            if name:
                item_id = ingredients_db.create_review_item(conn, name)
            parsed = replace(
                parsed,
                qty=qty,
                qty_max=qty_max,
                unit=unit,
                notes=fields.get("notes", ""),
                optional=fields.get("optional") == "1",
            )
        elif action == "non_food":
            parsed = replace(parsed, is_food=False)
        elif action != "confirm":
            raise ValueError("Unknown action")
        if parsed.is_food and item_id is None:
            raise ValueError("Choose an item first")
        normalized = normalize_line(parsed.raw)
        ingredients_db.save_override(
            conn,
            line_id,
            normalized,
            parsed,
            item_id,
            original.item_text.casefold().strip(),
        )
        for pending in ingredients_db.active_unconfirmed_lines(conn):
            if normalize_line(pending["raw_text"]) == normalized:
                apply(conn, pending["id"], pending["raw_text"])
