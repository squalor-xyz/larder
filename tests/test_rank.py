# SPDX-License-Identifier: AGPL-3.0-only
import sqlite3
import unittest
from datetime import date, timedelta
from importlib import import_module

from larder import db, planner_db


class PlannerLoadersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:", autocommit=True)
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        db.migrate(self.conn)
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO recipe_source (id, kind, location) VALUES (1, 'test', '')"
            )
            self.conn.execute(
                "INSERT INTO recipe "
                "(id, source_id, slug, title, source_ref, content_hash, body) "
                "VALUES (1, 1, 'soup', 'Soup', '', '', '')"
            )
            self.conn.execute(
                "INSERT INTO recipe_meta (recipe_id, favorite, effort) "
                "VALUES (1, 1, 'project')"
            )
            self.conn.execute(
                "INSERT INTO meal_plan (id, week_start) VALUES (1, '2026-11-08')"
            )

    def test_rotation_candidates_includes_null_and_dinner_meal_types(self) -> None:
        for meal_type in (None, "dinner"):
            with self.subTest(meal_type=meal_type):
                with db.transaction(self.conn):
                    self.conn.execute(
                        "UPDATE recipe_meta SET meal_type=?", (meal_type,)
                    )
                self.assertEqual(
                    [dict(row) for row in planner_db.rotation_candidates(self.conn)],
                    [
                        {
                            "recipe_id": 1,
                            "title": "Soup",
                            "favorite": 1,
                            "effort": "project",
                        }
                    ],
                )

    def test_rotation_candidates_excludes_other_meal_types(self) -> None:
        for meal_type in ("breakfast", "lunch"):
            with self.subTest(meal_type=meal_type):
                with db.transaction(self.conn):
                    self.conn.execute(
                        "UPDATE recipe_meta SET meal_type=?", (meal_type,)
                    )
                self.assertEqual(planner_db.rotation_candidates(self.conn), [])

    def test_rotation_candidates_excludes_out_of_rotation(self) -> None:
        with db.transaction(self.conn):
            self.conn.execute("UPDATE recipe_meta SET in_rotation=0")
        self.assertEqual(planner_db.rotation_candidates(self.conn), [])

    def test_rotation_candidates_excludes_archived(self) -> None:
        with db.transaction(self.conn):
            self.conn.execute("UPDATE recipe SET archived=1")
        self.assertEqual(planner_db.rotation_candidates(self.conn), [])

    def test_meal_history_includes_made_and_planned_dates(self) -> None:
        for status in ("made", "planned"):
            with self.subTest(status=status):
                with db.transaction(self.conn):
                    self.conn.execute("DELETE FROM planned_meal")
                    self.conn.execute(
                        "INSERT INTO planned_meal (meal_plan_id, date, recipe_id, status) "
                        "VALUES (1, '2026-11-09', 1, ?)",
                        (status,),
                    )
                self.assertEqual(
                    [dict(row) for row in planner_db.meal_history(self.conn)],
                    [{"recipe_id": 1, "date": "2026-11-09"}],
                )

    def test_meal_history_excludes_skipped_dates(self) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO planned_meal (meal_plan_id, date, recipe_id, status) "
                "VALUES (1, '2026-11-09', 1, 'skipped')"
            )
        self.assertEqual(planner_db.meal_history(self.conn), [])


SUNDAY = date(2026, 11, 8)


