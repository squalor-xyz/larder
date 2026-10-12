# SPDX-License-Identifier: AGPL-3.0-only
"""Flask web shell: the app factory, base layout, health check and proxy trust.

``create_app`` creates the data layout, migrates the database once and returns
the app. Each request opens its own connection through ``get_db``, which closes
it at teardown.
"""

import ipaddress
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from flask import Blueprint, Flask, Response, current_app, g, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from larder import db
from larder.config import IPNetwork, Settings, ensure_data_dirs

SETTINGS_KEY = "LARDER_SETTINGS"

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


def create_app(settings: Settings) -> Flask:
    """Build the app. Creates the data layout and migrates the database first."""
    ensure_data_dirs(settings)
    conn = db.connect(settings)
    try:
        db.migrate(conn)
    finally:
        conn.close()

    app = Flask(__name__)
    app.config[SETTINGS_KEY] = settings
    app.register_blueprint(shell)
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
    return any(address in network for network in trusted)


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
