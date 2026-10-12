# SPDX-License-Identifier: AGPL-3.0-only
import contextlib
import io
import os
import sqlite3
import tempfile
import tomllib
import unittest
from importlib import resources
from pathlib import Path
from unittest import mock

from flask import Flask, Response, jsonify, request
from werkzeug.test import TestResponse

from larder import db
from larder.__main__ import main
from larder.config import Settings, load_settings
from larder.db import DatabaseError
from larder.web import create_app, get_db

REPO_ROOT = Path(__file__).resolve().parent.parent

CSP = (
    "default-src 'self'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'; object-src 'none'"
)
PROBE_PATH = "/_probe"
FORWARDED = {
    "X-Forwarded-For": "203.0.113.9",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "larder.example",
}


def shipped_schema_version() -> int:
    migrations = resources.files("larder") / "migrations"
    return max(
        int(entry.name[:4])
        for entry in migrations.iterdir()
        if entry.name.endswith(".sql")
    )


def add_probe(app: Flask) -> None:
    """Register a route that reports what the request says about its peer."""

    def probe() -> Response:
        return jsonify(
            remote_addr=request.remote_addr,
            scheme=request.scheme,
            host=request.host,
        )

    app.add_url_rule(PROBE_PATH, "probe", probe)


class WebShellTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)

    def write_config(self, text: str) -> None:
        (self.data_dir / "larder.toml").write_text(text, encoding="utf-8")

    def settings(self) -> Settings:
        return load_settings({"LARDER_DATA": str(self.data_dir)})

    @staticmethod
    def get(
        app: Flask,
        path: str,
        remote_addr: str = "127.0.0.1",
        headers: dict[str, str] | None = None,
    ) -> TestResponse:
        return app.test_client().get(
            path, headers=headers, environ_base={"REMOTE_ADDR": remote_addr}
        )


class HealthTest(WebShellTestCase):
    def test_health_returns_ok_and_db_version(self) -> None:
        app = create_app(self.settings())

        response = self.get(app, "/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"status": "ok", "db_version": shipped_schema_version()},
        )


class AppFactoryTest(WebShellTestCase):
    def test_create_app_migrates_database(self) -> None:
        settings = self.settings()

        create_app(settings)

        conn = db.connect(settings)
        self.addCleanup(conn.close)
        # Nothing is left to apply: create_app already did it.
        self.assertEqual(db.migrate(conn), [])

    def test_db_connection_closed_at_teardown(self) -> None:
        app = create_app(self.settings())

        with app.app_context():
            conn = get_db()
            self.assertIs(get_db(), conn)

        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


class ErrorPageTest(WebShellTestCase):
    def test_unknown_route_404_uses_base_template(self) -> None:
        app = create_app(self.settings())

        response = self.get(app, "/no-such-page")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.mimetype, "text/html")
        body = response.get_data(as_text=True)
        self.assertIn('<html lang="en">', body)
        self.assertIn('name="viewport"', body)
        self.assertIn("<header", body)
        self.assertIn("<main>", body)
        self.assertIn("<h1>Page not found</h1>", body)
        self.assertIn("larder.css", body)

    def test_404_has_security_headers(self) -> None:
        app = create_app(self.settings())

        response = self.get(app, "/no-such-page")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("Content-Security-Policy"), CSP)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

    def test_stylesheet_is_served(self) -> None:
        app = create_app(self.settings())

        response = self.get(app, "/static/larder.css")
        self.addCleanup(response.close)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/css")


class SecurityHeadersTest(WebShellTestCase):
    def test_security_headers_present(self) -> None:
        app = create_app(self.settings())

        response = self.get(app, "/healthz")

        self.assertEqual(response.headers.get("Content-Security-Policy"), CSP)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("Referrer-Policy"), "same-origin")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")


