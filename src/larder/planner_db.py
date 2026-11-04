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
