# SPDX-License-Identifier: AGPL-3.0-only
"""SQL for the meal plan.

Plain functions that take a connection first.
"""

import sqlite3
from datetime import date, timedelta

from larder import db


def meal_on(
    conn: sqlite3.Connection, day: str, *, include_completed: bool = False
) -> sqlite3.Row | None:
    """Load tonight's meal and title; prefer planned rows, then the lowest id.

    By default only planned meals count. Today also shows made/skipped rows
    as confirmation after an action, including on later visits.
    """
    return conn.execute(
        "SELECT p.*, r.title FROM planned_meal p "
        "JOIN recipe r ON r.id=p.recipe_id "
        "WHERE p.date=? AND (p.status='planned' OR ?) "
        "ORDER BY (p.status='planned') DESC, p.id LIMIT 1",
        (day, include_completed),
    ).fetchone()


def finish_tonight(
    conn: sqlite3.Connection, day: date, meal_id: int, action: str
) -> tuple[date | None, bool]:
    """Finish tonight atomically; return (skip destination, status changed).

    A stale form raises ValueError. Repeated actions on a completed meal do
    nothing. An open night has no row, including no made/skipped history row.
    Roll-forward creates a new planned row in this Sunday-start week only.
    No plan-confirmed event is emitted: list regeneration waits for a plan save.
    """
    if action not in ("made", "skip"):
        raise ValueError("invalid action")
    with db.transaction(conn):
        meal = meal_on(conn, day.isoformat(), include_completed=True)
        if meal is None or meal["id"] != meal_id:
            raise ValueError("tonight's meal changed")
        if meal["status"] != "planned":
            return None, False
        conn.execute(
            "UPDATE planned_meal SET status=? WHERE id=?",
            ("made" if action == "made" else "skipped", meal_id),
        )
        if action == "skip":
            sunday = day - timedelta(days=(day.weekday() + 1) % 7)
            for offset in range(1, (sunday + timedelta(days=7) - day).days):
                destination = day + timedelta(days=offset)
                if (
                    conn.execute(
                        "SELECT 1 FROM planned_meal WHERE date=? LIMIT 1",
                        (destination.isoformat(),),
                    ).fetchone()
                    is None
                ):
                    conn.execute(
                        "INSERT INTO planned_meal (meal_plan_id,date,recipe_id) "
                        "VALUES (?,?,?)",
                        (
                            meal["meal_plan_id"],
                            destination.isoformat(),
                            meal["recipe_id"],
                        ),
                    )
                    return destination, True
    return None, True


def rotation_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Load unarchived rotation dinners, including unspecified meal types.

    Each row contains recipe_id, title, favorite and effort. Metadata is
    returned unchanged; scoring belongs to the pure ranking engine.
    """
    return conn.execute(
        "SELECT recipe.id AS recipe_id, recipe.title, "
        "recipe_meta.favorite, recipe_meta.effort "
        "FROM recipe JOIN recipe_meta ON recipe_meta.recipe_id = recipe.id "
        "WHERE recipe.archived = 0 AND recipe_meta.in_rotation = 1 "
        "AND (recipe_meta.meal_type IS NULL OR recipe_meta.meal_type = 'dinner') "
        "ORDER BY recipe.id"
    ).fetchall()


def meal_history(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Load every made or planned recipe_id and ISO date, excluding skips.

    Includes future plans and recipes no longer in rotation, without changing
    stored dates. The ranking engine applies repeat rules to these events.
    """
    return conn.execute(
        "SELECT recipe_id, date FROM planned_meal "
        "WHERE status IN ('made', 'planned') ORDER BY date, id"
    ).fetchall()


def week_plan(conn: sqlite3.Connection, week_start: str) -> sqlite3.Row | None:
    """Load one week, including its nullable confirmation timestamp."""
    return conn.execute(
        "SELECT * FROM meal_plan WHERE week_start=?", (week_start,)
    ).fetchone()


def week_meals(conn: sqlite3.Connection, week_start: str | int) -> list[sqlite3.Row]:
    """Load saved meals by ISO week start, ordered by date then id.

    Includes skipped meals and archived recipes. Integer plan ids remain
    supported for the planning session's existing callers.
    """
    column = "w.id" if isinstance(week_start, int) else "w.week_start"
    return conn.execute(
        "SELECT p.*, r.title, m.effort FROM planned_meal p "
        "JOIN meal_plan w ON w.id=p.meal_plan_id "
        "JOIN recipe r ON r.id=p.recipe_id "
        "LEFT JOIN recipe_meta m ON m.recipe_id=r.id "
        f"WHERE {column}=? ORDER BY p.date,p.id",
        (week_start,),
    ).fetchall()


def create_week(conn: sqlite3.Connection, week_start: str) -> int:
    """Insert a draft; caller owns the transaction."""
    cursor = conn.execute(
        "INSERT INTO meal_plan (week_start) VALUES (?)", (week_start,)
    )
    return cursor.lastrowid


def put_meal(conn: sqlite3.Connection, plan_id: int, day: str, recipe_id: int) -> None:
    """Set one night's pick, resetting its status and lock."""
    conn.execute(
        "DELETE FROM planned_meal WHERE meal_plan_id=? AND date=?", (plan_id, day)
    )
    conn.execute(
        "INSERT INTO planned_meal (meal_plan_id,date,recipe_id) VALUES (?,?,?)",
        (plan_id, day, recipe_id),
    )


def edit_meal(conn: sqlite3.Connection, plan_id: int, day: str, action: str) -> None:
    """Apply a validated skip, lock toggle or open-night action."""
    if action == "open":
        conn.execute(
            "DELETE FROM planned_meal WHERE meal_plan_id=? AND date=?", (plan_id, day)
        )
    elif action == "skip":
        conn.execute(
            "UPDATE planned_meal SET status='skipped' WHERE meal_plan_id=? AND date=?",
            (plan_id, day),
        )
    elif action == "lock":
        conn.execute(
            "UPDATE planned_meal SET locked=1-locked WHERE meal_plan_id=? AND date=?",
            (plan_id, day),
        )


def confirm_week(conn: sqlite3.Connection, plan_id: int, timestamp: str) -> None:
    """Mark a week confirmed within the caller's transaction."""
    conn.execute("UPDATE meal_plan SET confirmed_at=? WHERE id=?", (timestamp, plan_id))