class ProxyHeadersTest(WebShellTestCase):
    def test_proxy_headers_ignored_unless_trusted(self) -> None:
        # No trusted_proxies in the config: forwarded headers never apply.
        app = create_app(self.settings())
        add_probe(app)

        response = self.get(app, PROBE_PATH, "10.1.2.3", FORWARDED)

        self.assertEqual(
            response.get_json(),
            {"remote_addr": "10.1.2.3", "scheme": "http", "host": "localhost"},
        )

    def test_proxy_headers_ignored_from_untrusted_peer_when_list_set(self) -> None:
        self.write_config('trusted_proxies = ["10.0.0.0/8"]\n')
        app = create_app(self.settings())
        add_probe(app)

        response = self.get(app, PROBE_PATH, "192.0.2.7", FORWARDED)

        self.assertEqual(
            response.get_json(),
            {"remote_addr": "192.0.2.7", "scheme": "http", "host": "localhost"},
        )

    def test_proxy_headers_applied_when_trusted(self) -> None:
        self.write_config('trusted_proxies = ["10.0.0.0/8"]\n')
        app = create_app(self.settings())
        add_probe(app)

        response = self.get(app, PROBE_PATH, "10.1.2.3", FORWARDED)

        self.assertEqual(
            response.get_json(),
            {
                "remote_addr": "203.0.113.9",
                "scheme": "https",
                "host": "larder.example",
            },
        )

    def test_proxy_headers_ignored_when_peer_address_is_not_an_ip(self) -> None:
        self.write_config('trusted_proxies = ["0.0.0.0/0", "::/0"]\n')
        app = create_app(self.settings())
        add_probe(app)

        for peer in ("not-an-ip", ""):
            with self.subTest(peer=peer):
                response = self.get(app, PROBE_PATH, peer, FORWARDED)

                self.assertEqual(
                    response.get_json(),
                    {"remote_addr": peer, "scheme": "http", "host": "localhost"},
                )


class MainTest(WebShellTestCase):
    @staticmethod
    def run_main(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(argv)
        return code, stderr.getvalue()

    def test_main_reports_config_error(self) -> None:
        code, err = self.run_main([], {})

        self.assertEqual(code, 2)
        self.assertIn("LARDER_DATA", err)
        self.assertNotIn("Traceback", err)

    def test_main_reports_database_error(self) -> None:
        env = {"LARDER_DATA": str(self.data_dir)}
        with mock.patch(
            "larder.__main__.create_app", side_effect=DatabaseError("boom")
        ):
            code, err = self.run_main([], env)

        self.assertEqual(code, 2)
        self.assertIn("boom", err)
        self.assertNotIn("Traceback", err)

    def test_main_runs_dev_server_on_requested_address(self) -> None:
        env = {"LARDER_DATA": str(self.data_dir)}
        with mock.patch("flask.Flask.run") as run:
            code, _ = self.run_main([], env)
            self.assertEqual(code, 0)
            run.assert_called_once_with(host="127.0.0.1", port=8000, debug=False)

            run.reset_mock()
            code, _ = self.run_main(["--host", "0.0.0.0", "--port", "9000"], env)
            self.assertEqual(code, 0)
            run.assert_called_once_with(host="0.0.0.0", port=9000, debug=False)


class TemplateRulesTest(unittest.TestCase):
    def test_templates_have_no_inline_script_or_style(self) -> None:
        templates = resources.files("larder.web") / "templates"
        names = {entry.name for entry in templates.iterdir()}
        self.assertTrue({"base.html", "404.html"} <= names)

        for entry in templates.iterdir():
            text = entry.read_text(encoding="utf-8")
            lowered = text.lower()
            with self.subTest(template=entry.name):
                self.assertNotIn("<script", lowered)
                self.assertNotIn("<style", lowered)
                self.assertNotIn(" style=", lowered)
                self.assertNotRegex(lowered, r"\son\w+\s*=")


class PackageDataTest(unittest.TestCase):
    def test_templates_and_static_ship_as_package_data(self) -> None:
        pyproject = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        patterns = pyproject["tool"]["setuptools"]["package-data"]["larder"]
        self.assertIn("web/templates/*.html", patterns)
        self.assertIn("web/static/*", patterns)

        package_root = REPO_ROOT / "src" / "larder"
        for pattern in ("web/templates/*.html", "web/static/*"):
            with self.subTest(pattern=pattern):
                self.assertTrue(list(package_root.glob(pattern)))


if __name__ == "__main__":
    unittest.main()
