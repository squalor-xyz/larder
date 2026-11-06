# SPDX-License-Identifier: AGPL-3.0-only
"""Shared shopping forms and content-versioned fragments for 5-second polling."""

import hashlib
import json
from datetime import UTC, datetime

from flask import (
    Blueprint,
    Response,
    abort,
    g,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from larder import db, ingredients_db, shopping_db
from larder.web import get_db

shopping = Blueprint("shopping", __name__)


def _id(raw: str) -> int:
    value = int(raw)
    if not 0 < value <= 2**63 - 1:
        raise ValueError("invalid id")
    return value


@shopping.route("/list", methods=["GET", "POST"])
def index() -> Response:
    """Render the shared list or apply one CSRF-protected form action."""
    conn = get_db()
    timestamp = datetime.now(UTC).isoformat()
    with db.transaction(conn):
        list_id = shopping_db.active_list(conn, timestamp)
        if request.method == "POST":
            try:
                action = request.form.get("action")
                if (
                    action in {"check", "remove", "clear", "ordered"}
                    and _id(request.form.get("list_id", "")) != list_id
                ):
                    abort(409)
                if action == "add":
                    text = request.form.get("text", "").strip()
                    if not text or len(text) > 500:
                        raise ValueError("empty or long item")
                    item_id = next(
                        (
                            r["id"]
                            for r in ingredients_db.items(conn)
                            if r["canonical_name"].casefold() == text.casefold()
                        ),
                        None,
                    )
                    shopping_db.add_manual_item(
                        conn,
                        list_id,
                        item_id,
                        None if item_id else text,
                        None,
                        None,
                        "manual",
                        g.person.id,
                        timestamp,
                    )
                elif action == "staple":
                    staple_id = _id(request.form.get("staple_id", ""))
                    staple = next(
                        (r for r in shopping_db.staples(conn) if r["id"] == staple_id),
                        None,
                    )
                    if staple is None:
                        abort(409)
                    shopping_db.add_manual_item(
                        conn,
                        list_id,
                        staple["item_id"],
                        None,
                        staple["default_qty"],
                        staple["unit_id"],
                        "staple",
                        g.person.id,
                        timestamp,
                    )
                elif action in {"check", "remove"}:
                    row_id = _id(request.form.get("row_id", ""))
                    checked = None
                    if action == "check":
                        raw = request.form.get("checked")
                        if raw not in {"0", "1"}:
                            raise ValueError("invalid checked state")
                        checked = int(raw)
                    if not shopping_db.change_item(conn, list_id, row_id, checked):
                        abort(409)
                elif action == "clear":
                    shopping_db.clear_checked(conn, list_id)
                elif action == "ordered":
                    shopping_db.archive_list(conn, list_id, timestamp)
                else:
                    raise ValueError("unknown action")
            except ValueError:
                abort(400)
            return redirect(url_for("shopping.index"), code=303)
        context = _context(list_id)
    return _response(
        render_template(
            "list/index.html", catalog=ingredients_db.items(conn), **context
        )
    )


def _context(list_id: int) -> dict:
    rows = [dict(r) for r in shopping_db.list_items(get_db(), list_id)]
    staples = [dict(r) for r in shopping_db.staples(get_db())]
    version = hashlib.sha256(
        json.dumps([list_id, rows, staples], sort_keys=True).encode()
    ).hexdigest()
    groups = {}
    for row in rows:
        groups.setdefault(row["category"] or "Other", []).append(row)
    return {
        "list_id": list_id,
        "groups": groups,
        "staples": staples,
        "version": version,
    }


def _response(body: str, status: int = 200) -> Response:
    response = make_response(body, status)
    response.headers["Cache-Control"] = "no-store"
    return response


@shopping.get("/list/refresh")
def refresh() -> Response:
    """Return 204 for equal content, otherwise the replacement fragment."""
    with db.transaction(get_db()):
        list_id = shopping_db.active_list(get_db(), datetime.now(UTC).isoformat())
        context = _context(list_id)
    if request.args.get("version") == context["version"]:
        return _response("", 204)
    return _response(render_template("list/_items.html", **context))
