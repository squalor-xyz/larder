# SPDX-License-Identifier: AGPL-3.0-only
"""Pure, conservative ingredient parsing (I1-I4, ADR 0007 and ADR 0019).

``parse_line`` preserves raw input and returns a frozen ``ParsedLine``. Quantities
are floats (or None), units are canonical spellings (or None), alternatives are
an ordered tuple, and notes are text joined with ``; ``. Empty notes are ``""``.
The first choice stays in item_text; S17 maps it and the alternatives to ids and
writes ingredient_line and ingredient_line_alt. This module never accesses a DB.

A missing quantity costs 0.2; a quantity without a recognized unit costs 0.3;
unparsed tokens cost 0.3; alternatives cost 0.1. Scores are floored at zero.
Unknown unit text remains in item_text, since it could also be food text. This
conservatively sends bare counts such as ``1 egg`` for review rather than guessing
a unit. Malformed quantities remain text; no bounds are silently reordered.
Unbalanced parentheses or a missing food name leave the whole line opaque, with
no quantity/unit and both missing-quantity and leftover penalties (score 0.5).
Callers flag confidence below units.REVIEW_THRESHOLD (0.8) for review.

Parentheses and trailing comma text are notes. An explicit ``or`` before a
parenthesis introduces alternatives, including comma-separated choices. The
marker ``(optional)`` sets optional, ignoring case and inner spaces. A leading
parenthesis after a package unit is a package note, allowing ``1 can (15 oz) corn``
to count cans. The small non-food vocabulary below matches whole words in item
text, never unit or notes. A standalone ``or`` at either end of a choice counts
as unparsed text and lowers confidence for review.
"""

import math
import re
from dataclasses import dataclass
from fractions import Fraction

from larder.ingredients.units import PACKAGE_UNITS, UNIT_ALIASES

_MISSING_QUANTITY_PENALTY = 0.2
_LEFTOVER_PENALTY = 0.3
_UNKNOWN_UNIT_PENALTY = 0.3
_ALTERNATIVES_PENALTY = 0.1

_FRACTIONS = {
    "¼": 1 / 4,
    "½": 1 / 2,
    "¾": 3 / 4,
    "⅐": 1 / 7,
    "⅑": 1 / 9,
    "⅒": 1 / 10,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅕": 1 / 5,
    "⅖": 2 / 5,
    "⅗": 3 / 5,
    "⅘": 4 / 5,
    "⅙": 1 / 6,
    "⅚": 5 / 6,
    "⅛": 1 / 8,
    "⅜": 3 / 8,
    "⅝": 5 / 8,
    "⅞": 7 / 8,
}
_UNICODE_FRACTION = "[" + "".join(_FRACTIONS) + "]"
_NUMBER = (
    rf"(?:\d+\s+\d+/\d+|\d+/\d+|\d*\.\d+|"
    rf"\d+(?:\s*{_UNICODE_FRACTION})?|{_UNICODE_FRACTION})"
)
_QUANTITY = re.compile(
    rf"^(?P<low>{_NUMBER})(?:\s*(?:[-–—]|\bto\b)\s*"
    rf"(?P<high>{_NUMBER}))?(?=\s|[a-zA-Z(]|$)"
)
_UNIT = re.compile(r"^([a-zA-Z]+)\.?(?=\s|\(|$)\s*")
_OPTIONAL = re.compile(r"\(\s*optional\s*\)", re.IGNORECASE)
_PARENTHETICAL = re.compile(r"^\(([^()]*)\)\s*")
_OR = re.compile(r"\bor\b", re.IGNORECASE)
_DANGLING_OR = re.compile(r"^or\b|\bor$", re.IGNORECASE)
_CHOICE_SEPARATOR = re.compile(r"\s*,\s*(?:or\s+)?|\s+or\s+", re.IGNORECASE)
# The owner-approved seed list; plural forms do not match substrings of food.
_NON_FOOD = re.compile(
    r"\b(?:mason\s+jars?|foil|parchment|toothpicks?|skewers?)\b", re.IGNORECASE
)
_LEFTOVER = re.compile(r"[\d()/?]|[^\w\s,'’&.\-]", re.UNICODE)


