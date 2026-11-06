# SPDX-License-Identifier: AGPL-3.0-only
"""Flask web shell: the app factory, base layout, health check and proxy trust.

``create_app`` creates the data layout, migrates the database, seeds missing
categories, items, aliases and units, and returns the app. Request connections
from ``get_db`` close at teardown.
"""

import ipaddress
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from flask import Blueprint, Flask, Response, current_app, g, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from larder import auth, db
from larder.config import IPNetwork, Settings, ensure_data_dirs
from larder.ingredients.match import seed_items
from larder.recipes.repo import register_sync
from larder.scheduler import Scheduler
from larder.shopping import generate  # noqa: F401 - register the plan subscriber

SETTINGS_KEY = "LARDER_SETTINGS"
SCHEDULER_KEY = "larder.scheduler"

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}

shell = Blueprint("shell", __name__)

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]]


def create_app(settings: Settings, *, bootstrap: bool = True) -> Flask:
    """Build the app.

    Creates the data layout, migrates the database, seeds missing categories,
    items, aliases and units, then syncs config people. When ``bootstrap`` is
    true, logs a bootstrap pairing link while no admin has a device (replacing
    an unused one). Multiple WSGI processes pass ``bootstrap=False`` and run
    ``python -m larder bootstrap`` once before the server instead.
    Raises ConfigError when config names no admin.
    """
    auth.require_admin(settings)
    ensure_data_dirs(settings)
    session_key = auth.load_session_key(settings)
    conn = db.connect(settings)
    try:
        db.migrate(conn)
        seed_items(conn)
        auth.sync_people(conn, settings)
        if bootstrap:
            auth.ensure_bootstrap(conn, settings)
    finally:
        conn.close()

    # Imported here: the views import this module for get_db and SETTINGS_KEY.
    from larder.web import (
        auth_views,
        home_views,
        list_views,
        plan_views,
        pwa_views,
        recipes_views,
        repo_views,  # noqa: F401
        review_views,  # noqa: F401
    )

    app = Flask(__name__)
    app.config[SETTINGS_KEY] = settings
    # Built, not started: the dev server starts it (``python -m larder``).
    app.extensions[SCHEDULER_KEY] = Scheduler(settings.data_dir)
    if settings.recipe_repo_url:
        register_sync(app.extensions[SCHEDULER_KEY], settings)
    app.register_blueprint(shell)
    auth_views.init_app(app, session_key)
    app.register_blueprint(recipes_views.recipes)
    app.register_blueprint(home_views.home)
    app.register_blueprint(list_views.shopping)
    app.register_blueprint(plan_views.planning)
    app.register_blueprint(pwa_views.pwa)
    app.register_error_handler(404, _not_found)
    app.after_request(_security_headers)
    app.teardown_appcontext(_close_db)
    if settings.trusted_proxies:
        app.wsgi_app = _ProxyTrust(app.wsgi_app, settings.trusted_proxies)
    return app


def get_db() -> sqlite3.Connection:
    """Return this request's database connection, opening it on first use."""
    if "db" not in g:
        g.db = db.connect(current_app.config[SETTINGS_KEY])
    conn: sqlite3.Connection = g.db
    return conn


@shell.get("/healthz")
def healthz() -> Response:
    """Report that the app is up and the highest applied migration version."""
    return jsonify(status="ok", db_version=db.schema_version(get_db()))


class _ProxyTrust:
    """WSGI middleware that applies forwarded headers only from trusted peers.

    The peer is the connecting address (REMOTE_ADDR), not a header. A peer that
    is missing or not an IP address is untrusted.
    """

    def __init__(self, app: WSGIApp, trusted: Sequence[IPNetwork]) -> None:
        self._plain = app
        self._proxied = ProxyFix(app, x_for=1, x_proto=1, x_host=1)
        self._trusted = tuple(trusted)

    def __call__(
        self, environ: dict[str, Any], start_response: Callable[..., Any]
    ) -> Iterable[bytes]:
        if _is_trusted(environ.get("REMOTE_ADDR"), self._trusted):
            return self._proxied(environ, start_response)
        return self._plain(environ, start_response)


def _is_trusted(remote_addr: str | None, trusted: Sequence[IPNetwork]) -> bool:
    if not remote_addr:
        return False
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    # A dual-stack socket reports an IPv4 peer as ::ffff:a.b.c.d. Test both
    # forms, so an IPv4 network matches it and a ::/0 entry still does.
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [address]
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        candidates.append(address.ipv4_mapped)
    return any(candidate in network for candidate in candidates for network in trusted)


def _security_headers(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


def _not_found(_error: Exception) -> tuple[str, int]:
    return render_template("404.html"), 404


def _close_db(_error: BaseException | None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()
