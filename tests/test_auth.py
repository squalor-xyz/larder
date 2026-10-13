# SPDX-License-Identifier: AGPL-3.0-only
import contextlib
import io
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from datetime import timedelta
from unittest import mock

from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from larder import auth, db
from larder.__main__ import main
from larder.config import ConfigError
from larder.web import create_app
from tests.test_web_shell import (
    CSP,
    CSRF_FIELD,
    DEVICE_COOKIE,
    WebShellTestCase,
)

PAIR_PATH = re.compile(r"/pair/([A-Za-z0-9_-]+)")
TEN_MINUTES = 600
ONE_YEAR = 365 * 24 * 3600
TWO_ADMINS = (
    '[[people]]\nname = "Ada"\nrole = "admin"\n\n'
    '[[people]]\nname = "Bea"\nrole = "admin"\n\n'
    '[[people]]\nname = "Sam"\nrole = "member"\n'
)
ONLY_ADA_AND_ZED = (
    '[[people]]\nname = "Ada"\nrole = "admin"\n\n'
    '[[people]]\nname = "Zed"\nrole = "member"\n'
)
FORWARDED_HTTPS = {
    "X-Forwarded-For": "203.0.113.9",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "larder.example",
}


class AuthTestCase(WebShellTestCase):
    def app(self) -> Flask:
        return create_app(self.settings())

    def app_logging(self) -> tuple[Flask, str]:
        """Start the app and return it with the bootstrap token it logged."""
        with self.assertLogs("larder.auth", "WARNING") as logs:
            app = self.app()
        found = PAIR_PATH.search("\n".join(logs.output))
        assert found is not None, logs.output
        return app, found.group(1)

    def connect(self) -> sqlite3.Connection:
        conn = db.connect(self.settings())
        self.addCleanup(conn.close)
        return conn

    def pair_token(self, person: str) -> str:
        return auth.create_pair_token(self.connect(), person)

    @staticmethod
    def csrf(response: TestResponse) -> str:
        found = CSRF_FIELD.search(response.get_data(as_text=True))
        assert found is not None, "no CSRF field in the page"
        return found.group(1)

    def complete_pairing(
        self,
        client: FlaskClient,
        token: str,
        label: str = "Phone",
        **fields: str,
    ) -> TestResponse:
        page = client.get(f"/pair/{token}")
        # A dead link has no form, so there is no token to send.
        found = CSRF_FIELD.search(page.get_data(as_text=True))
        csrf = found.group(1) if found else "dead-link"
        if not found:
            with client.session_transaction() as session:
                session["csrf"] = csrf
        return client.post(
            f"/pair/{token}", data={"csrf_token": csrf, "label": label, **fields}
        )

    @staticmethod
    def device_cookie(client: FlaskClient) -> str:
        cookie = client.get_cookie(DEVICE_COOKIE)
        assert cookie is not None, "no device cookie"
        return cookie.value

    def rows(self, sql: str, *args: object) -> list[sqlite3.Row]:
        return self.connect().execute(sql, args).fetchall()

    @staticmethod
    def set_cookie_header(response: TestResponse, name: str) -> str:
        for header in response.headers.getlist("Set-Cookie"):
            if header.startswith(f"{name}="):
                return header
        raise AssertionError(f"no Set-Cookie for {name}")

    @contextlib.contextmanager
    def later(self, seconds: int) -> Iterator[None]:
        moved = auth.now() + timedelta(seconds=seconds)
        with mock.patch("larder.auth.now", lambda: moved):
            yield


class StartupTest(AuthTestCase):
    def test_startup_requires_an_admin_in_config(self) -> None:
        cases = {
            "members only": '[[people]]\nname = "Sam"\n',
            "nobody": "",
        }
        for label, people in cases.items():
            with self.subTest(label):
                self.write_config("", people=people)

                with self.assertRaises(ConfigError) as raised:
                    create_app(self.settings())

                self.assertIn("admin", str(raised.exception))

    def test_config_people_synced_to_person_table(self) -> None:
        self.write_config("", people=TWO_ADMINS)
        self.app_logging()
        first = {
            r["name"]: (r["id"], r["active"]) for r in self.rows("SELECT * FROM person")
        }
        self.assertEqual(
            {n: a for n, (_, a) in first.items()}, {"Ada": 1, "Bea": 1, "Sam": 1}
        )

        # Sam leaves the config and Zed arrives: Sam is kept, but inactive.
        self.write_config("", people=ONLY_ADA_AND_ZED)
        self.app_logging()
        second = {
            r["name"]: (r["id"], r["active"]) for r in self.rows("SELECT * FROM person")
        }
        self.assertEqual(
            {n: a for n, (_, a) in second.items()},
            {"Ada": 1, "Bea": 0, "Sam": 0, "Zed": 1},
        )
        for name in ("Ada", "Bea", "Sam"):
            self.assertEqual(second[name][0], first[name][0], name)

        # Sam comes back: the same row is active again.
        self.write_config("", people=TWO_ADMINS)
        self.app_logging()
        third = {
            r["name"]: (r["id"], r["active"]) for r in self.rows("SELECT * FROM person")
        }
        self.assertEqual(third["Sam"], first["Sam"])
        self.assertEqual(third["Zed"][1], 0)


