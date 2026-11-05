# SPDX-License-Identifier: AGPL-3.0-only
"""One-page weekly planning for every paired household member."""

from datetime import date, timedelta

from flask import (
    Blueprint,
    Response,
    abort,
    redirect,
    render_template,
    request,
    url_for,
)

from larder import planner_db, recipes_db
from larder.planner import plan
from larder.web import get_db

planning = Blueprint("planning", __name__)
today = date.today


def _date(raw: str) -> date:
    parsed = date.fromisoformat(raw)
    if parsed.isoformat() != raw:
        raise ValueError("expected ISO date")
    return parsed


def _seen(raw: str) -> list[int]:
    return [int(value) for value in raw.split(",") if value]


@planning.route("/plan", methods=["GET", "POST"])
def week() -> Response | str:
    """Review, edit and confirm a Sunday-starting week."""
    try:
        now = today()
        week_start = (
            _date(request.args["week"])
            if "week" in request.args
            else now + timedelta(days=(6 - now.weekday()) % 7)
        )
        if week_start.weekday() != 6:
            raise ValueError("week must start Sunday")
        read_only = week_start + timedelta(days=6) < now
        if read_only and request.method == "POST":
            abort(400)
        days = [week_start + timedelta(days=n) for n in range(7)]
        shown = {
            day.isoformat(): _seen(request.args.get("seen_" + day.isoformat(), ""))
            for day in days
        }
        conn = get_db()
        if request.method == "POST":
            day = _date(request.form.get("day", week_start.isoformat()))
            seen = _seen(request.form.get("seen", ""))
            raw_recipe = request.form.get("recipe_id", "")
            recipe_id = int(raw_recipe) if raw_recipe else None
            if recipe_id is not None and not 0 < recipe_id <= 2**63 - 1:
                raise ValueError("invalid recipe id")
            replacement = plan.save(
                conn,
                week_start,
                request.form.get("action", ""),
                day,
                recipe_id=recipe_id,
                seen=seen,
            )
            if replacement is not None:
                shown[day.isoformat()] = list(dict.fromkeys([*seen, replacement]))
            params = {
                "seen_" + key: ",".join(map(str, ids))
                for key, ids in shown.items()
                if ids
            }
            return redirect(
                url_for("planning.week", week=week_start.isoformat(), **params),
                code=303,
            )
        if read_only:
            saved = planner_db.week_plan(conn, week_start.isoformat())
            meals = planner_db.week_meals(conn, saved["id"]) if saved else []
        else:
            saved, meals = plan.load_week(conn, week_start)
    except (ValueError, OverflowError):
        abort(400)
    by_day = {row["date"]: row for row in meals}
    for day, row in by_day.items():
        if row["recipe_id"] not in shown[day]:
            shown[day].append(row["recipe_id"])
    return render_template(
        "plan/week.html",
        week_start=week_start,
        read_only=read_only,
        days=days,
        saved=saved,
        meals=by_day,
        shown=shown,
        recipes=recipes_db.browse_recipes(conn),
    )
