# SPDX-License-Identifier: AGPL-3.0-only
"""Parse and write one recipe file (ADR 0009, ADR 0018).

A recipe file is Markdown with ``+++`` TOML front matter. ``parse_recipe``
turns it into a ``RecipeDoc``; ``serialize_recipe`` writes it back.

``RecipeDoc.body`` holds the Markdown after the front matter, verbatim. The
other fields (``intro``, ``ingredients``, ``directions``, ``notes``) are views
read from it. The serializer writes ``body``, so a stray ``# Title`` line, a
nutrition block or any other section survives a round trip. A caller that
edits a recipe edits ``body``.

Reading normalizes line endings to ``\\n`` and drops a leading byte order mark.
Ingredient lines stay raw text. Parsing them is a separate step.
"""

import datetime
import math
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FENCE = "+++"
# A code fence opens with 3+ backticks (no backtick in the info string) or 3+
# tildes. It closes on a line of only the same character, at least as long.
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}(?=[^`]*$)|~{3,})")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_H1 = re.compile(r"^#[ \t]+")
_H2 = re.compile(r"^##[ \t]+(.*?)[ \t]*$")
_H3 = re.compile(r"^###[ \t]+(.*?)[ \t]*$")
_CLOSING_HASHES = re.compile(r"[ \t]+#+$")
_THEMATIC_BREAK = re.compile(r"^ {0,3}([-*_])([ \t]*\1){2,}[ \t]*$")
_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(.*)$")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")

# Heading name (case-folded, trailing colon removed) -> section kind.
_SECTIONS = {
    "ingredients": "ingredients",
    "directions": "directions",
    "steps": "directions",
    "notes": "notes",
}


class RecipeParseError(ValueError):
    """A recipe file could not be parsed. The message names the recipe."""


@dataclass(frozen=True)
class IngredientLine:
    """One raw ingredient list item.

    ``line`` is the 1-based line number in ``RecipeDoc.body`` where the item
    starts. ``group`` is the ``###`` sub-group heading above it, or None.
    """

    text: str
    line: int
    group: str | None = None


@dataclass
class RecipeDoc:
    slug: str
    front_matter: dict[str, Any]
    intro: str
    ingredients: list[IngredientLine]
    directions: list[str]
    notes: str
    body: str

    @property
    def is_draft(self) -> bool:
        """True when the front matter says ``draft = true`` (ADR 0018)."""
        return self.front_matter.get("draft") is True


def read_recipe(path: Path) -> RecipeDoc:
    """Read a recipe file. The slug is the filename without ``.md``."""
    return parse_recipe(path.read_text(encoding="utf-8"), path.stem)


def parse_recipe(text: str, slug: str) -> RecipeDoc:
    """Parse the text of a recipe file.

    Raises RecipeParseError when the front matter is missing, not closed or not
    valid TOML.
    """
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines[0].rstrip() != _FENCE:
        raise RecipeParseError(
            f"recipe {slug!r}: no TOML front matter (the file must start with a "
            f"{_FENCE!r} line)"
        )
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == _FENCE), None)
    if close is None:
        raise RecipeParseError(
            f"recipe {slug!r}: the front matter has no closing {_FENCE!r} line"
        )
    try:
        front_matter = tomllib.loads("\n".join(lines[1:close]))
    except tomllib.TOMLDecodeError as exc:
        raise RecipeParseError(
            f"recipe {slug!r}: invalid TOML front matter: {exc}"
        ) from exc
    body_lines = lines[close + 1 :]
    intro, ingredients, directions, notes = _read_body(body_lines)
    return RecipeDoc(
        slug=slug,
        front_matter=front_matter,
        intro=intro,
        ingredients=ingredients,
        directions=directions,
        notes=notes,
        body="\n".join(body_lines),
    )


# A body line: (1-based line number in the body, raw text, kind). Kind is
# "text", "h3" (a ### heading) or "code" (a fence line or a line inside one).
_Line = tuple[int, str, str]


def _split_sections(body_lines: list[str]) -> list[tuple[str, list[_Line]]]:
    """Split body lines into ``(kind, lines)`` sections, in file order.

    The first section is the intro. Its kind is "intro". Sections with an
    unknown heading have kind "other". Stray ``# `` lines are dropped.
    """
    sections: list[tuple[str, list[_Line]]] = [("intro", [])]
    fence: str | None = None  # the opening run, for example "````"
    for number, raw in enumerate(body_lines, start=1):
        if fence is not None:
            close = _FENCE_CLOSE.match(raw)
            if (
                close
                and close.group(1)[0] == fence[0]
                and len(close.group(1)) >= len(fence)
            ):
                fence = None
            sections[-1][1].append((number, raw, "code"))
        elif opening := _FENCE_OPEN.match(raw):
            fence = opening.group(1)
            sections[-1][1].append((number, raw, "code"))
        elif h2 := _H2.match(raw):
            sections.append((_SECTIONS.get(_heading_name(h2.group(1)), "other"), []))
        elif _H3.match(raw):
            sections[-1][1].append((number, raw, "h3"))
        elif _H1.match(raw):
            continue
        else:
            sections[-1][1].append((number, raw, "text"))
    return sections


def _heading_text(text: str) -> str:
    """Heading text without closing ``#`` marks, spaces and a trailing colon."""
    return _CLOSING_HASHES.sub("", text).rstrip(" \t:")


def _heading_name(text: str) -> str:
    return _heading_text(text).casefold()


def _read_body(
    body_lines: list[str],
) -> tuple[str, list[IngredientLine], list[str], str]:
    intro_parts: list[str] = []
    ingredients: list[IngredientLine] = []
    directions: list[str] = []
    notes_parts: list[str] = []
    for kind, lines in _split_sections(body_lines):
        if kind == "intro":
            intro_parts.append(_raw_text(lines))
        elif kind == "notes":
            notes_parts.append(_raw_text(lines))
        elif kind == "directions":
            directions.extend(text for _, text, _ in _blocks(lines))
        elif kind == "ingredients":
            ingredients.extend(_read_ingredients(lines))
    return (
        "\n\n".join(p for p in intro_parts if p),
        ingredients,
        directions,
        "\n\n".join(p for p in notes_parts if p),
    )


def _raw_text(lines: list[_Line]) -> str:
    return "\n".join(raw for _, raw, _ in lines).strip()


def _read_ingredients(lines: list[_Line]) -> list[IngredientLine]:
    out: list[IngredientLine] = []
    group: str | None = None
    segment: list[_Line] = []

    def flush() -> None:
        out.extend(
            IngredientLine(text, number, group)
            for number, text, is_item in _blocks(segment)
            if is_item
        )
        segment.clear()

    for line in lines:
        heading = _H3.match(line[1]) if line[2] == "h3" else None
        if heading:
            flush()
            group = _heading_text(heading.group(1)) or None
        else:
            segment.append(line)
    flush()
    return out


def _blocks(lines: Iterable[_Line]) -> list[tuple[int, str, bool]]:
    """Group lines into list items and paragraphs.

    Returns ``(line number, text, is_list_item)``. A list item ends at a blank
    line or at the next item. Other non-blank lines that follow an item or a
    paragraph directly continue it, joined with a space. Code and heading lines
    only separate blocks.
    """
    out: list[tuple[int, str, bool]] = []
    current: tuple[int, list[str], bool] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            out.append((current[0], " ".join(current[1]), current[2]))
            current = None

    for number, raw, kind in lines:
        if kind != "text" or not raw.strip() or _THEMATIC_BREAK.match(raw):
            flush()
        elif item := _ITEM.match(raw):
            flush()
            current = (number, [item.group(1).strip()], True)
        elif current is not None:
            current[1].append(raw.strip())
        else:
            current = (number, [raw.strip()], False)
    flush()
    return out


def serialize_recipe(doc: RecipeDoc) -> str:
    """Write the recipe file: TOML front matter, then ``doc.body`` verbatim.

    Raises TypeError for a front matter value this writer cannot express,
    including a time of day with a UTC offset (TOML has none).
    """
    return f"{_FENCE}\n{_dump_toml(doc.front_matter)}{_FENCE}\n{doc.body}"


def _dump_toml(table: dict[str, Any]) -> str:
    return "".join(f"{_key(k)} = {_value(v)}\n" for k, v in table.items())


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else _string(key)


_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}


def _string(value: str) -> str:
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _value(value: Any) -> str:
    # bool before int (bool is an int); datetime before date (a subclass).
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, datetime.time) and value.tzinfo is not None:
        raise TypeError("TOML has no time of day with a UTC offset")
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return (
            "{ "
            + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items())
            + " }"
        )
    raise TypeError(f"cannot write {type(value).__name__} as a TOML value")