class BootstrapTest(AuthTestCase):
    def test_bootstrap_link_logged_single_use_and_replaced_on_restart(self) -> None:
        app, first_token = self.app_logging()
        client = app.test_client()

        # A GET (a link preview, say) never uses the link up.
        self.assertEqual(client.get(f"/pair/{first_token}").status_code, 200)
        self.assertEqual(client.get(f"/pair/{first_token}").status_code, 200)

        # A restart replaces the unused link.
        restarted, second_token = self.app_logging()
        self.assertNotEqual(first_token, second_token)
        other = restarted.test_client()
        self.assertEqual(other.get(f"/pair/{first_token}").status_code, 404)
        self.assertEqual(other.get(f"/pair/{second_token}").status_code, 200)

        # It works once.
        paired = self.complete_pairing(other, second_token, person="Ada")
        self.assertEqual(paired.status_code, 303)
        self.assertEqual(other.get(f"/pair/{second_token}").status_code, 404)
        fresh = restarted.test_client()
        self.assertEqual(self.complete_pairing(fresh, second_token).status_code, 404)
        self.assertEqual(len(self.rows("SELECT id FROM device")), 1)

    def test_bootstrap_log_hints_at_the_usual_address(self) -> None:
        with self.assertLogs("larder.auth", "WARNING") as logs:
            self.app()

        text = "\n".join(logs.output)
        self.assertRegex(text, r"/pair/[A-Za-z0-9_-]{40,}")
        self.assertIn("usual address", text)

    def test_bootstrap_page_pairs_device_to_chosen_admin(self) -> None:
        self.write_config("", people=TWO_ADMINS)
        app, token = self.app_logging()
        client = app.test_client()

        page = client.get(f"/pair/{token}").get_data(as_text=True)

        self.assertIn("Ada", page)
        self.assertIn("Bea", page)
        self.assertNotIn("Sam", page)  # a member cannot be the first device

        bad = self.complete_pairing(client, token, person="Sam")
        self.assertEqual(bad.status_code, 400)
        unknown = self.complete_pairing(client, token, person="Nobody")
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(self.rows("SELECT id FROM device"), [])

        done = self.complete_pairing(
            client, token, label="Kitchen tablet", person="Bea"
        )

        self.assertEqual(done.status_code, 303)
        self.assertEqual(done.headers["Location"], "/")
        device = self.rows(
            "SELECT person.name AS name, device.label AS label FROM device "
            "JOIN person ON person.id = device.person_id"
        )
        self.assertEqual(
            [(r["name"], r["label"]) for r in device], [("Bea", "Kitchen tablet")]
        )
        # The new device is Bea's: it can open the admin page.
        self.assertEqual(client.get("/admin/devices").status_code, 200)

    def test_no_bootstrap_link_once_an_admin_device_exists(self) -> None:
        # Pair Ada by a pair token, so the bootstrap token is left unused.
        app, _ = self.app_logging()
        self.complete_pairing(app.test_client(), self.pair_token("Ada"))
        unused = (
            "SELECT id FROM pairing_token WHERE kind = 'bootstrap' AND used_at IS NULL"
        )
        self.assertEqual(len(self.rows(unused)), 1)

        with self.assertNoLogs("larder.auth", "DEBUG"):
            self.app()

        self.assertEqual(self.rows(unused), [])

    def test_bootstrap_link_dead_once_an_admin_device_exists(self) -> None:
        app, bootstrap = self.app_logging()
        client = app.test_client()
        self.assertEqual(client.get(f"/pair/{bootstrap}").status_code, 200)
        self.complete_pairing(app.test_client(), self.pair_token("Ada"))
        devices = len(self.rows("SELECT id FROM device"))

        self.assertEqual(client.get(f"/pair/{bootstrap}").status_code, 404)
        late = self.complete_pairing(client, bootstrap, person="Ada")

        self.assertEqual(late.status_code, 404)
        self.assertEqual(len(self.rows("SELECT id FROM device")), devices)
        # The dead POST did not spend the token either.
        self.assertEqual(
            self.rows("SELECT used_at FROM pairing_token WHERE kind = 'bootstrap'")[0][
                0
            ],
            None,
        )

    def test_complete_pairing_rejects_dead_bootstrap_link_and_keeps_it_unspent(
        self,
    ) -> None:
        # Straight at auth.complete_pairing, past the page-level check.
        app, bootstrap = self.app_logging()
        self.complete_pairing(app.test_client(), self.pair_token("Ada"))
        conn = self.connect()
        ada = auth.bootstrap_person_id(conn, self.settings(), "Ada")
        devices = len(self.rows("SELECT id FROM device"))

        done = auth.complete_pairing(conn, bootstrap, "x", ada, ["Ada"])

        self.assertIsNone(done)
        self.assertEqual(len(self.rows("SELECT id FROM device")), devices)
        used = self.rows(
            "SELECT used_at FROM pairing_token WHERE token_hash = ?",
            auth.hash_token(bootstrap),
        )
        self.assertIsNone(used[0][0])  # the consume was rolled back

    def test_member_device_does_not_stop_bootstrap(self) -> None:
        app, token = self.app_logging()
        self.complete_pairing(app.test_client(), token, person="Ada")
        sam = self.pair_token("Sam")
        self.complete_pairing(app.test_client(), sam)
        # Take Ada's device away: only a member's device is left.
        conn = self.connect()
        with db.transaction(conn):
            conn.execute(
                "UPDATE device SET revoked_at = '2026-10-13T00:00:00Z' "
                "WHERE person_id = (SELECT id FROM person WHERE name = 'Ada')"
            )

        _, new_token = self.app_logging()

        self.assertNotEqual(new_token, token)

    def test_revoked_admin_device_brings_bootstrap_back(self) -> None:
        app, token = self.app_logging()
        client = app.test_client()
        self.complete_pairing(client, token, person="Ada")
        conn = self.connect()
        with db.transaction(conn):
            conn.execute("UPDATE device SET revoked_at = '2026-10-13T00:00:00Z'")

        _, new_token = self.app_logging()

        self.assertNotEqual(new_token, token)


