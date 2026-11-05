# SPDX-License-Identifier: AGPL-3.0-only
"""Member recipe browsing and admin metadata forms; no recipe file writes."""

import re

from flask import (
    Blueprint,
    Response,
    abort,
    redirect,
    render_template,
    request,
    url_for,
)
from markupsafe import Markup, escape

from larder import db, recipes_db
from larder.web import auth_views, get_db

recipes = Blueprint("recipes", __name__, url_prefix="/recipes")
MEAL_TYPES = ("dinner", "breakfast", "lunch", "side", "dessert")
EFFORTS = ("quick", "normal", "project")


def _filters() -> tuple[str | None, int | None]:
    meal_type = request.args.get("meal_type", "")
    rotation = request.args.get("in_rotation", "")
    if meal_type and meal_type not in MEAL_TYPES:
        abort(400)
    if rotation not in ("", "0", "1"):
        abort(400)
    return meal_type or None, int(rotation) if rotation else None


def _recipe(recipe_id: int):
    recipe = recipes_db.get_recipe(get_db(), recipe_id)
    if recipe is None:
        abort(404)
    return recipe


def _markdown(body: str) -> Markup:
    """Render headings, flat lists, bold and paragraphs; escape all source HTML.

    Other Markdown syntax is displayed literally. Only fixed tags added here
    are trusted; raw HTML and Markdown links never become executable markup.
    """
    blocks: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None

    def inline(text: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", str(escape(text)))

    def flush() -> None:
        nonlocal list_kind
        if paragraph:
            blocks.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()
        if list_kind:
            blocks.append(f"</{list_kind}>")
            list_kind = None

    for line in body.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        item = re.match(r"^\s*(?:([-*+])|\d+[.)])\s+(.*)$", line)
        if not line.strip():
            flush()
        elif heading:
            flush()
            level = len(heading[1])
            blocks.append(f"<h{level}>" + inline(heading[2]) + f"</h{level}>")
        elif item:
            kind = "ul" if item[1] else "ol"
            if paragraph or list_kind != kind:
                flush()
                blocks.append(f"<{kind}>")
                list_kind = kind
            blocks.append("<li>" + inline(item[2]) + "</li>")
        elif list_kind:
            # An unindented continuation is still text in the last list item.
            blocks[-1] = (
                blocks[-1].removesuffix("</li>") + " " + inline(line.strip()) + "</li>"
            )
        else:
            paragraph.append(line.strip())
    flush()
    return Markup("\n".join(blocks))


@recipes.get("")
def recipe_list() -> str:
    """Browse the active recipe mirror, read-only for household members."""
    meal_type, rotation = _filters()
    return render_template(
        "recipes/list.html",
        recipes=recipes_db.browse_recipes(get_db(), meal_type, rotation),
        meal_types=MEAL_TYPES,
        meal_type=meal_type,
        rotation=rotation,
    )


@recipes.get("/<int:recipe_id>")
def recipe_view(recipe_id: int) -> str:
    """Show escaped recipe content and household metadata."""
    recipe = _recipe(recipe_id)
    return render_template(
        "recipes/view.html", recipe=recipe, body=_markdown(recipe["body"])
    )


@auth_views.admin.route("/recipes/<int:recipe_id>/meta", methods=["GET", "POST"])
def recipe_meta(recipe_id: int) -> Response | str:
    """Edit only household metadata under the admin and CSRF guards."""
    conn = get_db()
    with db.transaction(conn):
        recipe = _recipe(recipe_id)
        if request.method == "POST":
            meal_type = request.form.get("meal_type", "")
            effort = request.form.get("effort", "")
            if meal_type and meal_type not in MEAL_TYPES:
                abort(400)
            if effort and effort not in EFFORTS:
                abort(400)
            raw_servings = request.form.get("servings", "").strip()
            try:
                servings = int(raw_servings) if raw_servings else None
            except ValueError:
                abort(400)
            if servings is not None and not 0 < servings <= 2**63 - 1:
                abort(400)
            recipes_db.update_meta(
                conn,
                recipe_id,
                meal_type or None,
                servings,
                "in_rotation" in request.form,
                "favorite" in request.form,
                effort or None,
            )
            return redirect(
                url_for("recipes.recipe_view", recipe_id=recipe_id), code=303
            )
    return render_template(
        "recipes/meta_form.html", recipe=recipe, meal_types=MEAL_TYPES, efforts=EFFORTS
    )


@auth_views.admin.post("/recipes/rotation")
def recipe_rotation() -> Response:
    """Apply submitted checkbox state only to rows displayed by this filter."""
    meal_type, rotation = _filters()
    try:
        page_ids = {int(value) for value in request.form.getlist("page_id")}
        checked_ids = {int(value) for value in request.form.getlist("in_rotation")}
    except ValueError:
        abort(400)
    conn = get_db()
    with db.transaction(conn):
        visible_ids = {
            row["id"] for row in recipes_db.browse_recipes(conn, meal_type, rotation)
        }
        if not page_ids <= visible_ids or not checked_ids <= page_ids:
            abort(400)
        recipes_db.update_rotation(conn, page_ids, checked_ids)
    return redirect(
        url_for(
            "recipes.recipe_list",
            meal_type=meal_type or "",
            in_rotation=rotation if rotation is not None else "",
        ),
        code=303,
    )