class RankTest(unittest.TestCase):
    def setUp(self) -> None:
        self.r = import_module("larder.planner.rank")

    def candidates(self, n: int = 8) -> tuple:
        return tuple(self.r.Candidate(i, f"Meal {i}") for i in range(1, n + 1))

    def week(self, candidates=None, history=(), rules=None, seed=0) -> tuple:
        return self.r.suggest_week(
            SUNDAY,
            rules or self.r.Rules(),
            history,
            candidates=self.candidates() if candidates is None else candidates,
            seed=self.r.Seed(seed),
        )

    def test_excludes_meals_made_within_min_repeat_days(self) -> None:
        history = (self.r.History(1, SUNDAY - timedelta(days=1)),)
        candidates = (
            self.r.Candidate(1, "Recent favorite", favorite=True),
        ) + self.candidates()[1:]
        self.assertNotIn(
            1,
            [
                p.recipe_id
                for p in self.week(
                    candidates, history, self.r.Rules(favorite_weight=1000)
                )
            ],
        )

    def test_prefers_least_recently_made(self) -> None:
        history = tuple(
            self.r.History(i, SUNDAY - timedelta(days=i * 20)) for i in (1, 2)
        )
        picks = self.week(self.candidates(2), history, self.r.Rules(nights=1))
        self.assertEqual(picks[0].recipe_id, 2)

    def test_favorite_weighting(self) -> None:
        plain = self.candidates(2)
        favorite = (plain[0], self.r.Candidate(2, "Meal 2", favorite=True))
        history = (
            self.r.History(1, SUNDAY - timedelta(days=30)),
            self.r.History(2, SUNDAY - timedelta(days=25)),
        )
        rules = self.r.Rules(nights=1)
        self.assertEqual(
            (
                self.week(plain, history, rules)[0].recipe_id,
                self.week(favorite, history, rules)[0].recipe_id,
            ),
            (1, 2),
        )

    def test_effort_balance_rules(self) -> None:
        candidates = (
            tuple(
                self.r.Candidate(i, str(i), favorite=True, effort="project")
                for i in (1, 2)
            )
            + self.candidates(8)[2:]
        )
        picks = self.week(candidates)
        projects = [p for p in picks if p.recipe_id in (1, 2)]
        self.assertEqual(
            [(p.day.weekday(), p.recipe_id in (1, 2)) for p in projects], [(6, True)]
        )

    def test_project_can_be_suggested_on_saturday(self) -> None:
        candidates = (
            self.r.Candidate(1, "Project", effort="project"),
        ) + self.candidates(7)[1:]
        history = (self.r.History(1, SUNDAY - timedelta(days=5)),)
        self.assertEqual(self.week(candidates, history)[6].recipe_id, 1)

    def test_deterministic_with_seed(self) -> None:
        self.assertEqual(self.week(seed=31), self.week(seed=31))

    def test_candidate_input_order_does_not_change_ties(self) -> None:
        candidates = self.candidates()
        self.assertEqual(self.week(candidates), self.week(tuple(reversed(candidates))))

    def test_different_seeds_change_ties(self) -> None:
        outputs = {
            tuple(p.recipe_id for p in self.week(seed=seed)) for seed in range(5)
        }
        self.assertGreater(len(outputs), 1)

    def test_fills_week_with_fewer_rotation_meals_than_nights(self) -> None:
        picks = self.week(self.candidates(3))
        self.assertEqual(
            sorted(p.recipe_id for p in picks if p.recipe_id is not None), [1, 2, 3]
        )

    def test_empty_slots_have_dates(self) -> None:
        self.assertEqual(
            self.week(()),
            tuple(self.r.Pick(SUNDAY + timedelta(days=i), None) for i in range(7)),
        )

    def test_fallback_reuses_least_recent_meal(self) -> None:
        history = (
            self.r.History(1, SUNDAY - timedelta(days=1)),
            self.r.History(2, SUNDAY - timedelta(days=5)),
        )
        candidates = (
            self.r.Candidate(1, "Favorite", favorite=True),
            self.r.Candidate(2, "Older"),
        )
        self.assertEqual(
            self.week(candidates, history, self.r.Rules(nights=1))[0].recipe_id, 2
        )

    def test_repeat_boundary_is_eligible(self) -> None:
        history = (
            self.r.History(1, SUNDAY - timedelta(days=10)),
            self.r.History(2, SUNDAY - timedelta(days=11)),
        )
        candidates = (
            self.r.Candidate(1, "Favorite", favorite=True),
            self.r.Candidate(2, "Other"),
        )
        self.assertEqual(
            self.week(candidates, history, self.r.Rules(nights=1))[0].recipe_id, 1
        )

    def test_future_planned_dates_respect_gap(self) -> None:
        history = (self.r.History(1, SUNDAY + timedelta(days=1)),)
        candidates = (
            self.r.Candidate(1, "Future favorite", favorite=True),
        ) + self.candidates()[1:]
        self.assertNotIn(
            1,
            [
                p.recipe_id
                for p in self.week(
                    candidates, history, self.r.Rules(favorite_weight=1000)
                )
            ],
        )

    def test_all_history_events_are_checked(self) -> None:
        history = (
            self.r.History(1, SUNDAY - timedelta(days=1)),
            self.r.History(1, SUNDAY - timedelta(days=40)),
        )
        candidates = (
            self.r.Candidate(1, "Recent favorite", favorite=True),
        ) + self.candidates()[1:]
        self.assertNotIn(
            1,
            [
                p.recipe_id
                for p in self.week(
                    candidates, history, self.r.Rules(favorite_weight=1000)
                )
            ],
        )

    def test_configured_nights(self) -> None:
        self.assertEqual(
            [p.day for p in self.week(rules=self.r.Rules(nights=3))],
            [SUNDAY + timedelta(days=i) for i in range(3)],
        )

    def test_week_starts_on_sunday(self) -> None:
        with self.assertRaisesRegex(ValueError, "Sunday"):
            self.r.suggest_week(
                SUNDAY + timedelta(days=1), self.r.Rules(), (), candidates=()
            )

    def test_score_weights_are_configurable(self) -> None:
        candidate = self.r.Candidate(1, "Quick", favorite=True, effort="quick")
        history = (self.r.History(1, SUNDAY - timedelta(days=20)),)
        rules = self.r.Rules(recency_weight=2, favorite_weight=9, effort_weight=3)
        self.assertEqual(
            self.r.score(candidate, SUNDAY + timedelta(days=1), rules, history), 54
        )

    def test_never_made_score_and_recency_cap(self) -> None:
        candidate = self.r.Candidate(1, "Meal")
        history = (self.r.History(1, SUNDAY - timedelta(days=500)),)
        self.assertEqual(
            (
                self.r.score(candidate, SUNDAY, self.r.Rules(recency_cap=100), ()),
                self.r.score(candidate, SUNDAY, self.r.Rules(recency_cap=100), history),
            ),
            (100, 100),
        )

    def test_effort_score_depends_on_night(self) -> None:
        quick = self.r.Candidate(1, "Quick", effort="quick")
        project = self.r.Candidate(2, "Project", effort="project")
        rules = self.r.Rules(recency_weight=0)
        self.assertEqual(
            tuple(
                self.r.score(c, day, rules, ())
                for c in (quick, project)
                for day in (SUNDAY, SUNDAY + timedelta(days=1))
            ),
            (0, 1, 1, 0),
        )

    def test_alternatives_respect_all_week_picks_and_limit(self) -> None:
        week = (
            self.r.Pick(SUNDAY, 1),
            self.r.Pick(SUNDAY + timedelta(days=1), 2, locked=True),
        )
        result = self.r.alternatives(
            week,
            SUNDAY,
            2,
            candidates=self.candidates(5),
            rules=self.r.Rules(),
            history=(),
        )
        self.assertEqual(
            (len(result), {c.recipe_id for c in result} <= {3, 4, 5}), (2, True)
        )

    def test_alternatives_does_not_swap_locked_target(self) -> None:
        result = self.r.alternatives(
            (self.r.Pick(SUNDAY, 1, locked=True),),
            SUNDAY,
            3,
            candidates=self.candidates(),
            rules=self.r.Rules(),
            history=(),
        )
        self.assertEqual(result, ())

    def test_alternatives_respects_existing_project(self) -> None:
        candidates = (
            self.r.Candidate(1, "Project", effort="project"),
            self.r.Candidate(2, "Other project", effort="project"),
            self.r.Candidate(3, "Soup"),
        )
        week = (
            self.r.Pick(SUNDAY, 1, locked=True),
            self.r.Pick(SUNDAY + timedelta(days=6), None),
        )
        result = self.r.alternatives(
            week,
            SUNDAY + timedelta(days=6),
            5,
            candidates=candidates,
            rules=self.r.Rules(),
            history=(),
        )
        self.assertEqual([c.recipe_id for c in result], [3])

    def test_alternatives_excludes_weekday_projects(self) -> None:
        day = SUNDAY + timedelta(days=1)
        result = self.r.alternatives(
            (self.r.Pick(day, None),),
            day,
            5,
            candidates=(self.r.Candidate(1, "Project", effort="project"),),
            rules=self.r.Rules(),
            history=(),
        )
        self.assertEqual(result, ())

    def test_alternatives_prefers_gap_eligible_before_fallback(self) -> None:
        history = (self.r.History(1, SUNDAY - timedelta(days=1)),)
        candidates = (
            self.r.Candidate(1, "Recent favorite", favorite=True),
            self.r.Candidate(2, "Other"),
        )
        result = self.r.alternatives(
            (self.r.Pick(SUNDAY, None),),
            SUNDAY,
            2,
            candidates=candidates,
            rules=self.r.Rules(favorite_weight=1000),
            history=history,
        )
        self.assertEqual([c.recipe_id for c in result], [2, 1])

    def test_loader_output_can_feed_ranker(self) -> None:
        fixture = PlannerLoadersTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with db.transaction(fixture.conn):
            fixture.conn.execute(
                "INSERT INTO recipe "
                "(id, source_id, slug, title, source_ref, content_hash, body) "
                "VALUES (2, 1, 'pasta', 'Pasta', '', '', '')"
            )
            fixture.conn.execute("INSERT INTO recipe_meta (recipe_id) VALUES (2)")
            fixture.conn.execute(
                "INSERT INTO planned_meal (meal_plan_id, date, recipe_id, status) "
                "VALUES (1, '2026-11-07', 1, 'made')"
            )
        candidates = tuple(
            self.r.Candidate(**dict(row))
            for row in planner_db.rotation_candidates(fixture.conn)
        )
        history = tuple(
            self.r.History(row["recipe_id"], date.fromisoformat(row["date"]))
            for row in planner_db.meal_history(fixture.conn)
        )
        self.assertEqual(
            self.week(
                candidates, history, self.r.Rules(nights=1, favorite_weight=1000)
            )[0].recipe_id,
            2,
        )

    def test_alternatives_rejects_night_outside_week(self) -> None:
        with self.assertRaisesRegex(ValueError, "night is not in week"):
            self.r.alternatives(
                (self.r.Pick(SUNDAY, 1),),
                SUNDAY + timedelta(days=1),
                3,
                candidates=self.candidates(),
                rules=self.r.Rules(),
                history=(),
            )

    def test_alternatives_counts_project_outside_candidates(self) -> None:
        saturday = SUNDAY + timedelta(days=6)
        week = (
            self.r.Pick(SUNDAY, 99, locked=True, effort="project"),
            self.r.Pick(saturday, None),
        )
        result = self.r.alternatives(
            week,
            saturday,
            5,
            candidates=(
                self.r.Candidate(2, "Project", effort="project"),
                self.r.Candidate(3, "Soup"),
            ),
            rules=self.r.Rules(),
            history=(),
        )
        self.assertEqual([candidate.recipe_id for candidate in result], [3])
