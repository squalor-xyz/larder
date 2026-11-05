# SPDX-License-Identifier: AGPL-3.0-only
"""SQL for canonical items, seed data and parsed ingredient fields.

Functions take the caller's connection; writes run inside the caller's
``db.transaction`` so a complete seed or recipe parse is atomic.
"""

import sqlite3
from collections.abc import Iterable, Sequence

from larder.ingredients.parse import ParsedLine
from larder.ingredients.units import REVIEW_THRESHOLD, UNIT_DIMENSIONS, UNIT_SYNONYMS


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


def review_lines(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unresolved lines from active recipes, with the first candidate only."""
    return conn.execute(
        "SELECT l.*, r.title, i.canonical_name, u.name AS unit "
        "FROM ingredient_line l JOIN recipe r ON r.id=l.recipe_id "
        "LEFT JOIN item i ON i.id=l.item_id LEFT JOIN unit u ON u.id=l.unit_id "
        "WHERE r.archived=0 AND l.confirmed=0 "
        "AND (l.confidence < ? OR (l.is_food=1 AND l.item_id IS NULL)) "
        "ORDER BY r.title, r.id, l.position, l.id",
        (REVIEW_THRESHOLD,),
    ).fetchall()


def active_unconfirmed_lines(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return potential duplicate lines without excluding confident suggestions."""
    return conn.execute(
        "SELECT l.id,l.raw_text FROM ingredient_line l "
        "JOIN recipe r ON r.id=l.recipe_id "
        "WHERE l.confirmed=0 AND r.archived=0 ORDER BY l.id"
    ).fetchall()


def review_line(conn: sqlite3.Connection, line_id: int) -> sqlite3.Row | None:
    """Return an active unconfirmed line for an answer, or None if stale."""
    return conn.execute(
        "SELECT l.*,u.name AS unit FROM ingredient_line l "
        "JOIN recipe r ON r.id=l.recipe_id LEFT JOIN unit u ON u.id=l.unit_id "
        "WHERE l.id=? AND l.confirmed=0 AND r.archived=0",
        (line_id,),
    ).fetchone()


def review_units(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Canonical units available in the edit form."""
    return conn.execute("SELECT name FROM unit ORDER BY name").fetchall()


def create_review_item(conn: sqlite3.Connection, name: str) -> int:
    """Create a canonical item or reuse the identical existing name."""
    conn.execute(
        "INSERT INTO item (canonical_name) VALUES (?) ON CONFLICT (canonical_name) DO NOTHING",
        (name,),
    )
    return conn.execute(
        "SELECT id FROM item WHERE canonical_name=?", (name,)
    ).fetchone()[0]


def save_override(
    conn: sqlite3.Connection,
    line_id: int,
    normalized: str,
    parsed: ParsedLine,
    item_id: int | None,
    alias: str,
) -> None:
    """Persist an answer and copy alternatives verbatim; caller owns transaction."""
    conn.execute(
        "INSERT INTO parse_override (normalized_text,qty,qty_max,unit_id,item_id,notes,optional,is_food) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (normalized_text) DO UPDATE SET "
        "qty=excluded.qty,qty_max=excluded.qty_max,unit_id=excluded.unit_id,"
        "item_id=excluded.item_id,notes=excluded.notes,optional=excluded.optional,is_food=excluded.is_food",
        (
            normalized,
            parsed.qty,
            parsed.qty_max,
            unit_id(conn, parsed.unit),
            item_id,
            parsed.notes,
            int(parsed.optional),
            int(parsed.is_food),
        ),
    )
    override_id = conn.execute(
        "SELECT id FROM parse_override WHERE normalized_text=?", (normalized,)
    ).fetchone()[0]
    conn.execute("DELETE FROM parse_override_alt WHERE override_id=?", (override_id,))
    conn.execute(
        "INSERT INTO parse_override_alt (override_id,position,alt_text,item_id) "
        "SELECT ?,position,alt_text,item_id FROM ingredient_line_alt WHERE line_id=?",
        (override_id, line_id),
    )
    if item_id is not None and parsed.is_food and alias:
        conn.execute(
            "INSERT INTO item_alias (alias,item_id) VALUES (?,?) "
            "ON CONFLICT (alias) DO UPDATE SET item_id=excluded.item_id",
            (alias, item_id),
        )


def apply_override(conn: sqlite3.Connection, line_id: int, normalized: str) -> bool:
    """Copy a permanent answer and alternatives onto one line, if present."""
    override = conn.execute(
        "SELECT * FROM parse_override WHERE normalized_text=?", (normalized,)
    ).fetchone()
    if override is None:
        return False
    conn.execute(
        "UPDATE ingredient_line SET qty=?,qty_max=?,unit_id=?,item_id=?,notes=?,optional=?,"
        "is_food=?,confidence=1,confirmed=1 WHERE id=?",
        (
            override["qty"],
            override["qty_max"],
            override["unit_id"],
            override["item_id"],
            override["notes"],
            override["optional"],
            override["is_food"],
            line_id,
        ),
    )
    conn.execute("DELETE FROM ingredient_line_alt WHERE line_id=?", (line_id,))
    conn.execute(
        "INSERT INTO ingredient_line_alt (line_id,position,alt_text,item_id) "
        "SELECT ?,position,alt_text,item_id FROM parse_override_alt WHERE override_id=?",
        (line_id, override["id"]),
    )
    return True
