# SPDX-License-Identifier: AGPL-3.0-only
import datetime
from unittest import mock

from flask.testing import FlaskClient

from larder import auth, db
from larder.web import create_app
from tests.test_web_shell import CSRF_FIELD, WebShellTestCase

TODAY = datetime.date(2026, 11, 3)


class HomeTest(WebShellTestCase):
    def seed_meal(
        self, title: str, day: datetime.date, status: str = "planned"
    ) -> None:
        """Add a recipe and put it on ``day`` with ``status``."""
        conn = db.connect(self.settings())
        try:
            with db.transaction(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO recipe_source (kind, location) "
                    "VALUES ('test', 'test')"
                )
                conn.execute(
                    "INSERT INTO meal_plan (week_start) VALUES (?) "
                    "ON CONFLICT (week_start) DO NOTHING",
                    (day.isoformat(),),
                )
                plan_id = conn.execute(
                    "SELECT id FROM meal_plan WHERE week_start = ?",
                    (day.isoformat(),),
                ).fetchone()[0]
                recipe_id = conn.execute(
                    "INSERT INTO recipe (source_id, slug, title, source_ref, "
                    "content_hash, body) "
                    "VALUES ((SELECT id FROM recipe_source), ?, ?, '', '', '') "
                    "RETURNING id",
                    (title.lower().replace(" ", "-"), title),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO planned_meal (meal_plan_id, date, recipe_id, status) "
                    "VALUES (?, ?, ?, ?)",
                    (plan_id, day.isoformat(), recipe_id, status),
                )
        finally:
            conn.close()

    def landing(self, client: FlaskClient) -> str:
        with mock.patch("larder.web.home_views.today", return_value=TODAY):
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_paired_device_gets_the_landing_page(self) -> None:
        app = create_app(self.settings())

        response = self.paired_client(app).get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn("<h1>You're paired</h1>", response.get_data(as_text=True))

    def test_unpaired_device_still_gets_401_at_root(self) -> None:
        app = create_app(self.settings())

        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("You're paired", response.get_data(as_text=True))

    def test_landing_says_paired_when_no_plan(self) -> None:
        app = create_app(self.settings())

        body = self.landing(self.paired_client(app))

        self.assertIn("You're paired", body)
        self.assertNotIn("Tonight", body)

    def test_landing_shows_tonights_meal_when_planned(self) -> None:
        app = create_app(self.settings())
        # Two planned meals on one day: the lowest id is shown.
        self.seed_meal("Leek Pie", TODAY)
        self.seed_meal("Fish Stew", TODAY)

        body = self.landing(self.paired_client(app))

        self.assertIn("You're paired", body)
        self.assertIn("Leek Pie", body)
        self.assertNotIn("Fish Stew", body)

    def test_landing_ignores_other_dates_and_skipped_meals(self) -> None:
        app = create_app(self.settings())
        self.seed_meal("Yesterday Soup", TODAY - datetime.timedelta(days=1))
        self.seed_meal("Tomorrow Curry", TODAY + datetime.timedelta(days=1))
        self.seed_meal("Skipped Bake", TODAY, status="skipped")
        self.seed_meal("Made Roast", TODAY, status="made")

        body = self.landing(self.paired_client(app))

        for title in ("Yesterday Soup", "Tomorrow Curry", "Skipped Bake", "Made Roast"):
            with self.subTest(title=title):
                self.assertNotIn(title, body)
        self.assertNotIn("Tonight", body)

    def test_pairing_redirect_lands_on_a_200_page(self) -> None:
        app = create_app(self.settings())
        conn = db.connect(self.settings())
        try:
            token = auth.create_pair_token(conn, "Ada")
        finally:
            conn.close()
        client = app.test_client()
        page = client.get(f"/pair/{token}")
        csrf = CSRF_FIELD.search(page.get_data(as_text=True))
        assert csrf is not None, "pair page has no CSRF field"
        done = client.post(
            f"/pair/{token}", data={"csrf_token": csrf.group(1), "label": "Test"}
        )
        self.assertEqual(done.status_code, 303)
        location = done.headers["Location"]

        landed = client.get(location)

        self.assertEqual(landed.status_code, 200)
        self.assertIn("You're paired", landed.get_data(as_text=True))
