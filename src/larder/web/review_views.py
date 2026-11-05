# SPDX-License-Identifier: AGPL-3.0-only
"""Admin review queue; import before registering the shared admin blueprint."""

from flask import Response, abort, g, redirect, render_template, request, url_for

from larder import ingredients_db
from larder.ingredients import overrides
from larder.web import auth_views, get_db


@auth_views.admin.app_context_processor
def review_context() -> dict[str, int]:
    """Expose the unresolved count in admin navigation only."""
    if g.get("person") is None or g.person.role != "admin":
        return {}
    return {"review_count": len(ingredients_db.review_lines(get_db()))}


@auth_views.admin.get("/review")
def review_queue() -> str:
    """Show one-tap confirmations and on-demand full edit forms."""
    conn = get_db()
    return render_template(
        "review/queue.html",
        lines=ingredients_db.review_lines(conn),
        items=ingredients_db.items(conn),
        units=ingredients_db.review_units(conn),
    )


@auth_views.admin.post("/review/<int:line_id>")
def review_answer(line_id: int) -> Response:
    """Accept an answer under the existing admin guard and CSRF protection."""
    try:
        overrides.answer(
            get_db(), line_id, request.form.get("action", ""), request.form
        )
    except LookupError:
        abort(404)
    except ValueError:
        abort(400)
    return redirect(url_for("admin.review_queue"), code=303)
