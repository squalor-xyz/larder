# SPDX-License-Identifier: AGPL-3.0-only
"""Weekly planning through paired member requests and real persisted output."""

import re
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from werkzeug.test import TestResponse

from larder import db, planner_db
from larder.planner.rank import (
    Candidate,
    History,
    Pick,
    Rules,
    alternatives,
    suggest_week,
)
from larder.web import create_app
from tests.test_web_shell import WebShellTestCase

WEEK = date(2026, 11, 8)
PATH = "/plan?week=2026-11-08"


class PlanWebTest(WebShellTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app(self.settings())
        self.app.testing = True
        clock = mock.patch(
            "larder.web.plan_views.today", return_value=date(2026, 11, 5)
        )
        clock.start()
        self.addCleanup(clock.stop)
        self.client = self.paired_client(self.app, "Sam")
        self.conn = db.connect(self.settings())
        self.addCleanup(self.conn.close)
        self.conn.execute(
            "INSERT INTO recipe_source (kind,location) VALUES ('test','plan')"
        )
        for n in range(1, 13):
            self.conn.execute(
                "INSERT INTO recipe (id,source_id,slug,title,source_ref,content_hash,body) VALUES (?,1,?,?, '', '', '')",
                (n, f"meal-{n}", f"Meal {n}"),
            )
            self.conn.execute(
                "INSERT INTO recipe_meta (recipe_id,meal_type) VALUES (?,'dinner')",
                (n,),
            )
        self.candidates = [
            Candidate(**dict(row)) for row in planner_db.rotation_candidates(self.conn)
        ]

    def post(self, action: str, day: date = WEEK, **fields) -> TestResponse:
        with self.client.session_transaction() as session:
            token = session["csrf"]
        return self.client.post(
            PATH,
            data={
                "csrf_token": token,
                "action": action,
                "day": day.isoformat(),
                **fields,
            },
        )

    def meals(self) -> list[tuple]:
        return [
            tuple(row)
            for row in self.conn.execute(
                "SELECT date,recipe_id,status,locked FROM planned_meal ORDER BY date,id"
            )
        ]

    def open_week(self) -> str:
        response = self.client.get(PATH)
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_new_week_prefilled_from_suggestions(self) -> None:
        page = self.open_week()
        expected = suggest_week(WEEK, Rules(), [], candidates=self.candidates)
        self.assertEqual(
            self.meals(),
            [(p.day.isoformat(), p.recipe_id, "planned", 0) for p in expected],
        )
        self.assertEqual(
            re.findall(r'<a href="/recipes/\d+">(Meal \d+)</a>', page),
            [f"Meal {p.recipe_id}" for p in expected],
        )
        self.assertIn('href="/plan"', self.client.get("/").text)
        self.client.get(PATH)
        self.assertEqual(
            self.meals(),
            [(p.day.isoformat(), p.recipe_id, "planned", 0) for p in expected],
        )

    def test_swap_replaces_with_alternative(self) -> None:
        page = self.open_week()
        original = self.meals()
        seen = [original[0][1]]
        for _ in range(2):
            picks = [
                Pick(date.fromisoformat(d), r, bool(lock))
                for d, r, _, lock in self.meals()
            ]
            history = [
                History(row["recipe_id"], date.fromisoformat(row["date"]))
                for row in planner_db.meal_history(self.conn)
                if not WEEK
                <= date.fromisoformat(row["date"])
                <= WEEK + timedelta(days=6)
            ]
            expected = alternatives(
                picks,
                WEEK,
                1,
                candidates=[c for c in self.candidates if c.recipe_id not in seen],
                rules=Rules(),
                history=history,
            )[0].recipe_id
            self.assertIn(f'name="seen" value="{",".join(map(str, seen))}"', page)
            response = self.post("swap", seen=",".join(map(str, seen)))
            self.assertEqual(response.status_code, 303)
            self.assertEqual(
                self.meals()[0], (WEEK.isoformat(), expected, "planned", 0)
            )
            self.assertEqual(self.meals()[1:], original[1:])
            seen.append(expected)
            page = self.client.get(response.location).text

    def test_skip_and_lock_persist(self) -> None:
        self.open_week()
        original = self.meals()
        self.post("skip")
        self.assertEqual(
            self.meals()[0], (WEEK.isoformat(), original[0][1], "skipped", 0)
        )
        self.assertNotIn(
            original[0][1],
            [row["recipe_id"] for row in planner_db.meal_history(self.conn)],
        )
        monday = WEEK + timedelta(days=1)
        self.post("lock", monday)
        self.assertEqual(
            self.meals()[1], (monday.isoformat(), original[1][1], "planned", 1)
        )
        before = self.meals()
        self.post("swap", monday)
        self.assertEqual(self.meals(), before)
        self.assertIn("Locked", self.client.get(PATH).text)
        self.post("lock", monday)
        with self.subTest(check="unlocked"):
            self.assertEqual(
                self.meals()[1], (monday.isoformat(), original[1][1], "planned", 0)
            )
        self.post("swap", monday)
        with self.subTest(check="swap after unlock"):
            self.assertNotEqual(self.meals()[1][1], original[1][1])

    def subscribe_output(self) -> Path:
        from larder.planner import plan

        output = self.data_dir / "subscriber.txt"

        def subscriber(event: str, week_start: date) -> None:
            # A separate connection proves the subscriber sees committed state.
            conn = db.connect(self.settings())
            try:
                confirmed = conn.execute(
                    "SELECT confirmed_at FROM meal_plan WHERE week_start=?",
                    (week_start.isoformat(),),
                ).fetchone()[0]
                rows = list(
                    conn.execute(
                        "SELECT date,recipe_id,status,locked FROM planned_meal ORDER BY date,id"
                    )
                )
                output.write_text(
                    repr(
                        (
                            event,
                            week_start.isoformat(),
                            bool(confirmed),
                            [tuple(r) for r in rows],
                        )
                    )
                )
            finally:
                conn.close()

        patcher = mock.patch.object(plan, "_subscribers", [])
        patcher.start()
        self.addCleanup(patcher.stop)
        plan.subscribe(subscriber)
        return output

    def test_confirm_marks_plan_final_and_triggers_list_generation_hook(self) -> None:
        self.open_week()
        output = self.subscribe_output()
        self.assertEqual(self.post("confirm").status_code, 303)
        confirmed = self.conn.execute("SELECT confirmed_at FROM meal_plan").fetchone()[
            0
        ]
        self.assertRegex(confirmed, r"^\d{4}-\d\d-\d\dT.*\+00:00$")
        self.assertEqual(
            output.read_text(),
            repr(("plan_confirmed", WEEK.isoformat(), True, self.meals())),
        )
        self.assertIn("Confirmed", self.client.get(PATH).text)

    def test_editing_confirmed_plan_updates_list_hook(self) -> None:
        self.open_week()
        output = self.subscribe_output()
        self.post("confirm")
        for action, fields in (
            ("swap", {}),
            ("skip", {}),
            ("lock", {}),
            ("pick", {"recipe_id": "12"}),
            ("open", {}),
        ):
            with self.subTest(action=action):
                output.unlink()
                self.post(action, **fields)
                self.assertEqual(
                    output.read_text(),
                    repr(("plan_confirmed", WEEK.isoformat(), True, self.meals())),
                )

    def test_pick_open_and_confirm_with_any_number_of_open_nights(self) -> None:
        self.open_week()
        self.conn.execute("UPDATE recipe_meta SET in_rotation=0 WHERE recipe_id=12")
        self.post("pick", recipe_id="12")
        self.assertEqual(self.meals()[0], (WEEK.isoformat(), 12, "planned", 0))
        for n in range(3):
            with self.subTest(open_nights=n):
                if n:
                    day = WEEK + timedelta(days=n - 1)
                    self.post("open", day)
                    self.assertNotIn(day.isoformat(), [row[0] for row in self.meals()])
                    self.assertIn("<span>Open night</span>", self.client.get(PATH).text)
                self.assertEqual(self.post("confirm").status_code, 303)
                self.assertIsNotNone(
                    self.conn.execute("SELECT confirmed_at FROM meal_plan").fetchone()[
                        0
                    ]
                )

    def test_week_selection_and_invalid_writes(self) -> None:
        self.open_week()
        from larder.web import plan_views

        with mock.patch.object(plan_views, "today", return_value=date(2026, 11, 5)):
            self.assertIn("2026-11-08", self.client.get("/plan").text)
        with mock.patch.object(plan_views, "today", return_value=WEEK):
            self.assertIn("2026-11-08", self.client.get("/plan").text)
        before = self.meals()
        for fields in (
            {"action": "bad"},
            {"action": "skip", "day": date(2026, 11, 7)},
            {"action": "pick", "recipe_id": "999"},
            {"action": "swap", "seen": "bad"},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(self.post(**fields).status_code, 400)
                self.assertEqual(self.meals(), before)
        # Existing rows must not bypass the Sunday validation.
        self.conn.execute("INSERT INTO meal_plan (week_start) VALUES ('2026-11-09')")
        for week in ("bad", "2026-11-09"):
            self.assertEqual(self.client.get("/plan?week=" + week).status_code, 400)
        self.assertEqual(
            self.client.post(PATH, data={"action": "skip"}).status_code, 400
        )
        self.assertEqual(self.app.test_client().get(PATH).status_code, 401)
        self.assertEqual(self.meals(), before)

    def test_rendered_forms_submit_csrf(self) -> None:
        from tests.test_web_shell import CSRF_FIELD

        page = self.open_week()
        # Submit the token actually shipped to the browser, rather than the
        # session token supplied by our convenience helper.
        tokens = CSRF_FIELD.findall(page)
        for n in range(8):
            with self.subTest(form=n):
                response = self.client.post(
                    PATH,
                    data={
                        "csrf_token": tokens[n],
                        "action": "skip" if n < 7 else "confirm",
                        "day": (WEEK + timedelta(days=min(n, 6))).isoformat(),
                    },
                )
                self.assertEqual(response.status_code, 303)

    def test_past_week_is_not_autofilled(self) -> None:
        from larder.web import plan_views

        before = [tuple(row) for row in planner_db.meal_history(self.conn)]
        with mock.patch.object(plan_views, "today", return_value=date(2026, 11, 5)):
            response = self.client.get("/plan?week=2026-10-04")
        with self.subTest(check="response"):
            self.assertEqual(response.status_code, 200)
        with self.subTest(check="meals"):
            self.assertEqual(self.meals(), [])
        with self.subTest(check="history"):
            self.assertEqual(
                [tuple(row) for row in planner_db.meal_history(self.conn)], before
            )
        with self.subTest(check="no draft"):
            self.assertIsNone(planner_db.week_plan(self.conn, "2026-10-04"))
        with self.subTest(check="read only"):
            self.assertNotIn('<form method="post"', response.text)

    def test_past_week_rejects_writes(self) -> None:
        from larder.web import plan_views

        self.open_week()
        with self.client.session_transaction() as session:
            token = session["csrf"]
        before = self.meals()
        with mock.patch.object(plan_views, "today", return_value=date(2026, 11, 5)):
            for action in ("confirm", "swap", "skip", "lock", "pick", "open"):
                with self.subTest(action=action):
                    response = self.client.post(
                        "/plan?week=2026-10-04",
                        data={
                            "csrf_token": token,
                            "action": action,
                            "day": "2026-10-04",
                            "recipe_id": "12",
                        },
                    )
                    with self.subTest(check="response"):
                        self.assertEqual(response.status_code, 400)
                    with self.subTest(check="unchanged"):
                        self.assertEqual(self.meals(), before)

    def test_ended_week_shows_stored_meals_read_only(self) -> None:
        from larder.web import plan_views

        self.open_week()
        before = self.meals()
        # Saturday is still in the week; Sunday makes it read only.
        for today, editable in (
            (date(2026, 11, 14), True),
            (date(2026, 11, 15), False),
        ):
            with self.subTest(today=today):
                with mock.patch.object(plan_views, "today", return_value=today):
                    response = self.client.get(PATH)
                with self.subTest(check="meals shown"):
                    self.assertEqual(
                        re.findall(
                            r'<a href="/recipes/\d+">(Meal \d+)</a>', response.text
                        ),
                        [f"Meal {row[1]}" for row in before],
                    )
                with self.subTest(check="controls"):
                    self.assertEqual('<form method="post"' in response.text, editable)
                with self.subTest(check="unchanged"):
                    self.assertEqual(self.meals(), before)
