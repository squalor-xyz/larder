# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only current week for every paired household member."""

from datetime import timedelta

from flask import Blueprint, current_app, render_template

from larder import planner_db
from larder.web import SETTINGS_KEY, get_db, home_views

week = Blueprint("week", __name__)


@week.get("/week")
def index() -> str:
    """Show all saved meals in today's Sunday-starting week without edits."""
    day = home_views.today(current_app.config[SETTINGS_KEY])
    start = day - timedelta(days=(day.weekday() + 1) % 7)
    days = [(start + timedelta(days=n)).isoformat() for n in range(7)]
    conn = get_db()
    saved = planner_db.week_plan(conn, start.isoformat())
    meals = {day: [] for day in days}
    for meal in planner_db.week_meals(conn, start.isoformat()):
        if meal["date"] in meals:
            meals[meal["date"]].append(meal)
    return render_template(
        "week.html", week_start=start.isoformat(), days=days, saved=saved, meals=meals
    )
