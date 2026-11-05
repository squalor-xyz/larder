# SPDX-License-Identifier: AGPL-3.0-only
"""SQL for the meal plan.

Plain functions that take a connection first.
"""

import sqlite3


def meal_on(conn: sqlite3.Connection, day: str) -> sqlite3.Row | None:
    """Return the planned meal on the ISO date ``day`` with its recipe title.

    Only meals with status ``planned`` count. With several, the lowest id wins.
    Returns None when there is none.
    """
    row: sqlite3.Row | None = conn.execute(
        "SELECT planned_meal.id, planned_meal.date, planned_meal.recipe_id, "
        "recipe.title FROM planned_meal "
        "JOIN recipe ON recipe.id = planned_meal.recipe_id "
        "WHERE planned_meal.date = ? AND planned_meal.status = 'planned' "
        "ORDER BY planned_meal.id LIMIT 1",
        (day,),
    ).fetchone()
    return row


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


def week_meals(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    """Load persisted meals, including skipped and archived recipes."""
    return conn.execute(
        "SELECT p.*, r.title, m.effort FROM planned_meal p "
        "JOIN recipe r ON r.id=p.recipe_id "
        "LEFT JOIN recipe_meta m ON m.recipe_id=r.id "
        "WHERE p.meal_plan_id=? ORDER BY p.date,p.id",
        (plan_id,),
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