class PairingLinkTest(AuthTestCase):
    def test_get_pair_page_does_not_use_up_the_link(self) -> None:
        app = self.app_quiet()
        token = self.pair_token("Sam")
        client = app.test_client()

        for _ in range(3):
            response = client.get(f"/pair/{token}")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Sam", response.get_data(as_text=True))

        self.assertEqual(self.complete_pairing(client, token).status_code, 303)

    def app_quiet(self) -> Flask:
        # The first device is unpaired, so the app logs a bootstrap link.
        with self.assertLogs("larder.auth", "WARNING"):
            return self.app()

    def test_pair_page_without_label_names_a_default_device(self) -> None:
        app = self.app_quiet()
        token = self.pair_token("Sam")

        self.complete_pairing(app.test_client(), token, label="   ")

        labels = [r["label"] for r in self.rows("SELECT label FROM device")]
        self.assertEqual(labels, ["New device"])

    def test_pairing_link_single_use_and_expires_after_10_minutes(self) -> None:
        app = self.app_quiet()
        client = app.test_client()

        used = self.pair_token("Sam")
        self.assertEqual(self.complete_pairing(client, used).status_code, 303)
        self.assertEqual(client.get(f"/pair/{used}").status_code, 404)
        self.assertEqual(self.complete_pairing(client, used).status_code, 404)

        just_in_time = self.pair_token("Sam")
        with self.later(TEN_MINUTES - 1):
            self.assertEqual(client.get(f"/pair/{just_in_time}").status_code, 200)
            done = self.complete_pairing(client, just_in_time)
            self.assertEqual(done.status_code, 303)

        too_late = self.pair_token("Sam")
        with self.later(TEN_MINUTES + 1):
            self.assertEqual(client.get(f"/pair/{too_late}").status_code, 404)
            self.assertEqual(self.complete_pairing(client, too_late).status_code, 404)
        self.assertEqual(len(self.rows("SELECT id FROM device")), 2)

    def test_pairing_link_for_a_person_who_left_the_config_is_invalid(self) -> None:
        self.write_config("", people=TWO_ADMINS)
        self.app_logging()
        token = self.pair_token("Sam")
        self.write_config("", people=ONLY_ADA_AND_ZED)
        app, _ = self.app_logging()

        client = app.test_client()
        self.assertEqual(client.get(f"/pair/{token}").status_code, 404)
        self.assertEqual(self.complete_pairing(client, token).status_code, 404)
        self.assertEqual(self.rows("SELECT id FROM device"), [])

    def test_invalid_links_all_look_the_same(self) -> None:
        app = self.app_quiet()
        client = app.test_client()
        used = self.pair_token("Sam")
        self.complete_pairing(client, used)
        expired = self.pair_token("Sam")

        with self.later(TEN_MINUTES + 1):
            bodies = {
                label: client.get(f"/pair/{token}")
                for label, token in {
                    "never issued": "x" * 43,
                    "used": used,
                    "expired": expired,
                }.items()
            }

        self.assertEqual({r.status_code for r in bodies.values()}, {404})
        self.assertEqual(len({r.get_data() for r in bodies.values()}), 1)

    def test_pair_post_without_valid_csrf_token_is_rejected(self) -> None:
        app = self.app_quiet()
        token = self.pair_token("Sam")
        client = app.test_client()
        client.get(f"/pair/{token}")

        for label, data in {
            "missing": {"label": "x"},
            "wrong": {"label": "x", "csrf_token": "nope"},
        }.items():
            with self.subTest(label):
                self.assertEqual(
                    client.post(f"/pair/{token}", data=data).status_code, 400
                )
        self.assertEqual(self.rows("SELECT id FROM device"), [])
        # The link was not spent by the rejected posts.
        self.assertEqual(self.complete_pairing(client, token).status_code, 303)

    def test_oversized_post_to_pair_is_rejected_with_413(self) -> None:
        app = self.app_quiet()
        token = self.pair_token("Sam")
        client = app.test_client()
        page = client.get(f"/pair/{token}")

        for path in ("/pair/x", f"/pair/{token}"):
            with self.subTest(path=path):
                big = client.post(
                    path,
                    data={"csrf_token": self.csrf(page), "label": "a" * (1024 * 1024)},
                )
                self.assertEqual(big.status_code, 413)
        self.assertEqual(self.rows("SELECT id FROM device"), [])
        # The link was not spent; a normal post still works.
        self.assertEqual(self.complete_pairing(client, token).status_code, 303)


