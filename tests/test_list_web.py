# SPDX-License-Identifier: AGPL-3.0-only
import re
import sqlite3
from pathlib import Path

from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from larder import db
from larder.web import create_app
from tests.test_web_shell import CSRF_FIELD, WebShellTestCase


class ListWebTest(WebShellTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app(self.settings())
        self.client = self.paired_client(self.app, "Sam")
        self.other = self.paired_client(self.app, "Ada")
        self.conn = db.connect(self.settings())
        self.addCleanup(self.conn.close)
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO category (name,sort_order) VALUES ('First',-2),('Second',-1)"
            )
            self.first = self.conn.execute(
                "INSERT INTO item (canonical_name,category) VALUES ('Zucchini','First')"
            ).lastrowid
            self.second = self.conn.execute(
                "INSERT INTO item (canonical_name,category) VALUES ('Apples','Second')"
            ).lastrowid

    def page(self) -> str:
        return self.client.get("/list").get_data(as_text=True)

    def post(
        self, action: str, client: FlaskClient | None = None, **data: str | list[str]
    ) -> TestResponse:
        client = client or self.client
        page = client.get("/list").get_data(as_text=True)
        match = CSRF_FIELD.search(page)
        with client.session_transaction() as session:
            token = match.group(1) if match else session["csrf"]
        return client.post(
            "/list",
            data={
                "action": action,
                "csrf_token": token,
                "list_id": re.search(r'name="list_id" value="([0-9]+)"', page).group(1)
                if 'name="list_id"' in page
                else "0",
                **data,
            },
            follow_redirects=True,
        )

    def rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM list_item WHERE list_id=(SELECT id FROM shopping_list WHERE archived_at IS NULL) ORDER BY id"
        ).fetchall()

    def test_add_free_text_and_canonical_items(self) -> None:
        self.assertEqual(self.post("add", text="<soap>").status_code, 200)
        self.post("add", text="Zucchini")
        self.assertEqual(
            [(r["free_text"], r["item_id"], r["source"]) for r in self.rows()],
            [("<soap>", None, "manual"), (None, self.first, "manual")],
        )
        self.assertIn("&lt;soap&gt;", self.page())
        self.assertEqual(self.post("add", text=" ").status_code, 400)
        self.assertEqual(
            self.client.post(
                "/list",
                data={"action": "ordered", "list_id": str(self.rows()[0]["list_id"])},
            ).status_code,
            400,
        )
        self.assertEqual(self.app.test_client().get("/list").status_code, 401)
        self.post("remove", row_id=str(self.rows()[0]["id"]))
        self.assertNotIn("&lt;soap&gt;", self.page())

    def test_check_and_uncheck_persist_with_added_by(self) -> None:
        self.post("add", text="Oranges")
        row_id = str(self.rows()[0]["id"])
        form = next(
            form
            for form in re.findall(r"<form\b.*?</form>", self.page(), re.DOTALL)
            if f'name="row_id" value="{row_id}"' in form
        )
        checked_fields = re.findall(r'name="checked" value="([01])"', form)
        self.post("check", row_id=row_id, checked=checked_fields, client=self.other)
        self.assertEqual(self.rows()[0]["checked"], 1)
        self.assertIn("<s>Oranges</s>", self.page())
        self.assertIn("added by Sam", self.page())
        self.post("check", row_id=row_id, checked="0")
        self.assertEqual(self.rows()[0]["checked"], 0)
        self.post("clear")
        self.assertIn("Oranges", self.page())
        self.post("check", row_id=row_id, checked="1")
        self.post("clear")
        self.assertNotIn("Oranges", self.page())

    def test_items_grouped_in_category_order(self) -> None:
        for text in ("Loose", "Apples", "Zucchini"):
            self.post("add", text=text)
        body = self.page()
        self.assertRegex(
            body,
            r"(?s)<h2>First</h2>.*Zucchini.*<h2>Second</h2>.*Apples.*<h2>Other</h2>.*Loose",
        )

    def test_staples_one_tap_add(self) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO staple (item_id,default_qty,unit_id) VALUES (?,2,(SELECT id FROM unit WHERE name='kg'))",
                (self.first,),
            )
        self.assertIn("<summary>Staples</summary>", self.page())
        response = self.post("staple", staple_id="1")
        self.assertIn("2 kg", response.get_data(as_text=True))
        self.assertEqual(
            [(r["item_id"], r["qty"], r["source"]) for r in self.rows()],
            [(self.first, 2, "staple")],
        )

    def test_ordered_button_resets_per_adr_0020(self) -> None:
        self.post("add", text="Keep archived")
        self.post("add", text="Unchecked")
        self.post("check", row_id=str(self.rows()[0]["id"]), checked="1")
        old = self.rows()[0]["list_id"]
        response = self.post("ordered")
        self.assertEqual(
            (
                "Your list is empty" in response.get_data(as_text=True),
                len(self.rows()),
                self.conn.execute(
                    "SELECT archived_at FROM shopping_list WHERE id=?", (old,)
                ).fetchone()[0]
                is not None,
                self.conn.execute(
                    "SELECT count(*) FROM list_item WHERE list_id=?", (old,)
                ).fetchone()[0],
            ),
            (True, 0, True, 2),
        )

        new_list = self.conn.execute(
            "SELECT id FROM shopping_list WHERE archived_at IS NULL"
        ).fetchone()[0]
        with db.transaction(self.conn):
            staple_id = self.conn.execute(
                "INSERT INTO staple (item_id) VALUES (?)", (self.first,)
            ).lastrowid
        milk = self.conn.execute(
            "SELECT id FROM item WHERE canonical_name='milk' COLLATE NOCASE"
        ).fetchone()[0]
        for action, fields, expected in (
            ("add", {"text": "Milk"}, (None, milk, "manual", new_list)),
            (
                "staple",
                {"staple_id": str(staple_id)},
                (None, self.first, "staple", new_list),
            ),
        ):
            with self.subTest(stale_action=action):
                before = [
                    (r["free_text"], r["item_id"], r["source"], r["list_id"])
                    for r in self.rows()
                ]
                response = self.post(action, list_id=str(old), **fields)
                self.assertEqual(
                    (
                        response.status_code,
                        [
                            (r["free_text"], r["item_id"], r["source"], r["list_id"])
                            for r in self.rows()
                        ],
                    ),
                    (200, before + [expected]),
                )
        for action, fields in (
            ("ordered", {}),
            ("remove", {"row_id": str(self.rows()[0]["id"]) if self.rows() else "1"}),
        ):
            with self.subTest(stale_action=action):
                self.assertEqual(
                    self.post(action, list_id=str(old), **fields).status_code, 409
                )

    def test_refresh_endpoint_returns_changes_since_version(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "src/larder/web/static/list.js"
        ).read_text()
        self.assertNotIn("activeElement", script)
        self.assertIn("dataset.dirty", script)
        page = self.page()
        self.assertIn('src="/static/list.js"', page)
        self.assertIn('data-poll-ms="5000"', page)
        version = re.search(r'data-version="([^"]+)"', page).group(1)
        self.assertEqual(
            self.client.get(
                "/list/refresh", query_string={"version": version}
            ).status_code,
            204,
        )
        self.post("add", text="From another phone", client=self.other)
        response = self.client.get("/list/refresh", query_string={"version": version})
        self.assertIn("From another phone", response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        version = re.search(
            r'data-version="([^"]+)"', response.get_data(as_text=True)
        ).group(1)
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO staple (item_id) VALUES (?)", (self.second,))
        self.assertIn(
            "Apples",
            self.client.get(
                "/list/refresh", query_string={"version": version}
            ).get_data(as_text=True),
        )
