# SPDX-License-Identifier: AGPL-3.0-only
"""Generation assertions inspect persisted grocery rows, including real plan hooks."""

import sqlite3
import unittest
from datetime import date, timedelta

from larder import db
from larder.ingredients.match import seed_items

TODAY = date(2026, 11, 5)


class ListGenerateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:", autocommit=True)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.conn.close)
        db.migrate(self.conn)
        seed_items(self.conn)
        self.conn.execute(
            "INSERT INTO recipe_source (kind,location) VALUES ('test','test')"
        )
        for n in (1, 2):
            self.conn.execute(
                "INSERT INTO recipe (id,source_id,slug,title,source_ref,content_hash,body) VALUES (?,1,?,?, '', '', '')",
                (n, str(n), str(n)),
            )
        self.conn.execute(
            "INSERT INTO item (id,canonical_name) VALUES (9001,'test food')"
        )
        self.conn.execute(
            "INSERT INTO meal_plan (id,week_start,confirmed_at) VALUES (1,'2026-11-01','confirmed')"
        )
        for n in (1, 2):
            self.conn.execute(
                "INSERT INTO planned_meal (meal_plan_id,date,recipe_id) VALUES (1,?,?)",
                (f"2026-11-0{n}", n),
            )

    def line(
        self,
        recipe: int = 1,
        qty: float | None = 1,
        unit: str | None = "can",
        **fields: object,
    ) -> int:
        unit_id = (
            self.conn.execute("SELECT id FROM unit WHERE name=?", (unit,)).fetchone()[0]
            if unit
            else None
        )
        values = {
            "recipe_id": recipe,
            "position": self.conn.execute(
                "SELECT count(*) FROM ingredient_line WHERE recipe_id=?", (recipe,)
            ).fetchone()[0],
            "raw_text": "test food",
            "qty": qty,
            "unit_id": unit_id,
            "item_id": 9001,
            "confidence": 1,
        }
        values.update(fields)
        return self.conn.execute(
            f"INSERT INTO ingredient_line ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        ).lastrowid

    def generate(self) -> list[str]:
        from larder.shopping.generate import regenerate

        return regenerate(self.conn, today=TODAY)

    def rows(self) -> list[tuple]:
        return [
            tuple(r)
            for r in self.conn.execute(
                "SELECT l.item_id,l.free_text,l.qty,u.name,l.source,l.checked FROM list_item l LEFT JOIN unit u ON u.id=l.unit_id ORDER BY l.id"
            )
        ]

    def test_items_from_plan_aggregated_across_recipes(self) -> None:
        self.line(qty=2)
        self.line(recipe=2, qty=3)
        self.generate()
        self.assertEqual(self.rows(), [(9001, None, 5, "can", "plan", 0)])

    def test_compatible_units_converted_and_summed(self) -> None:
        for a, b, x, y, expected in [
            ("tbsp", "cup", 2, 1, 1.125),
            ("ml", "tsp", 5, 1, 2.01442068105529),
            ("ml", "l", 500, 1, 1.5),
            ("oz", "lb", 8, 1, 1.5),
            ("g", "kg", 500, 1, 1.5),
            ("g", "oz", 1, 1, 1.0352739619495803),
        ]:
            with self.subTest(units=(a, b)):
                self.conn.execute("DELETE FROM ingredient_line")
                self.line(unit=a, qty=x)
                self.line(recipe=2, unit=b, qty=y)
                self.generate()
                self.assertEqual(self.rows(), [(9001, None, expected, b, "plan", 0)])

    def test_incompatible_units_kept_separate(self) -> None:
        for unit in ("cup", "g", "can", "jar", None):
            self.line(unit=unit)
        self.generate()
        self.assertEqual(
            self.rows(),
            [(9001, None, 1, u, "plan", 0) for u in ("cup", "g", "can", "jar", None)],
        )

    def test_optional_and_non_food_lines_excluded(self) -> None:
        for field, value in [("optional", 1), ("is_food", 0)]:
            with self.subTest(field=field):
                self.conn.execute("DELETE FROM ingredient_line")
                self.line(**{field: value})
                self.generate()
                self.assertEqual(self.rows(), [])

    def test_staple_lines_excluded(self) -> None:
        self.line()
        self.conn.execute("INSERT INTO staple (item_id) VALUES (9001)")
        self.generate()
        self.assertEqual(self.rows(), [])

    def test_regenerate_preserves_manual_and_staple_items_and_checks(self) -> None:
        self.line()
        self.generate()
        self.conn.execute("UPDATE list_item SET checked=1")
        for source in ("manual", "staple", "runout"):
            self.conn.execute(
                "INSERT INTO list_item (list_id,free_text,source,checked,added_at) SELECT id,?,?,1,'old' FROM shopping_list",
                (source, source),
            )
        self.generate()
        self.assertEqual(
            sorted(self.rows(), key=lambda r: r[4]),
            sorted(
                [(9001, None, 1, "can", "plan", 1)]
                + [(None, s, None, None, s, 1) for s in ("manual", "staple", "runout")],
                key=lambda r: r[4],
            ),
        )

    def test_converted_quantity_keeps_check_on_repeat(self) -> None:
        self.line(qty=1, unit="tsp")
        self.line(recipe=2, qty=5, unit="ml")
        self.generate()
        self.conn.execute("UPDATE list_item SET checked=1")
        self.generate()
        self.assertEqual(
            self.rows(), [(9001, None, 2.01442068105529, "tsp", "plan", 1)]
        )

    def test_category_order_and_free_text_last(self) -> None:
        from larder import shopping_db

        self.line()
        self.line(item_id=None, raw_text="AAA unknown")
        self.conn.execute(
            "INSERT INTO item (id,canonical_name,category) SELECT 9002,'AAA known',name FROM category ORDER BY sort_order LIMIT 1 OFFSET 1"
        )
        self.line(item_id=9002)
        self.conn.execute(
            "UPDATE item SET category=(SELECT name FROM category ORDER BY sort_order LIMIT 1) WHERE id=9001"
        )
        self.generate()
        self.assertEqual(
            [row["item_id"] for row in shopping_db.list_items(self.conn, 1)],
            [9001, 9002, None],
        )

    def test_changed_quantity_unchecks(self) -> None:
        self.line()
        self.generate()
        self.conn.execute("UPDATE list_item SET checked=1")
        self.conn.execute("UPDATE ingredient_line SET qty=2")
        self.generate()
        self.assertEqual(self.rows(), [(9001, None, 2, "can", "plan", 0)])

    def test_unresolved_lines_included_as_raw_text_with_warning(self) -> None:
        self.line(item_id=None, raw_text="a little mystery")
        warnings = self.generate()
        self.assertEqual(
            (self.rows(), warnings),
            (
                [(None, "a little mystery", 1, "can", "plan", 0)],
                ["Unresolved ingredient: a little mystery"],
            ),
        )

    def test_range_quantity_follows_adr_0020(self) -> None:
        self.line(qty=2, qty_max=3)
        self.generate()
        self.assertEqual(self.rows(), [(9001, None, 3, "can", "plan", 0)])

    def test_alternatives_follow_adr_0020(self) -> None:
        line = self.line(qty=2)
        self.conn.execute(
            "INSERT INTO ingredient_line_alt (line_id,position,alt_text) VALUES (?,1,'other food')",
            (line,),
        )
        self.conn.execute(
            "INSERT INTO ingredient_line_alt (line_id,position,alt_text) VALUES (?,2,'third food')",
            (line,),
        )
        self.generate()
        self.assertEqual(self.rows(), [(9001, None, 2, "can", "plan", 0)])

    def test_only_confirmed_unended_weeks_and_planned_meals(self) -> None:
        self.line()
        for column, value in [("confirmed_at", None), ("week_start", "2026-10-25")]:
            with self.subTest(column=column):
                self.conn.execute(
                    "UPDATE meal_plan SET week_start='2026-11-01',confirmed_at='yes'"
                )
                self.conn.execute(f"UPDATE meal_plan SET {column}=?", (value,))
                self.generate()
                self.assertEqual(self.rows(), [])
        self.conn.execute(
            "UPDATE meal_plan SET week_start='2026-11-01',confirmed_at='yes'"
        )
        for status in ("skipped", "made"):
            with self.subTest(status=status):
                self.conn.execute("UPDATE planned_meal SET status=?", (status,))
                self.generate()
                self.assertEqual(self.rows(), [])

    def test_future_confirmed_week_is_added_to_current_week(self) -> None:
        self.line()
        self.conn.execute(
            "INSERT INTO meal_plan (id,week_start,confirmed_at) VALUES (2,'2026-11-08','yes')"
        )
        self.conn.execute(
            "INSERT INTO planned_meal (meal_plan_id,date,recipe_id) VALUES (2,'2026-11-08',1)"
        )
        self.generate()
        self.assertEqual(self.rows(), [(9001, None, 2, "can", "plan", 0)])

    def test_saturday_is_still_active(self) -> None:
        self.line()
        from larder.shopping.generate import regenerate

        regenerate(self.conn, today=date(2026, 11, 7))
        self.assertEqual(self.rows(), [(9001, None, 1, "can", "plan", 0)])

    def test_low_confidence_match_keeps_raw_text(self) -> None:
        self.line(confidence=0.5, raw_text="maybe test food")
        warnings = self.generate()
        self.assertEqual(
            (self.rows(), warnings),
            (
                [(None, "maybe test food", 1, "can", "plan", 0)],
                ["Unresolved ingredient: maybe test food"],
            ),
        )

    def test_low_confidence_staple_match_kept_as_raw_text(self) -> None:
        self.line(confidence=0.5, raw_text="olive oil spray")
        self.conn.execute("INSERT INTO staple (item_id) VALUES (9001)")
        warnings = self.generate()
        self.assertEqual(
            (self.rows(), warnings),
            (
                [(None, "olive oil spray", 1, "can", "plan", 0)],
                ["Unresolved ingredient: olive oil spray"],
            ),
        )

    def test_confirmed_parse_uses_match_despite_confidence(self) -> None:
        self.line(confidence=0.5, confirmed=1)
        self.generate()
        self.assertEqual(self.rows(), [(9001, None, 1, "can", "plan", 0)])

    def test_provenance_and_cascade(self) -> None:
        self.line()
        self.line(recipe=2)
        self.line(recipe=2)
        self.generate()
        self.assertEqual(
            [
                tuple(r)
                for r in self.conn.execute(
                    "SELECT recipe_id FROM list_item_recipe ORDER BY recipe_id"
                )
            ],
            [(1,), (2,)],
        )
        self.conn.execute("DELETE FROM list_item")
        self.assertEqual(list(self.conn.execute("SELECT * FROM list_item_recipe")), [])

    def test_unknown_quantities_stay_unknown(self) -> None:
        self.line(qty=None)
        self.line(recipe=2, qty=2)
        self.generate()
        self.assertEqual(self.rows(), [(9001, None, None, "can", "plan", 0)])

    def test_ended_week_removes_obsolete_plan_items(self) -> None:
        self.line()
        self.generate()
        self.conn.execute("UPDATE meal_plan SET week_start='2026-10-25'")
        self.generate()
        self.assertEqual(self.rows(), [])


class ListHookTest(unittest.TestCase):
    def test_confirmation_and_edit_generate_real_list(self) -> None:
        from larder.planner import plan
        from larder.web import create_app
        from tests.test_web_shell import WebShellTestCase

        fixture = WebShellTestCase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        app = create_app(fixture.settings())
        conn = db.connect(fixture.settings())
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO recipe_source (kind,location) VALUES ('test','hook')")
        conn.execute(
            "INSERT INTO recipe (id,source_id,slug,title,source_ref,content_hash,body) VALUES (1,1,'a','A','','','')"
        )
        conn.execute("INSERT INTO recipe_meta (recipe_id) VALUES (1)")
        conn.execute(
            "INSERT INTO ingredient_line (recipe_id,position,raw_text,qty,confidence) VALUES (1,0,'mystery',2,0)"
        )
        week = date.today() - timedelta(days=(date.today().weekday() + 1) % 7)
        with app.app_context():
            plan.save(conn, week, "pick", week, recipe_id=1)
            plan.save(conn, week, "confirm", week)
            self.assertEqual(
                [tuple(r) for r in conn.execute("SELECT free_text,qty FROM list_item")],
                [("mystery", 2)],
            )
            plan.save(conn, week, "skip", week)
            self.assertEqual(list(conn.execute("SELECT * FROM list_item")), [])
