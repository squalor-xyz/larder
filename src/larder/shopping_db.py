# SPDX-License-Identifier: AGPL-3.0-only
"""Shopping SQL; write helpers run inside the caller's transaction."""

import sqlite3
from collections.abc import Iterable


def plan_lines(conn: sqlite3.Connection, today: str) -> list[sqlite3.Row]:
    """One ingredient occurrence per planned meal in confirmed, unended weeks.

    Alternatives remain child rows: only the first choice on the parent feeds
    the list. Repeated nights of the same recipe each contribute quantities.
    """
    return conn.execute(
        "SELECT i.*, u.name AS unit_name, "
        "EXISTS (SELECT 1 FROM staple s WHERE s.item_id=i.item_id) AS is_staple "
        "FROM meal_plan p "
        "JOIN planned_meal m ON m.meal_plan_id=p.id "
        "JOIN ingredient_line i ON i.recipe_id=m.recipe_id "
        "LEFT JOIN unit u ON u.id=i.unit_id "
        "WHERE p.confirmed_at IS NOT NULL AND date(p.week_start,'+6 days')>=? "
        "AND m.status='planned' AND i.optional=0 AND i.is_food=1 "
        "ORDER BY p.week_start,m.id,i.position,i.id",
        (today,),
    ).fetchall()


def active_list(conn: sqlite3.Connection, timestamp: str) -> int:
    """Find or create the sole active list under the caller's write lock."""
    row = conn.execute(
        "SELECT id FROM shopping_list WHERE archived_at IS NULL"
    ).fetchone()
    if row is not None:
        return row["id"]
    return conn.execute(
        "INSERT INTO shopping_list (created_at) VALUES (?)", (timestamp,)
    ).lastrowid


def plan_items(conn: sqlite3.Connection, list_id: int) -> list[sqlite3.Row]:
    """Read the previous generated quantities and check states."""
    return conn.execute(
        "SELECT l.*, u.name AS unit_name FROM list_item l "
        "LEFT JOIN unit u ON u.id=l.unit_id WHERE list_id=? AND source='plan'",
        (list_id,),
    ).fetchall()


def remove_plan_items(conn: sqlite3.Connection, list_id: int) -> None:
    """Replace only generated items; recipe links cascade."""
    conn.execute("DELETE FROM list_item WHERE list_id=? AND source='plan'", (list_id,))


def add_plan_item(
    conn: sqlite3.Connection,
    list_id: int,
    item_id: int | None,
    free_text: str | None,
    qty: float | None,
    unit_id: int | None,
    checked: bool,
    timestamp: str,
    recipes: Iterable[int],
) -> None:
    """Persist one generated line and its distinct source recipes."""
    item = conn.execute(
        "INSERT INTO list_item (list_id,item_id,free_text,qty,unit_id,source,checked,added_at) "
        "VALUES (?,?,?,?,?,'plan',?,?)",
        (list_id, item_id, free_text, qty, unit_id, checked, timestamp),
    ).lastrowid
    conn.executemany(
        "INSERT INTO list_item_recipe (list_item_id,recipe_id) VALUES (?,?)",
        [(item, recipe) for recipe in sorted(set(recipes))],
    )


def list_items(conn: sqlite3.Connection, list_id: int) -> list[sqlite3.Row]:
    """Read list rows in configured category order, with free text last."""
    return conn.execute(
        "SELECT l.*, i.canonical_name, i.category, u.name AS unit_name, p.name AS added_by_name "
        "FROM list_item l LEFT JOIN person p ON p.id=l.added_by LEFT JOIN item i ON i.id=l.item_id "
        "LEFT JOIN category c ON c.name=i.category "
        "LEFT JOIN unit u ON u.id=l.unit_id WHERE l.list_id=? "
        "ORDER BY (l.item_id IS NULL), (c.sort_order IS NULL), c.sort_order, "
        "COALESCE(i.canonical_name,l.free_text), l.id",
        (list_id,),
    ).fetchall()


def staples(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Existing staples with their display names and default quantities."""
    return conn.execute(
        "SELECT s.*, i.canonical_name, u.name AS unit_name FROM staple s "
        "JOIN item i ON i.id=s.item_id LEFT JOIN unit u ON u.id=s.unit_id "
        "ORDER BY i.canonical_name,s.id"
    ).fetchall()


def add_manual_item(
    conn: sqlite3.Connection,
    list_id: int,
    item_id: int | None,
    text: str | None,
    qty: float | None,
    unit_id: int | None,
    source: str,
    person_id: int,
    timestamp: str,
) -> None:
    """Add a manual or staple row under the caller's write lock."""
    conn.execute(
        "INSERT INTO list_item (list_id,item_id,free_text,qty,unit_id,source,added_by,added_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (list_id, item_id, text, qty, unit_id, source, person_id, timestamp),
    )


def change_item(
    conn: sqlite3.Connection, list_id: int, row_id: int, checked: int | None
) -> bool:
    """Set explicit check state, or remove a row, only from the active list."""
    if checked is None:
        cursor = conn.execute(
            "DELETE FROM list_item WHERE list_id=? AND id=?", (list_id, row_id)
        )
    else:
        cursor = conn.execute(
            "UPDATE list_item SET checked=? WHERE list_id=? AND id=?",
            (checked, list_id, row_id),
        )
    return cursor.rowcount == 1


def clear_checked(conn: sqlite3.Connection, list_id: int) -> None:
    """Remove checked rows only."""
    conn.execute("DELETE FROM list_item WHERE list_id=? AND checked=1", (list_id,))


def archive_list(conn: sqlite3.Connection, list_id: int, timestamp: str) -> None:
    """Archive every row together and start a new empty active list."""
    conn.execute(
        "UPDATE shopping_list SET archived_at=? WHERE id=?", (timestamp, list_id)
    )
    active_list(conn, timestamp)
