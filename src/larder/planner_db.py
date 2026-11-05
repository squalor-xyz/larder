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