class CliTest(AuthTestCase):
    def run_pair(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        env = {"LARDER_DATA": str(self.data_dir)}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = main(["pair", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_cli_pair_prints_single_use_path(self) -> None:
        code, out, err = self.run_pair("--person", "Sam")

        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^/pair/[A-Za-z0-9_-]{40,}$")
        self.assertIn("usual address", out)
        token = lines[0].removeprefix("/pair/")

        app = self.app_quiet_or_logging()
        client = app.test_client()
        self.assertIn("Sam", client.get(f"/pair/{token}").get_data(as_text=True))
        self.assertEqual(self.complete_pairing(client, token).status_code, 303)
        self.assertEqual(client.get(f"/pair/{token}").status_code, 404)
        devices = self.rows(
            "SELECT person.name AS name FROM device JOIN person ON person.id = device.person_id"
        )
        self.assertEqual([r["name"] for r in devices], ["Sam"])

    def app_quiet_or_logging(self) -> Flask:
        with self.assertLogs("larder.auth", "WARNING"):
            return self.app()

    def test_cli_pair_link_expires_after_10_minutes(self) -> None:
        _, out, _ = self.run_pair("--person", "Ada")
        token = out.splitlines()[0].removeprefix("/pair/")
        app = self.app_quiet_or_logging()

        with self.later(TEN_MINUTES + 1):
            self.assertEqual(app.test_client().get(f"/pair/{token}").status_code, 404)

    def test_cli_pair_unknown_person_fails(self) -> None:
        code, out, err = self.run_pair("--person", "Nobody")

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("Nobody", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.data_dir / "larder.db").exists())  # nothing was made

    def test_cli_pair_needs_an_admin_in_config(self) -> None:
        self.write_config("", people='[[people]]\nname = "Sam"\n')

        code, _, err = self.run_pair("--person", "Sam")

        self.assertEqual(code, 2)
        self.assertIn("admin", err)


class StorageTest(AuthTestCase):
    def test_tokens_stored_hashed_only(self) -> None:
        app, bootstrap = self.app_logging()
        ada, sam = app.test_client(), app.test_client()
        pair = self.pair_token("Sam")
        with self.assertNoLogs("larder", "DEBUG"):  # device tokens are never logged
            self.complete_pairing(ada, bootstrap, person="Ada")
            self.complete_pairing(sam, pair)
        device_tokens = [self.device_cookie(ada), self.device_cookie(sam)]

        hashes = {r["token_hash"] for r in self.rows("SELECT token_hash FROM device")}
        self.assertEqual(hashes, {auth.hash_token(t) for t in device_tokens})
        token_hashes = {
            r["token_hash"] for r in self.rows("SELECT token_hash FROM pairing_token")
        }
        self.assertEqual(
            token_hashes, {auth.hash_token(bootstrap), auth.hash_token(pair)}
        )
        for row in self.rows("SELECT token_hash FROM device"):
            self.assertRegex(row["token_hash"], r"^[0-9a-f]{64}$")
        # No raw token appears anywhere in the database files.
        raw = b"".join(
            path.read_bytes()
            for path in self.data_dir.glob("larder.db*")
            if path.is_file()
        )
        for secret in (bootstrap, pair, *device_tokens):
            self.assertNotIn(secret.encode(), raw)

    def test_session_key_created_with_mode_0600_and_reused(self) -> None:
        key_path = self.data_dir / "secrets" / "session_key"
        self.app_logging()

        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        first = key_path.read_bytes()
        self.assertEqual(len(first), 32)

        self.app_logging()

        self.assertEqual(key_path.read_bytes(), first)

    def test_session_key_survives_restart_for_csrf(self) -> None:
        app, token = self.app_logging()
        client = app.test_client()
        csrf = self.csrf(client.get(f"/pair/{token}"))

        restarted, _ = self.app_logging()  # replaces the bootstrap link...
        # ...so use a pairing link made before the restart for the rest.
        other = restarted.test_client()
        other.set_cookie("session", self.cookie_value(client, "session"))
        link = self.pair_token("Sam")
        done = other.post(f"/pair/{link}", data={"csrf_token": csrf, "label": "x"})

        self.assertEqual(done.status_code, 303)

    @staticmethod
    def cookie_value(client: FlaskClient, name: str) -> str:
        cookie = client.get_cookie(name)
        assert cookie is not None
        return cookie.value

    def test_short_session_key_is_a_config_error(self) -> None:
        self.app_logging()
        key_path = self.data_dir / "secrets" / "session_key"
        key_path.write_bytes(b"short")

        with self.assertRaises(ConfigError) as raised:
            self.app()

        self.assertIn("session_key", str(raised.exception))

    def test_unreadable_session_key_is_a_clean_config_error(self) -> None:
        self.app_logging()
        key_path = self.data_dir / "secrets" / "session_key"
        key_path.unlink()
        key_path.mkdir()  # reading it raises IsADirectoryError, an OSError

        with self.assertRaises(ConfigError) as raised:
            self.app()

        self.assertIn(str(key_path), str(raised.exception))

    def test_uncreatable_session_key_is_a_clean_config_error(self) -> None:
        key_path = self.data_dir / "secrets" / "session_key"
        with (
            mock.patch("larder.auth.os.link", side_effect=OSError("no hard links")),
            self.assertRaises(ConfigError) as raised,
        ):
            self.app()

        self.assertIn(str(key_path), str(raised.exception))
        self.assertEqual(list(key_path.parent.iterdir()), [])  # no temp file left

    def test_main_reports_session_key_failure_without_traceback(self) -> None:
        err = io.StringIO()
        env = {"LARDER_DATA": str(self.data_dir)}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("larder.auth.os.link", side_effect=OSError("no hard links")),
            contextlib.redirect_stderr(err),
        ):
            code = main([])

        self.assertEqual(code, 2)
        self.assertIn("session_key", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_pairing_token_requires_person_and_expiry_unless_bootstrap(self) -> None:
        self.app_logging()
        conn = self.connect()
        insert = (
            "INSERT INTO pairing_token (token_hash, kind, person_id, created_at, expires_at) "
            "VALUES (?, ?, ?, '2026-10-13T00:00:00Z', ?)"
        )
        person = conn.execute("SELECT id FROM person LIMIT 1").fetchone()[0]
        bad = {
            "pair without person": ("a", "pair", None, "2026-10-13T00:10:00Z"),
            "pair without expiry": ("b", "pair", person, None),
            "unknown kind": ("c", "other", person, "2026-10-13T00:10:00Z"),
        }
        for label, args in bad.items():
            with self.subTest(label), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(insert, args)
        conn.execute(insert, ("d", "bootstrap", None, None))
        conn.execute(insert, ("e", "pair", person, "2026-10-13T00:10:00Z"))


class CookieTest(AuthTestCase):
    def paired(self) -> tuple[Flask, FlaskClient, TestResponse]:
        app, token = self.app_logging()
        client = app.test_client()
        response = self.complete_pairing(client, token, person="Ada")
        return app, client, response

    def test_cookie_max_age_one_year_renewed_on_use(self) -> None:
        _, client, response = self.paired()
        header = self.set_cookie_header(response, DEVICE_COOKIE)
        self.assertIn(f"Max-Age={ONE_YEAR}", header)
        device = self.device_cookie(client)

        with self.later(2 * 3600):
            renewed = client.get("/no-such-page")

        self.assertEqual(renewed.status_code, 404)  # paired, so not 401
        again = self.set_cookie_header(renewed, DEVICE_COOKIE)
        self.assertIn(f"Max-Age={ONE_YEAR}", again)
        self.assertIn(f"{DEVICE_COOKIE}={device}", again)

    def test_last_seen_updates_when_cookie_is_renewed(self) -> None:
        _, client, _ = self.paired()
        before = self.rows("SELECT last_seen FROM device")[0]["last_seen"]

        with self.later(2 * 3600):
            client.get("/no-such-page")
            expected = auth.iso(auth.now())

        after = self.rows("SELECT last_seen FROM device")[0]["last_seen"]
        self.assertNotEqual(before, after)
        self.assertEqual(after, expected)

    def test_cookie_not_reissued_within_the_throttle(self) -> None:
        _, client, _ = self.paired()

        with self.later(600):
            quiet = client.get("/no-such-page")

        self.assertEqual(quiet.status_code, 404)
        self.assertEqual(
            [h for h in quiet.headers.getlist("Set-Cookie") if DEVICE_COOKIE in h], []
        )
        device = self.rows("SELECT last_seen, paired_at FROM device")[0]
        self.assertEqual(device["last_seen"], device["paired_at"])

    def test_cookie_flags_httponly_samesite_and_secure_when_https(self) -> None:
        self.write_config('trusted_proxies = ["127.0.0.1"]\n')
        app, _ = self.app_logging()

        for scheme, headers, secure in (
            ("http", {}, False),
            ("https", FORWARDED_HTTPS, True),
        ):
            client = app.test_client()
            token = self.pair_token("Sam")
            page = client.get(f"/pair/{token}", headers=headers)
            done = client.post(
                f"/pair/{token}",
                headers=headers,
                data={"csrf_token": self.csrf(page), "label": "x"},
            )
            cookies = {
                DEVICE_COOKIE: self.set_cookie_header(done, DEVICE_COOKIE),
                "session": self.set_cookie_header(page, "session"),
            }
            for name, header in cookies.items():
                with self.subTest(scheme=scheme, cookie=name):
                    self.assertIn("HttpOnly", header)
                    self.assertIn("SameSite=Lax", header)
                    self.assertEqual("Secure" in header, secure)


class GuardTest(AuthTestCase):
    def test_unpaired_request_gets_401_unpaired_page(self) -> None:
        app, _ = self.app_logging()
        client = app.test_client()

        for method, path in (
            ("get", "/"),
            ("get", "/no-such-page"),
            ("get", "/admin/devices"),
            ("post", "/admin/devices/pair"),
        ):
            with self.subTest(path=path, method=method):
                response = getattr(client, method)(path)
                body = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 401)
                self.assertIn("isn't set up yet", body)
                self.assertIn("ask an admin", body.lower())
                for name in ("Ada", "Sam"):
                    self.assertNotIn(name, body)
                self.assertNotIn("/admin/devices", body)
                self.assertEqual(response.headers["Content-Security-Policy"], CSP)

    def test_garbage_device_cookie_gets_the_unpaired_page(self) -> None:
        app, _ = self.app_logging()
        client = app.test_client()
        client.set_cookie(DEVICE_COOKIE, "not-a-real-token")

        self.assertEqual(client.get("/no-such-page").status_code, 401)

    def test_healthz_pair_and_static_open_when_unpaired(self) -> None:
        app, token = self.app_logging()
        client = app.test_client()

        self.assertEqual(client.get("/healthz").status_code, 200)
        css = client.get("/static/larder.css")
        self.addCleanup(css.close)
        self.assertEqual(css.status_code, 200)
        self.assertEqual(client.get(f"/pair/{token}").status_code, 200)
        # An invalid pairing link is a 404, not the unpaired page.
        self.assertEqual(client.get("/pair/not-a-token").status_code, 404)

    def test_revoked_device_rejected(self) -> None:
        app, token = self.app_logging()
        admin = app.test_client()
        self.complete_pairing(admin, token, person="Ada")
        sam = app.test_client()
        self.complete_pairing(sam, self.pair_token("Sam"), label="Sam's phone")
        self.assertEqual(sam.get("/no-such-page").status_code, 404)
        sam_device = self.rows("SELECT id FROM device WHERE label = 'Sam''s phone'")[0][
            "id"
        ]

        page = admin.get("/admin/devices")
        revoke = admin.post(
            f"/admin/devices/{sam_device}/revoke",
            data={"csrf_token": self.csrf(page)},
        )

        self.assertEqual(revoke.status_code, 303)
        self.assertEqual(sam.get("/no-such-page").status_code, 401)
        # Only that device was revoked.
        self.assertEqual(admin.get("/admin/devices").status_code, 200)

    def test_inactive_person_device_rejected(self) -> None:
        self.write_config("", people=TWO_ADMINS)
        app, token = self.app_logging()
        client = app.test_client()
        self.complete_pairing(client, token, person="Ada")
        sam = app.test_client()
        self.complete_pairing(sam, self.pair_token("Sam"))
        cookie = self.device_cookie(sam)
        self.assertEqual(sam.get("/no-such-page").status_code, 404)

        self.write_config("", people=ONLY_ADA_AND_ZED)
        restarted = self.app()

        again = restarted.test_client()
        again.set_cookie(DEVICE_COOKIE, cookie)
        self.assertEqual(again.get("/no-such-page").status_code, 401)

    def test_person_marked_inactive_in_db_is_rejected(self) -> None:
        # The database row alone decides: Sam is still in the config.
        app, token = self.app_logging()
        self.complete_pairing(app.test_client(), token, person="Ada")
        sam = app.test_client()
        self.complete_pairing(sam, self.pair_token("Sam"))
        self.assertEqual(sam.get("/no-such-page").status_code, 404)
        conn = self.connect()
        with db.transaction(conn):
            conn.execute("UPDATE person SET active = 0 WHERE name = 'Sam'")

        self.assertEqual(sam.get("/no-such-page").status_code, 401)

    def test_person_missing_from_config_is_rejected(self) -> None:
        # The config alone decides: Sam's row is active but he left the config.
        self.write_config("", people=TWO_ADMINS)
        app, token = self.app_logging()
        self.complete_pairing(app.test_client(), token, person="Ada")
        sam = app.test_client()
        self.complete_pairing(sam, self.pair_token("Sam"))
        self.write_config("", people=ONLY_ADA_AND_ZED)
        restarted = self.app()
        conn = self.connect()
        with db.transaction(conn):
            conn.execute("UPDATE person SET active = 1 WHERE name = 'Sam'")
        again = restarted.test_client()
        again.set_cookie(DEVICE_COOKIE, self.device_cookie(sam))

        self.assertEqual(again.get("/no-such-page").status_code, 401)

    def test_member_gets_403_on_admin_pages_and_no_admin_links(self) -> None:
        app, token = self.app_logging()
        admin = app.test_client()
        self.complete_pairing(admin, token, person="Ada")
        member = app.test_client()
        self.complete_pairing(member, self.pair_token("Sam"))
        device = self.rows("SELECT id FROM device")[0]["id"]

        for method, path in (
            ("get", "/admin/devices"),
            ("post", "/admin/devices/pair"),
            ("post", f"/admin/devices/{device}/revoke"),
            ("post", f"/admin/devices/{device}/rename"),
        ):
            with self.subTest(method=method, path=path):
                response = getattr(member, method)(path)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("Ada", response.get_data(as_text=True))
        self.assertEqual(self.rows("SELECT revoked_at FROM device")[0][0], None)

        self.assertNotIn(
            "/admin/devices", member.get("/no-such-page").get_data(as_text=True)
        )
        self.assertIn(
            "/admin/devices", admin.get("/no-such-page").get_data(as_text=True)
        )


class CsrfTest(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        app, token = self.app_logging()
        self.flask_app = app
        self.admin = app.test_client()
        self.complete_pairing(self.admin, token, person="Ada")
        self.device = self.rows("SELECT id FROM device")[0]["id"]

    def test_csrf_protection_on_state_changing_posts(self) -> None:
        page = self.admin.get("/admin/devices")
        good = self.csrf(page)
        posts = {
            "pair": ("/admin/devices/pair", {"person": "Sam"}),
            "rename": (f"/admin/devices/{self.device}/rename", {"label": "Renamed"}),
            "revoke": (f"/admin/devices/{self.device}/revoke", {}),
        }
        for label, (path, data) in posts.items():
            for kind, extra in (
                ("missing", {}),
                ("wrong", {"csrf_token": "x" * len(good)}),
                ("empty", {"csrf_token": ""}),
            ):
                with self.subTest(post=label, token=kind):
                    response = self.admin.post(path, data={**data, **extra})
                    self.assertEqual(response.status_code, 400)

        device = self.rows("SELECT label, revoked_at FROM device")[0]
        self.assertEqual((device["label"], device["revoked_at"]), ("Phone", None))
        self.assertEqual(
            self.rows("SELECT id FROM pairing_token WHERE kind = 'pair'"), []
        )

        accepted = self.admin.post(
            f"/admin/devices/{self.device}/rename",
            data={"label": "Renamed", "csrf_token": good},
        )
        self.assertEqual(accepted.status_code, 303)

    def test_csrf_token_is_per_session(self) -> None:
        other = self.flask_app.test_client()
        other.set_cookie(DEVICE_COOKIE, self.device_cookie(self.admin))
        mine = self.csrf(self.admin.get("/admin/devices"))
        theirs = self.csrf(other.get("/admin/devices"))

        self.assertNotEqual(mine, theirs)
        crossed = other.post(
            f"/admin/devices/{self.device}/revoke", data={"csrf_token": mine}
        )
        self.assertEqual(crossed.status_code, 400)

    def test_every_form_carries_the_token(self) -> None:
        self.admin.post(
            "/admin/devices/pair",
            data={
                "person": "Sam",
                "csrf_token": self.csrf(self.admin.get("/admin/devices")),
            },
        )
        html = self.admin.get("/admin/devices").get_data(as_text=True)

        forms = re.findall(r"<form\b.*?</form>", html, re.DOTALL)
        post_forms = [f for f in forms if 'method="post"' in f]
        self.assertGreaterEqual(len(post_forms), 3)  # pair, rename, revoke
        for form in post_forms:
            self.assertRegex(form, r'name="csrf_token" value="[^"]+"')

    def test_csrf_covers_put_patch_and_delete(self) -> None:
        app = self.app()  # a fresh app: routes can only be added before a request

        def probe() -> str:
            return "ran"

        app.add_url_rule(
            "/_csrf", "csrf_probe", probe, methods=["PUT", "PATCH", "DELETE", "POST"]
        )
        client = app.test_client()
        client.set_cookie(DEVICE_COOKIE, self.device_cookie(self.admin))
        token = self.csrf(client.get("/admin/devices"))

        for method in ("put", "patch", "delete", "post"):
            with self.subTest(method=method):
                bare = getattr(client, method)("/_csrf")
                self.assertEqual(bare.status_code, 400)
                ok = getattr(client, method)("/_csrf", data={"csrf_token": token})
                self.assertEqual(ok.status_code, 200)

    def test_safe_methods_need_no_token(self) -> None:
        self.assertEqual(self.admin.get("/admin/devices").status_code, 200)
        self.assertEqual(self.admin.head("/admin/devices").status_code, 200)


class AdminDevicesTest(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        app, token = self.app_logging()
        self.flask_app = app
        self.admin = app.test_client()
        self.complete_pairing(self.admin, token, label="Ada laptop", person="Ada")

    def post(self, path: str, **data: str) -> TestResponse:
        csrf = self.csrf(self.admin.get("/admin/devices"))
        return self.admin.post(path, data={"csrf_token": csrf, **data})

    def test_page_lists_devices_with_person_label_and_state(self) -> None:
        self.complete_pairing(
            self.flask_app.test_client(), self.pair_token("Sam"), label="Sam phone"
        )
        sam_device = self.rows("SELECT id FROM device WHERE label = 'Sam phone'")[0][
            "id"
        ]
        self.post(f"/admin/devices/{sam_device}/revoke")

        html = self.admin.get("/admin/devices").get_data(as_text=True)

        for text in ("Ada laptop", "Sam phone", "Ada", "Sam", "revoked"):
            self.assertIn(text, html)
        # Both people can be picked for a new pairing link.
        self.assertRegex(html, r"<option[^>]*>Sam</option>")
        self.assertRegex(html, r"<option[^>]*>Ada</option>")

    def test_rename_changes_only_that_label(self) -> None:
        self.complete_pairing(
            self.flask_app.test_client(), self.pair_token("Sam"), label="Sam phone"
        )
        ada = self.rows("SELECT id FROM device WHERE label = 'Ada laptop'")[0]["id"]

        done = self.post(f"/admin/devices/{ada}/rename", label="  Office PC ")

        self.assertEqual(done.status_code, 303)
        labels = sorted(r["label"] for r in self.rows("SELECT label FROM device"))
        self.assertEqual(labels, ["Office PC", "Sam phone"])
        blank = self.post(f"/admin/devices/{ada}/rename", label="  ")
        self.assertEqual(blank.status_code, 400)

    def test_unknown_device_is_404(self) -> None:
        self.assertEqual(self.post("/admin/devices/999/revoke").status_code, 404)
        self.assertEqual(
            self.post("/admin/devices/999/rename", label="x").status_code, 404
        )

    def test_revoke_keeps_the_first_revocation_time(self) -> None:
        device = self.rows("SELECT id FROM device")[0]["id"]
        sam = self.flask_app.test_client()
        self.complete_pairing(sam, self.pair_token("Sam"))
        sam_id = self.rows(
            "SELECT id FROM device WHERE person_id != (SELECT person_id FROM device WHERE id = ?)",
            device,
        )[0]["id"]
        self.post(f"/admin/devices/{sam_id}/revoke")
        first = self.rows("SELECT revoked_at FROM device WHERE id = ?", sam_id)[0][0]

        with self.later(3600):
            self.post(f"/admin/devices/{sam_id}/revoke")

        again = self.rows("SELECT revoked_at FROM device WHERE id = ?", sam_id)[0][0]
        self.assertIsNotNone(first)
        self.assertEqual(first, again)

    def test_pairing_page_shows_qr_svg_and_link(self) -> None:
        self.write_config('trusted_proxies = ["127.0.0.1"]\n')
        app = self.app()  # Ada's device from setUp exists, so no bootstrap link
        admin = app.test_client()
        admin.set_cookie(DEVICE_COOKIE, self.device_cookie(self.admin))

        response = admin.post(
            "/admin/devices/pair",
            headers=FORWARDED_HTTPS,
            data={
                "csrf_token": self.csrf(admin.get("/admin/devices")),
                "person": "Sam",
            },
        )

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        link = re.search(r"https://larder\.example/pair/[A-Za-z0-9_-]+", html)
        assert link is not None, "no full link in the page"
        svg = re.search(r"<svg\b.*?</svg>", html, re.DOTALL)
        assert svg is not None, "no inline SVG in the page"
        # The code encodes exactly the link that is shown.
        self.assertEqual(svg.group(0), auth.qr_svg(link.group(0)))
        # Inline SVG must not need a CSP change: no style, script or event attrs.
        self.assertEqual(response.headers["Content-Security-Policy"], CSP)
        lowered = svg.group(0).lower()
        for banned in (
            "<style",
            " style=",
            "<script",
            "<image",
            "href=",
            "javascript:",
        ):
            self.assertNotIn(banned, lowered)
        self.assertNotRegex(lowered, r"\son\w+\s*=")
        self.assertIn("Sam", html)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        # The link works on another device.
        new_device = app.test_client()
        opened = new_device.get(urlpath(link.group(0)))
        self.assertEqual(opened.status_code, 200)
        self.assertIn("Sam", opened.get_data(as_text=True))

    def test_pairing_link_in_page_is_the_shown_token_for_that_person(self) -> None:
        html = self.post("/admin/devices/pair", person="Sam").get_data(as_text=True)

        token = PAIR_PATH.search(html)
        assert token is not None
        row = self.rows(
            "SELECT person.name AS name, kind, expires_at, created_at FROM pairing_token "
            "JOIN person ON person.id = pairing_token.person_id WHERE token_hash = ?",
            auth.hash_token(token.group(1)),
        )[0]
        self.assertEqual((row["name"], row["kind"]), ("Sam", "pair"))
        self.assertEqual(
            auth.parse_iso(row["expires_at"]) - auth.parse_iso(row["created_at"]),
            timedelta(minutes=10),
        )

    def test_pairing_for_unknown_person_is_rejected(self) -> None:
        response = self.post("/admin/devices/pair", person="Nobody")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.rows("SELECT id FROM pairing_token WHERE kind = 'pair'"), []
        )


def urlpath(url: str) -> str:
    return "/" + url.split("/", 3)[3]