@dataclass(frozen=True)
class ParsedLine:
    raw: str
    qty: float | None
    qty_max: float | None
    unit: str | None
    item_text: str
    alternatives: tuple[str, ...]
    notes: str
    optional: bool
    is_food: bool
    confidence: float


def parse_line(raw: str) -> ParsedLine:
    """Parse one ingredient line without I/O or raising for unrecognized text."""
    optional = bool(_OPTIONAL.search(raw))
    text = _OPTIONAL.sub("", raw).strip()
    qty, qty_max, text = _quantity(text)
    unit = None
    if match := _UNIT.match(text):
        unit = UNIT_ALIASES.get(match.group(1).casefold())
        if unit is not None:
            text = text[match.end() :].strip()

    notes: list[str] = []
    if unit in PACKAGE_UNITS and (package := _PARENTHETICAL.match(text)):
        notes.append(package.group(1).strip())
        text = text[package.end() :].strip()

    # Split at the first parenthesis before looking for alternatives so that
    # an "or" inside a note cannot change the item list.
    head, paren, tail = text.partition("(")
    unmatched = re.sub(r"\([^()]*\)", "", text)
    malformed = "(" in unmatched or ")" in unmatched
    head, comma_notes = _item_and_notes(head)
    choices = tuple(part.strip() for part in _CHOICE_SEPARATOR.split(head))
    item_text, alternatives = choices[0], choices[1:]
    if malformed or not any(char.isalpha() for char in item_text):
        return ParsedLine(
            raw,
            None,
            None,
            None,
            _OPTIONAL.sub("", raw).strip(),
            (),
            "",
            optional,
            not bool(_NON_FOOD.search(item_text)),
            round(1.0 - _MISSING_QUANTITY_PENALTY - _LEFTOVER_PENALTY, 10),
        )
    if comma_notes:
        notes.append(comma_notes)
    if paren:
        notes.extend(
            part.strip(" ,") for part in re.split(r"[()]", tail) if part.strip(" ,")
        )

    unparsed = any(not choice for choice in choices) or any(
        _LEFTOVER.search(choice) or _DANGLING_OR.search(choice) for choice in choices
    )
    penalty = (
        (_MISSING_QUANTITY_PENALTY if qty is None else 0.0)
        + (_UNKNOWN_UNIT_PENALTY if qty is not None and unit is None else 0.0)
        + (_LEFTOVER_PENALTY if unparsed else 0.0)
        + (_ALTERNATIVES_PENALTY if alternatives else 0.0)
    )
    confidence = max(0.0, round(1.0 - penalty, 10))
    return ParsedLine(
        raw,
        qty,
        qty_max,
        unit,
        item_text,
        alternatives,
        "; ".join(note for note in notes if note),
        optional,
        not bool(_NON_FOOD.search(item_text)),
        confidence,
    )


def _number(text: str) -> float:
    if text[-1] in _FRACTIONS:
        return float(text[:-1].strip() or 0) + _FRACTIONS[text[-1]]
    if " " in text:
        whole, fraction = text.split(None, 1)
        return float(whole) + float(Fraction(fraction))
    return float(Fraction(text))


def _quantity(text: str) -> tuple[float | None, float | None, str]:
    match = _QUANTITY.match(text)
    if match:
        try:
            qty = _number(match.group("low"))
            qty_max = _number(match.group("high")) if match.group("high") else None
            if math.isfinite(qty) and (
                qty_max is None or (math.isfinite(qty_max) and qty_max >= qty)
            ):
                return qty, qty_max, text[match.end() :].strip()
        except (ValueError, ZeroDivisionError, OverflowError):
            pass
    return None, None, text


def _item_and_notes(text: str) -> tuple[str, str]:
    ors = list(_OR.finditer(text))
    # A comma after the final choice begins trailing notes. Earlier commas
    # separate choices when an explicit "or" is present.
    offset = ors[-1].end() if ors else 0
    comma = text.find(",", offset)
    if comma >= 0:
        return text[:comma].strip(), text[comma + 1 :].strip()
    return text.strip(), ""
