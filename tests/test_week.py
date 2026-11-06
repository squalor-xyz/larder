# SPDX-License-Identifier: AGPL-3.0-only
import datetime
import re
from unittest import mock
from zoneinfo import ZoneInfo

from flask import url_for

from larder import db, planner_db
from larder.web import create_app
from tests.test_web_shell import WebShellTestCase

SUNDAY = datetime.date(2026, 11, 1)
TODAY = SUNDAY + datetime.timedelta(days=2)


class WeekTest(WebShellTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.clock = mock.patch("larder.web.home_views.today", return_value=TODAY)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.app = create_app(self.settings())
        self.client = self.paired_client(self.app, "Sam")
        self.conn = db.connect(self.settings())
        self.addCleanup(self.conn.close)

    def seed(self, day: datetime.date, title: str = "Leek Pie") -> int:
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT OR IGNORE INTO recipe_source (kind,location) VALUES ('test','test')"
            )
            start = day - datetime.timedelta(days=(day.weekday() + 1) % 7)
            self.conn.execute(
                "INSERT OR IGNORE INTO meal_plan (week_start) VALUES (?)",
                (start.isoformat(),),
            )
            recipe_id = self.conn.execute(
                "INSERT INTO recipe (source_id,slug,title,source_ref,content_hash,body) VALUES (1,?,?,'','','') RETURNING id",
                (title, title),
            ).fetchone()[0]
            self.conn.execute(
                "INSERT INTO planned_meal (meal_plan_id,date,recipe_id) VALUES ((SELECT id FROM meal_plan WHERE week_start=?),?,?)",
                (start.isoformat(), day.isoformat(), recipe_id),
            )
        return recipe_id

    def page(self) -> str:
        return self.client.get("/week").get_data(as_text=True)

    def test_week_shows_seven_dates_with_meal_titles(self) -> None:
        self.seed(SUNDAY + datetime.timedelta(days=6), "Saturday")
        self.seed(SUNDAY, "First")
        self.seed(SUNDAY, "Second")
        self.seed(SUNDAY - datetime.timedelta(days=1), "Previous")
        self.seed(SUNDAY + datetime.timedelta(days=7), "Next")
        self.assertEqual(
            [
                row["title"]
                for row in planner_db.week_meals(self.conn, SUNDAY.isoformat())
            ],
            ["First", "Second", "Saturday"],
        )
        body = self.page()
        self.assertEqual(
            (
                re.findall(r'datetime="([0-9-]+)"', body),
                re.findall(r'href="/recipes/[0-9]+">([^<]+)</a>', body),
            ),
            (
                [(SUNDAY + datetime.timedelta(days=n)).isoformat() for n in range(7)],
                ["First", "Second", "Saturday"],
            ),
        )

    def test_night_without_a_meal_is_shown_empty(self) -> None:
        self.seed(SUNDAY)
        body = self.page()
        nights = re.findall(r'<li class="plan-night">(.*?)</li>', body, re.DOTALL)
        self.assertEqual(
            ["Open night" in night for night in nights], [False] + [True] * 6
        )

    def test_skipped_meal_is_labelled(self) -> None:
        recipe_id = self.seed(TODAY)
        meal_id = self.conn.execute(
            "SELECT id FROM planned_meal WHERE recipe_id=?", (recipe_id,)
        ).fetchone()[0]
        planner_db.finish_tonight(self.conn, TODAY, meal_id, "skip")
        paragraphs = re.findall(r"<p>(.*?)</p>", self.page(), re.DOTALL)
        self.assertEqual(
            [
                ("Leek Pie", '<span class="tag">Skipped</span>' in paragraph)
                for paragraph in paragraphs
                if ">Leek Pie</a>" in paragraph
            ],
            [("Leek Pie", True), ("Leek Pie", False)],
        )

    def test_sunday_starts_the_current_week(self) -> None:
        with mock.patch("larder.web.home_views.today", return_value=SUNDAY):
            dates = re.findall(r'datetime="([0-9-]+)"', self.page())
        self.assertEqual(dates[:1], [SUNDAY.isoformat()])

    def test_meal_title_links_to_the_recipe_view(self) -> None:
        recipe_id = self.seed(TODAY)
        with self.app.test_request_context():
            target = url_for("recipes.recipe_view", recipe_id=recipe_id)
        self.assertIn(f'href="{target}">Leek Pie</a>', self.page())

    def test_no_plan_shows_a_friendly_message(self) -> None:
        self.assertIn("Nothing planned for this week", self.page())

    def test_member_can_open_the_week(self) -> None:
        self.assertEqual(self.client.get("/week").status_code, 200)

    def test_page_has_no_forms_or_controls(self) -> None:
        self.seed(TODAY)
        body = self.page()
        self.assertEqual(
            (
                "Leek Pie" in body,
                re.findall(r"<(?:form|button|input|select|textarea)\b", body),
            ),
            (True, []),
        )

    def test_post_is_not_allowed(self) -> None:
        with self.client.session_transaction() as session:
            session["csrf"] = "test-token"
        self.assertEqual(
            self.client.post("/week", data={"csrf_token": "test-token"}).status_code,
            405,
        )

    def test_unpaired_device_gets_401(self) -> None:
        self.assertEqual(
            (
                self.client.get("/week").status_code,
                self.app.test_client().get("/week").status_code,
            ),
            (200, 401),
        )

    def test_header_has_a_week_link_for_paired_people(self) -> None:
        for person in ("Ada", "Sam"):
            with self.subTest(person=person):
                body = (
                    self.paired_client(self.app, person).get("/").get_data(as_text=True)
                )
                self.assertIn('href="/week">Week</a>', body.split("</header>")[0])

    def test_draft_is_labelled_until_confirmed(self) -> None:
        self.seed(TODAY)
        self.assertIn("Draft, not confirmed yet", self.page())
        with db.transaction(self.conn):
            self.conn.execute("UPDATE meal_plan SET confirmed_at='saved'")
        self.assertNotIn("Draft, not confirmed yet", self.page())

    def test_week_uses_household_timezone(self) -> None:
        self.clock.stop()
        self.write_config('timezone = "America/Los_Angeles"\n')
        app = create_app(self.settings())
        client = self.paired_client(app, "Sam")
        self.seed(SUNDAY + datetime.timedelta(days=7), "Current")
        self.seed(SUNDAY + datetime.timedelta(days=14), "Next")
        instant = datetime.datetime(2026, 11, 15, 1, tzinfo=datetime.UTC)
        with mock.patch("larder.web.home_views.datetime") as clock:
            clock.datetime.now.side_effect = lambda zone: instant.astimezone(
                zone or datetime.UTC
            )
            body = client.get("/week").get_data(as_text=True)
        self.assertEqual(
            re.findall(r'href="/recipes/[0-9]+">([^<]+)</a>', body), ["Current"]
        )

    def test_week_uses_host_date_when_timezone_unset(self) -> None:
        self.clock.stop()
        self.seed(SUNDAY + datetime.timedelta(days=7), "Current")
        self.seed(SUNDAY + datetime.timedelta(days=14), "Next")
        instant = datetime.datetime(2026, 11, 15, 1, tzinfo=datetime.UTC)

        def local_now(zone):
            return instant.astimezone(zone or ZoneInfo("America/Los_Angeles"))

        with mock.patch("larder.web.home_views.datetime") as clock:
            clock.datetime.now.side_effect = local_now
            body = self.page()
        self.assertEqual(
            re.findall(r'href="/recipes/[0-9]+">([^<]+)</a>', body), ["Current"]
        )
