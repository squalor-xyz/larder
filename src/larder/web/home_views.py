# SPDX-License-Identifier: AGPL-3.0-only
"""Tonight's dinner and one-tap status controls for paired devices."""

import datetime

from flask import Blueprint, abort, current_app, render_template, request

from larder import planner_db
from larder.config import Settings
from larder.web import SETTINGS_KEY, get_db

home = Blueprint("home", __name__)


def today(settings: Settings) -> datetime.date:
    """Return today's date in the household timezone (host local time if unset)."""
    return datetime.datetime.now(settings.timezone).date()


@home.route("/", methods=["GET", "POST"])
def index() -> str:
    """Show tonight's dinner and record made/skipped without regenerating lists."""
    settings: Settings = current_app.config[SETTINGS_KEY]
    day = today(settings)
    conn = get_db()
    moved_to = None
    action = None
    if request.method == "POST":
        action = request.form.get("action")
        try:
            meal_id = int(request.form.get("meal_id", ""))
        except ValueError:
            abort(400)
        if action not in ("made", "skip") or not 0 < meal_id <= 2**63 - 1:
            abort(400)
        try:
            moved_to, changed = planner_db.finish_tonight(conn, day, meal_id, action)
            if not changed:
                action = None
        except ValueError:
            abort(409)
    meal = planner_db.meal_on(conn, day.isoformat(), include_completed=True)
    return render_template("home.html", meal=meal, moved_to=moved_to, action=action)
