# SPDX-License-Identifier: AGPL-3.0-only
"""Web side of device pairing: the guard, CSRF, pairing pages and admin devices.

``init_app`` wires it into the app. The ``before_request`` guard maps the
device cookie to a person; anything else gets the unpaired page.
"""

from dataclasses import dataclass
from typing import Any

from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    g,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.sessions import SecureCookieSessionInterface
from markupsafe import Markup

from larder import auth
from larder.config import Settings
from larder.web import SETTINGS_KEY, get_db

DEVICE_COOKIE = "larder_device"
CSRF_SESSION_KEY = "csrf"
CSRF_FIELD = "csrf_token"
MAX_REQUEST_BYTES = 1024 * 1024
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
# Reachable without a device cookie. Unknown paths are not: an unpaired device
# learns nothing about what exists.
_OPEN_ENDPOINTS = frozenset(
    {"shell.healthz", "static", "auth.pair_page", "auth.pair_submit"}
)

pairing = Blueprint("auth", __name__)
admin = Blueprint("admin", __name__, url_prefix="/admin")


@dataclass(frozen=True, slots=True)
class CurrentPerson:
    id: int
    name: str
    role: str


class _SessionInterface(SecureCookieSessionInterface):
    """Flask's signed-cookie session, Secure whenever the request is https."""

    def get_cookie_secure(self, app: Flask) -> bool:
        return request.scheme == "https"


def init_app(app: Flask, session_key: bytes) -> None:
    """Install the session, guard, CSRF check, pages and error handlers."""
    app.secret_key = session_key
    app.session_interface = _SessionInterface()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Unpaired clients can POST to /pair/...; bound what the CSRF check parses.
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    )
    app.before_request(_guard)
    app.before_request(_check_csrf)
    app.after_request(_renew_device_cookie)
    app.context_processor(_template_context)
    app.template_filter("when")(_when)
    app.register_blueprint(pairing)
    app.register_blueprint(admin)
    app.register_error_handler(400, _bad_request)
    app.register_error_handler(403, _forbidden)


# -- guard, session and CSRF ---------------------------------------------------


def _settings() -> Settings:
    settings: Settings = current_app.config[SETTINGS_KEY]
    return settings


def _guard() -> Response | tuple[str, int] | None:
    if request.endpoint in _OPEN_ENDPOINTS:
        return None
    person = _identify()
    if person is None:
        return render_template("unpaired.html"), 401
    g.person = person
    if request.blueprint == admin.name and person.role != "admin":
        abort(403)
    return None


def _identify() -> CurrentPerson | None:
    """Map the device cookie to an active device of an active person."""
    cookie = request.cookies.get(DEVICE_COOKIE)
    if not cookie:
        return None
    conn = get_db()
    device = auth.device_for_cookie(conn, cookie)
    if device is None:
        return None
    # Roles live in config, not in the database.
    role = next(
        (p.role for p in _settings().people if p.name == device["person_name"]), None
    )
    if role is None:
        return None
    if auth.touch_device(conn, device):
        g.renew_device_cookie = cookie
    return CurrentPerson(device["person_id"], device["person_name"], role)


def _renew_device_cookie(response: Response) -> Response:
    cookie = g.pop("renew_device_cookie", None)
    if cookie is not None:
        _set_device_cookie(response, cookie)
    return response


def _set_device_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        DEVICE_COOKIE,
        token,
        max_age=auth.DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.scheme == "https",
    )


def csrf_token() -> str:
    """Return this session's CSRF token, creating it on first use."""
    token: str | None = session.get(CSRF_SESSION_KEY)
    if not token:
        token = auth.new_csrf_token()
        session[CSRF_SESSION_KEY] = token
    return token


def _check_csrf() -> None:
    if request.method in _SAFE_METHODS:
        return
    expected = session.get(CSRF_SESSION_KEY)
    supplied = request.form.get(CSRF_FIELD)
    if not expected or not supplied or not auth.tokens_match(expected, supplied):
        abort(400)


def _template_context() -> dict[str, Any]:
    return {"person": g.get("person"), "csrf_token": csrf_token}


def _when(value: str | None) -> str:
    """Show a stored UTC time as ``2026-10-13 12:00 UTC``."""
    if not value:
        return "never"
    return value.replace("T", " ").removesuffix("Z")[:16] + " UTC"


def _message(title: str, text: str, status: int) -> tuple[str, int]:
    return render_template("error.html", title=title, message=text), status


def _bad_request(_error: Exception) -> tuple[str, int]:
    return _message(
        "Request not accepted",
        "Go back, reload the page and try again.",
        400,
    )


def _forbidden(_error: Exception) -> tuple[str, int]:
    return _message("Not allowed", "Only an admin can open this page.", 403)


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


# -- pairing pages ------------------------------------------------------------


def _bad_link() -> tuple[str, int]:
    # One answer for unknown, used and expired links: no hint about which.
    return _message(
        "This link doesn't work",
        "It may have been used already or have expired. Ask an admin for a new one.",
        404,
    )


@pairing.get("/pair/<token>")
def pair_page(token: str) -> Response | tuple[str, int]:
    """Show the confirm page. A GET never uses the link up (link previews)."""
    link = auth.peek_token(get_db(), token, auth.admin_names(_settings()))
    if link is None:
        return _bad_link()
    page = render_template(
        "pair.html",
        token=token,
        bootstrap=link["kind"] == "bootstrap",
        person_name=link["person_name"],
        admins=auth.admin_names(_settings()),
    )
    return _no_store(make_response(page))


@pairing.post("/pair/<token>")
def pair_submit(token: str) -> Response | tuple[str, int]:
    """Use the link up, create the device and hand it its cookie."""
    conn = get_db()
    link = auth.peek_token(conn, token, auth.admin_names(_settings()))
    if link is None:
        return _bad_link()
    person_id = None
    if link["kind"] == "bootstrap":
        person_id = auth.bootstrap_person_id(
            conn, _settings(), request.form.get("person", "")
        )
        if person_id is None:
            abort(400)
    device_token = auth.complete_pairing(
        conn,
        token,
        request.form.get("label", ""),
        person_id,
        auth.admin_names(_settings()),
    )
    if device_token is None:
        return _bad_link()
    response = redirect("/", code=303)
    _set_device_cookie(response, device_token)
    return _no_store(response)


# -- admin devices ------------------------------------------------------------


def _devices_page(**extra: Any) -> Response:
    page = render_template(
        "admin/devices.html",
        devices=auth.list_devices(get_db()),
        people=[person.name for person in _settings().people],
        **extra,
    )
    return _no_store(make_response(page))


@admin.get("/devices")
def devices() -> Response:
    return _devices_page()


@admin.post("/devices/pair")
def create_pairing_link() -> Response:
    """Create a pairing link for a person and show it with a QR code."""
    name = request.form.get("person", "")
    try:
        token = auth.create_pair_token(get_db(), name)
    except LookupError:
        abort(400)
    link = url_for("auth.pair_page", token=token, _external=True)
    return _devices_page(
        new_link=link, new_person=name, new_qr=Markup(auth.qr_svg(link))
    )


@admin.post("/devices/<int:device_id>/rename")
def rename(device_id: int) -> Response:
    label = auth.clean_label(request.form.get("label", ""))
    if not label:
        abort(400)
    if not auth.rename_device(get_db(), device_id, label):
        abort(404)
    return redirect(url_for("admin.devices"), code=303)


@admin.post("/devices/<int:device_id>/revoke")
def revoke(device_id: int) -> Response:
    if not auth.revoke_device(get_db(), device_id):
        abort(404)
    return redirect(url_for("admin.devices"), code=303)
