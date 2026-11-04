# SPDX-License-Identifier: AGPL-3.0-only
"""SQL for canonical items, seed data and parsed ingredient fields.

Functions take the caller's connection; writes run inside the caller's
``db.transaction`` so a complete seed or recipe parse is atomic.
"""

import sqlite3
from collections.abc import Iterable, Sequence

from larder.ingredients.parse import ParsedLine
from larder.ingredients.units import UNIT_DIMENSIONS, UNIT_SYNONYMS


def insert_seed(
    conn: sqlite3.Connection,
    categories: Sequence[str],
    items: Iterable[tuple[str, str, Sequence[str]]],
) -> None:
    """Insert missing names/aliases without changing any existing row."""
    for position, name in enumerate(categories):
        conn.execute(
            "INSERT INTO category (name, sort_order) VALUES (?, ?) "
            "ON CONFLICT (name) DO NOTHING",
            (name, position),
        )
    for name, category, aliases in items:
        conn.execute(
            "INSERT INTO item (canonical_name, category) VALUES (?, ?) "
            "ON CONFLICT (canonical_name) DO NOTHING",
            (name, category),
        )
        item_id = conn.execute(
            "SELECT id FROM item WHERE canonical_name = ?", (name,)
        ).fetchone()[0]
        for alias in aliases:
            conn.execute(
                "INSERT INTO item_alias (item_id, alias) VALUES (?, ?) "
                "ON CONFLICT (alias) DO NOTHING",
                (item_id, alias),
            )
    for name, aliases in UNIT_SYNONYMS.items():
        conn.execute(
            "INSERT INTO unit (name, dimension) VALUES (?, ?) "
            "ON CONFLICT (name) DO NOTHING",
            (name, UNIT_DIMENSIONS[name]),
        )
        unit_id = conn.execute(
            "SELECT id FROM unit WHERE name = ?", (name,)
        ).fetchone()[0]
        for alias in aliases:
            conn.execute(
                "INSERT INTO unit_alias (unit_id, alias) VALUES (?, ?) "
                "ON CONFLICT (alias) DO NOTHING",
                (unit_id, alias),
            )


def items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Read canonical names in deterministic tie-breaking order."""
    return conn.execute(
        "SELECT id, canonical_name FROM item ORDER BY canonical_name, id"
    ).fetchall()


def aliases(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Read persisted aliases, including those learned through confirmation."""
    return conn.execute(
        "SELECT alias, item_id FROM item_alias ORDER BY alias, id"
    ).fetchall()


def unconfirmed_lines(conn: sqlite3.Connection, recipe_id: int) -> list[sqlite3.Row]:
    """Read raw lines for one recipe, preserving confirmed parses."""
    return conn.execute(
        "SELECT id, raw_text FROM ingredient_line "
        "WHERE recipe_id = ? AND confirmed = 0 ORDER BY position, id",
        (recipe_id,),
    ).fetchall()


def unit_id(conn: sqlite3.Connection, name: str | None) -> int | None:
    """Resolve a parser's canonical unit, or None for an unknown unit."""
    if name is None:
        return None
    row = conn.execute("SELECT id FROM unit WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"canonical unit {name!r} is missing; run seed_items first")
    return int(row[0])


def write_parse(
    conn: sqlite3.Connection,
    line_id: int,
    parsed: ParsedLine,
    matched_item_id: int | None,
    confidence: float,
    alternatives: Sequence[tuple[str, int | None]],
) -> None:
    """Replace all parsed fields and ordered alternatives within a transaction."""
    conn.execute(
        "UPDATE ingredient_line SET qty = ?, qty_max = ?, unit_id = ?, "
        "item_id = ?, notes = ?, optional = ?, is_food = ?, confidence = ? "
        "WHERE id = ?",
        (
            parsed.qty,
            parsed.qty_max,
            unit_id(conn, parsed.unit),
            matched_item_id,
            parsed.notes,
            int(parsed.optional),
            int(parsed.is_food),
            confidence,
            line_id,
        ),
    )
    conn.execute("DELETE FROM ingredient_line_alt WHERE line_id = ?", (line_id,))
    conn.executemany(
        "INSERT INTO ingredient_line_alt (line_id, position, alt_text, item_id) "
        "VALUES (?, ?, ?, ?)",
        [
            (line_id, position, text, item_id)
            for position, (text, item_id) in enumerate(alternatives, 1)
        ],
    )
