# SPDX-License-Identifier: AGPL-3.0-only
import datetime
import re
from pathlib import Path
from unittest import mock

from werkzeug.test import TestResponse

from larder import db, planner_db
from larder.web import create_app
from tests.test_web_shell import CSRF_FIELD, WebShellTestCase

TODAY = datetime.date(2026, 11, 3)
ROOT = Path(__file__).resolve().parent.parent


class TodayTest(WebShellTestCase):
    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch("larder.web.home_views.today", return_value=TODAY)
        patch.start()
        self.addCleanup(patch.stop)
        self.app = create_app(self.settings())
        self.client = self.paired_client(self.app, "Sam")
        self.conn = db.connect(self.settings())
        self.addCleanup(self.conn.close)

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
                    (
                        (
                            day - datetime.timedelta(days=(day.weekday() + 1) % 7)
                        ).isoformat(),
                    ),
                )
                plan_id = conn.execute(
                    "SELECT id FROM meal_plan WHERE week_start = ?",
                    (
                        (
                            day - datetime.timedelta(days=(day.weekday() + 1) % 7)
                        ).isoformat(),
                    ),
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

    def page(self) -> str:
        return self.client.get("/").get_data(as_text=True)

    def submit(self, action: str, **extra: str) -> TestResponse:
        page = self.page()
        csrf = CSRF_FIELD.search(page)
        with self.client.session_transaction() as session:
            token = csrf.group(1) if csrf else session["csrf"]
        meal = re.search(r'name="meal_id" value="([0-9]+)"', page)
        return self.client.post(
            "/",
            data={
                "csrf_token": token,
                "action": action,
                "meal_id": meal.group(1) if meal else "0",
                **extra,
            },
        )

    def meals(self) -> list[tuple]:
        return [
            tuple(row)
            for row in self.conn.execute(
                "SELECT date,status FROM planned_meal ORDER BY date,id"
            )
        ]

    def test_root_shows_tonights_planned_meal(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        self.seed_meal("Tomorrow Curry", TODAY + datetime.timedelta(days=1))
        body = self.page()
        self.assertIn("Tonight’s dinner", body)
        self.assertIn("Leek Pie", body)
        self.assertNotIn("Tomorrow Curry", body)
        self.assertIn('href="/recipes/1"', body)
        self.assertIn('value="made">Made it</button>', body)
        self.assertIn('value="skip">Skip tonight</button>', body)
        self.assertIn('href="/list"', body)

    def test_no_plan_shows_friendly_prompt(self) -> None:
        body = self.page().split("<main>", 1)[1]
        self.assertIn("Nothing planned for tonight", body)
        self.assertIn('href="/plan"', body)
        self.assertIn('href="/list"', body)
        self.assertNotIn('value="made"', body)

    def test_made_updates_recency_history(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        response = self.submit("made")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.meals(), [("2026-11-03", "made")])
        self.assertEqual(
            [tuple(row) for row in planner_db.meal_history(self.conn)],
            [(1, "2026-11-03")],
        )
        self.assertIn("Made it — Leek Pie", response.get_data(as_text=True))
        self.assertNotIn('value="made"', self.page())

    def test_skip_applies_roll_forward_rule(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        self.seed_meal("Busy Wednesday", TODAY + datetime.timedelta(days=1))
        response = self.submit("skip")
        self.assertEqual(
            self.meals(),
            [
                ("2026-11-03", "skipped"),
                ("2026-11-04", "planned"),
                ("2026-11-05", "planned"),
            ],
        )
        self.assertIn(
            "Skipped Leek Pie — moved to Thursday", response.get_data(as_text=True)
        )
        self.assertNotIn('value="skip"', response.get_data(as_text=True))
        self.assertEqual(
            [tuple(row) for row in planner_db.meal_history(self.conn)],
            [(2, "2026-11-04"), (1, "2026-11-05")],
        )

    def test_page_has_large_tap_targets_and_no_admin_links_for_member(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        body = self.page()
        self.assertIn('<form method="post" class="stack">', body)
        self.assertIsNotNone(CSRF_FIELD.search(body))
        self.assertNotIn("/admin/", body)
        css = (ROOT / "src/larder/web/static/larder.css").read_text()
        self.assertRegex(css, r"button \{\s*min-height: 44px;")

    def test_root_url_and_endpoint_are_unchanged_from_the_placeholder(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        with self.app.test_request_context():
            from flask import url_for

            self.assertEqual(url_for("home.index"), "/")
        self.assertEqual(self.submit("made").status_code, 200)

    def test_skip_full_week_does_not_cross_sunday(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        for offset in range(1, 5):
            self.seed_meal(f"Busy {offset}", TODAY + datetime.timedelta(days=offset))
        response = self.submit("skip")
        self.assertIn("No open night left this week", response.get_data(as_text=True))
        self.assertEqual(
            self.meals(),
            [("2026-11-03", "skipped")]
            + [(f"2026-11-0{n}", "planned") for n in range(4, 8)],
        )

    def test_repeated_skip_and_stale_meal_do_not_write(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        self.submit("skip")
        before = self.meals()
        replay = self.submit("skip", meal_id="1")
        self.assertNotIn("No open night left", replay.get_data(as_text=True))
        self.assertEqual(self.meals(), before)
        self.seed_meal("Replacement", TODAY)
        body = self.page()
        self.assertIn("Replacement", body)
        self.assertIn('value="made">Made it</button>', body)
        response = self.submit("made", meal_id="999")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.meals(),
            [
                ("2026-11-03", "skipped"),
                ("2026-11-03", "planned"),
                ("2026-11-04", "planned"),
            ],
        )

    def test_planned_meal_precedes_completed_meal_in_either_insertion_order(
        self,
    ) -> None:
        for status in ("made", "skipped"):
            for planned_first in (False, True):
                with self.subTest(status=status, planned_first=planned_first):
                    with db.transaction(self.conn):
                        self.conn.execute("DELETE FROM planned_meal")
                    planned_title = f"Planned {status} {planned_first}"
                    completed_title = f"Completed {status} {planned_first}"
                    meals = [(planned_title, "planned"), (completed_title, status)]
                    if not planned_first:
                        meals.reverse()
                    for title, meal_status in meals:
                        self.seed_meal(title, TODAY, status=meal_status)

                    body = self.page()

                    self.assertIn(f"<h2>{planned_title}</h2>", body)
                    self.assertIn('value="made">Made it</button>', body)

    def test_status_actions_preserve_shopping_list_and_confirmation(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        with db.transaction(self.conn):
            self.conn.execute("UPDATE meal_plan SET confirmed_at='saved'")
            self.conn.execute("INSERT INTO shopping_list (created_at) VALUES ('saved')")
            self.conn.execute(
                "INSERT INTO list_item (list_id,free_text,source,checked,added_at) VALUES (1,'keep','plan',1,'saved')"
            )
        for action in ("made", "skip"):
            with self.subTest(action=action):
                with db.transaction(self.conn):
                    self.conn.execute(
                        "UPDATE planned_meal SET status='planned' WHERE date=?",
                        (TODAY.isoformat(),),
                    )
                self.assertEqual(self.submit(action).status_code, 200)
                self.assertEqual(
                    [
                        tuple(r)
                        for r in self.conn.execute(
                            "SELECT free_text,checked FROM list_item"
                        )
                    ],
                    [("keep", 1)],
                )
                self.assertEqual(
                    self.conn.execute("SELECT confirmed_at FROM meal_plan").fetchone()[
                        0
                    ],
                    "saved",
                )

    def test_invalid_action_and_csrf_do_not_write(self) -> None:
        self.seed_meal("Leek Pie", TODAY)
        self.assertEqual(self.submit("unknown").status_code, 400)
        self.assertEqual(
            self.client.post("/", data={"action": "made", "meal_id": "1"}).status_code,
            400,
        )
        self.assertEqual(self.meals(), [("2026-11-03", "planned")])
