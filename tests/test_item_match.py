# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical item matching and its shared unit vocabulary."""

import sqlite3
import tomllib
import unittest
from pathlib import Path

from larder import db
from tests.test_web_shell import WebShellTestCase

ROOT = Path(__file__).resolve().parent.parent


def seed(conn: sqlite3.Connection) -> None:
    from larder.ingredients.match import seed_items

    seed_items(conn)


class ItemDbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:", autocommit=True)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys = ON")
        db.migrate(self.conn)

    def rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        return [tuple(row) for row in self.conn.execute(sql, args)]

    def recipe(self, recipe_id: int = 1) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO recipe_source (id, kind, location) "
            "VALUES (1, 'test', 'test')"
        )
        self.conn.execute(
            "INSERT INTO recipe (id, source_id, slug, title, source_ref, "
            "content_hash, body) VALUES (?, 1, ?, 'Test', '', '', '')",
            (recipe_id, str(recipe_id)),
        )

    def line(self, raw: str, position: int = 0, recipe_id: int = 1) -> int:
        return self.conn.execute(
            "INSERT INTO ingredient_line (recipe_id, position, raw_text, confidence) "
            "VALUES (?, ?, ?, 0) RETURNING id",
            (recipe_id, position, raw),
        ).fetchone()[0]


class MatchTest(ItemDbTest):
    def setUp(self) -> None:
        super().setUp()
        self.conn.executemany(
            "INSERT INTO item (id, canonical_name) VALUES (?, ?)",
            [(1, "ground turkey"), (2, "mushrooms"), (3, "milk")],
        )
        self.conn.execute(
            "INSERT INTO item_alias (item_id, alias) VALUES (1, 'ground turkey meat')"
        )

    def match(self, text: str) -> tuple:
        from larder.ingredients.match import match_item

        result = match_item(self.conn, text)
        return result.item_id, result.confidence, result.needs_review

    def test_exact_and_alias_match(self) -> None:
        for text in ("ground turkey", "ground turkey meat"):
            with self.subTest(text=text):
                self.assertEqual(self.match(text), (1, 1.0, False))

    def test_normalization_plural_case_descriptors(self) -> None:
        for text in (
            "sliced fresh mushrooms",
            "mushroom",
            "MUSHROOMS",
            "  mushrooms  ",
            "chopped mushrooms",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.match(text), (2, 1.0, False))

    def test_unknown_item_returns_candidate_with_low_confidence(self) -> None:
        item_id, confidence, review = self.match("smoked turkey")
        self.assertEqual((item_id, 0 < confidence < 0.8, review), (1, True, True))

    def test_no_token_overlap_returns_no_candidate(self) -> None:
        self.assertEqual(self.match("quinoa"), (None, 0.0, True))

    def test_alias_learned_from_confirmation_is_used(self) -> None:
        # Confirmation belongs to S18; the matcher reads its persisted alias.
        self.conn.execute(
            "INSERT INTO item_alias (item_id, alias) VALUES (2, 'forest mix')"
        )
        self.assertEqual(self.match("FOREST MIX"), (2, 1.0, False))

    def test_canonical_name_wins_over_alias(self) -> None:
        self.conn.execute("INSERT INTO item_alias (item_id, alias) VALUES (1, 'milk')")
        self.assertEqual(self.match("milk"), (3, 1.0, False))

    def test_empty_catalog_returns_no_candidate(self) -> None:
        self.conn.execute("DELETE FROM item_alias")
        self.conn.execute("DELETE FROM item")
        self.assertEqual(self.match("milk"), (None, 0.0, True))


class SeedTest(ItemDbTest):
    def test_seed_loads_categories_in_order(self) -> None:
        seed(self.conn)
        self.assertEqual(
            self.rows("SELECT name FROM category ORDER BY sort_order, name"),
            [
                (name,)
                for name in (
                    "produce",
                    "meat",
                    "dairy",
                    "bakery",
                    "canned",
                    "dry goods",
                    "spices",
                    "frozen",
                    "household",
                    "pet",
                )
            ],
        )

    def test_seed_loads_items_aliases_and_no_staples(self) -> None:
        seed(self.conn)
        self.assertEqual(
            (
                self.rows(
                    "SELECT canonical_name, category FROM item "
                    "WHERE canonical_name = 'ground turkey'"
                ),
                self.rows(
                    "SELECT i.canonical_name FROM item_alias a "
                    "JOIN item i ON i.id = a.item_id "
                    "WHERE a.alias = 'ground turkey meat'"
                ),
                self.rows("SELECT item_id FROM staple"),
            ),
            ([("ground turkey", "meat")], [("ground turkey",)], []),
        )

    def test_seed_loads_units_and_aliases(self) -> None:
        seed(self.conn)
        self.assertEqual(
            self.rows(
                "SELECT u.name, u.dimension, a.alias FROM unit u "
                "JOIN unit_alias a ON a.unit_id = u.id "
                "WHERE a.alias IN ('cloves', 'cans', 'jars', 'tablespoons', 'lbs') "
                "ORDER BY a.alias"
            ),
            [
                ("can", "package", "cans"),
                ("clove", "count", "cloves"),
                ("jar", "package", "jars"),
                ("lb", "mass", "lbs"),
                ("tbsp", "volume", "tablespoons"),
            ],
        )

    def test_seed_preserves_existing_rows(self) -> None:
        seed(self.conn)
        tables = ("category", "item", "item_alias", "unit", "unit_alias", "staple")
        edits = [
            ("category", "sort_order", 77, 0, "name = 'produce'"),
            ("item", "category", "custom", "dairy", "canonical_name = 'milk'"),
            ("unit", "dimension", "count", "volume", "name = 'cup'"),
            (
                "item_alias",
                "item_id",
                self.rows("SELECT id FROM item WHERE canonical_name = 'milk'")[0][0],
                self.rows("SELECT id FROM item WHERE canonical_name = 'ground turkey'")[
                    0
                ][0],
                "alias = 'ground turkey meat'",
            ),
            (
                "unit_alias",
                "unit_id",
                self.rows("SELECT id FROM unit WHERE name = 'g'")[0][0],
                self.rows("SELECT id FROM unit WHERE name = 'cup'")[0][0],
                "alias = 'cups'",
            ),
        ]
        for table, field, changed, original, where in edits:
            with self.subTest(table=table):
                # Only one existing value differs from the baseline per case.
                sql = f"UPDATE {table} SET {field} = ? WHERE {where}"
                self.conn.execute(sql, (changed,))
                try:
                    before = {
                        table: self.rows(f"SELECT * FROM {table} ORDER BY id")
                        for table in tables
                    }
                    seed(self.conn)
                    self.assertEqual(
                        {
                            table: self.rows(f"SELECT * FROM {table} ORDER BY id")
                            for table in tables
                        },
                        before,
                    )
                finally:
                    self.conn.execute(sql, (original,))

    def test_seed_reinserts_missing_rows(self) -> None:
        seed(self.conn)
        self.conn.execute("DELETE FROM item WHERE canonical_name = 'milk'")
        seed(self.conn)
        self.assertEqual(
            self.rows("SELECT category FROM item WHERE canonical_name = 'milk'"),
            [("dairy",)],
        )

    def test_seed_toml_ships_as_package_data(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertIn(
            "data/*.toml", config["tool"]["setuptools"]["package-data"]["larder"]
        )


class StartupSeedTest(WebShellTestCase):
    def test_startup_seeds_real_database(self) -> None:
        from larder.web import create_app

        create_app(self.settings(), bootstrap=False)
        conn = db.connect(self.settings())
        self.addCleanup(conn.close)
        self.assertEqual(
            [
                tuple(r)
                for r in conn.execute(
                    "SELECT canonical_name, category FROM item WHERE canonical_name = 'milk'"
                )
            ],
            [("milk", "dairy")],
        )


class PersistenceTest(ItemDbTest):
    def setUp(self) -> None:
        super().setUp()
        seed(self.conn)
        self.recipe()

    def parse(self) -> None:
        from larder.ingredients.match import parse_and_match_recipe

        parse_and_match_recipe(self.conn, 1)

    def test_writer_stores_whole_parse(self) -> None:
        self.line("2-3 cloves garlic, minced (optional)")
        self.parse()
        self.assertEqual(
            self.rows(
                "SELECT raw_text, qty, qty_max, u.name, i.canonical_name, notes, "
                "optional, is_food, confidence, confirmed FROM ingredient_line l "
                "LEFT JOIN unit u ON u.id=l.unit_id LEFT JOIN item i ON i.id=l.item_id"
            ),
            [
                (
                    "2-3 cloves garlic, minced (optional)",
                    2.0,
                    3.0,
                    "clove",
                    "garlic",
                    "minced",
                    1,
                    1,
                    1.0,
                    0,
                )
            ],
        )

    def test_writer_preserves_non_food(self) -> None:
        self.line("1 small mason jar")
        self.parse()
        self.assertEqual(self.rows("SELECT is_food FROM ingredient_line"), [(0,)])

    def test_writer_stores_ordered_alternatives(self) -> None:
        self.line("1 tbsp honey, maple syrup or agave")
        self.parse()
        self.assertEqual(
            self.rows(
                "SELECT a.position, a.alt_text, i.canonical_name FROM ingredient_line_alt a "
                "LEFT JOIN item i ON i.id=a.item_id ORDER BY a.position"
            ),
            [(1, "maple syrup", "maple syrup"), (2, "agave", "agave")],
        )

    def test_writer_replaces_stale_alternatives(self) -> None:
        line_id = self.line("1 tbsp honey, maple syrup or agave")
        self.parse()
        self.conn.execute(
            "UPDATE ingredient_line SET raw_text='1 tbsp honey' WHERE id=?", (line_id,)
        )
        self.parse()
        self.assertEqual(self.rows("SELECT alt_text FROM ingredient_line_alt"), [])

    def test_writer_keeps_low_parse_confidence(self) -> None:
        self.line("1 egg")
        self.parse()
        self.assertEqual(self.rows("SELECT confidence FROM ingredient_line"), [(0.7,)])

    def test_writer_keeps_low_match_confidence_and_candidate(self) -> None:
        self.line("1 cup smoked turkey")
        self.parse()
        rows = self.rows(
            "SELECT i.canonical_name, l.confidence FROM ingredient_line l LEFT JOIN item i ON i.id=l.item_id"
        )
        self.assertEqual(
            [(name, 0 < score < 0.8) for name, score in rows], [("ground turkey", True)]
        )

    def test_writer_marks_unknown_for_review(self) -> None:
        self.line("1 cup quinoa")
        self.parse()
        self.assertEqual(
            self.rows("SELECT item_id, confidence FROM ingredient_line"), [(None, 0.0)]
        )

    def test_unknown_alternative_keeps_line_in_review(self) -> None:
        self.line("1 tbsp honey or quinoa")
        self.parse()
        self.assertEqual(
            self.rows(
                "SELECT i.canonical_name, l.confidence, a.alt_text, a.item_id "
                "FROM ingredient_line l JOIN item i ON i.id=l.item_id "
                "JOIN ingredient_line_alt a ON a.line_id=l.id"
            ),
            [("honey", 0.0, "quinoa", None)],
        )

    def test_writer_preserves_confirmed_lines(self) -> None:
        line_id = self.line("1 cup milk")
        self.conn.execute(
            "UPDATE ingredient_line SET confirmed=1, qty=42, notes='edited', confidence=1 WHERE id=?",
            (line_id,),
        )
        self.conn.execute(
            "INSERT INTO ingredient_line_alt (line_id,position,alt_text) VALUES (?,1,'edited choice')",
            (line_id,),
        )
        self.parse()
        self.assertEqual(
            (
                self.rows(
                    "SELECT qty, notes, confidence, confirmed FROM ingredient_line"
                ),
                self.rows("SELECT alt_text FROM ingredient_line_alt"),
            ),
            ([(42.0, "edited", 1.0, 1)], [("edited choice",)]),
        )

    def test_writer_only_processes_requested_recipe(self) -> None:
        self.recipe(2)
        self.line("1 cup milk", recipe_id=2)
        self.parse()
        self.assertEqual(
            self.rows("SELECT item_id, confidence FROM ingredient_line"), [(None, 0.0)]
        )

    def test_writer_rolls_back_whole_recipe_on_failure(self) -> None:
        self.line("1 cup milk")
        self.line("1 cup flour", position=1)
        self.conn.execute(
            "CREATE TRIGGER reject_second BEFORE UPDATE ON ingredient_line WHEN NEW.position=1 BEGIN SELECT RAISE(ABORT,'test failure'); END"
        )
        try:
            self.parse()
        except sqlite3.IntegrityError:
            pass
        self.assertEqual(
            self.rows(
                "SELECT qty, item_id, confidence FROM ingredient_line ORDER BY position"
            ),
            [(None, None, 0.0), (None, None, 0.0)],
        )

    def test_golden_fixture_ingredients_map_or_need_review(self) -> None:
        fixture = tomllib.loads(
            (ROOT / "tests/fixtures/ingredients/golden.toml").read_text()
        )
        for position, line in enumerate(fixture["lines"]):
            self.line(line["raw"], position)
        self.parse()
        self.assertEqual(
            self.rows(
                "SELECT raw_text, qty, item_id IS NOT NULL OR confidence < 0.8 "
                "FROM ingredient_line ORDER BY position"
            ),
            [(line["raw"], line.get("qty"), 1) for line in fixture["lines"]],
        )


class UnitDimensionTest(unittest.TestCase):
    def test_canonical_unit_dimensions(self) -> None:
        from larder.ingredients.units import UNIT_DIMENSIONS

        self.assertEqual(
            UNIT_DIMENSIONS,
            {
                "tbsp": "volume",
                "tsp": "volume",
                "cup": "volume",
                "ml": "volume",
                "l": "volume",
                "lb": "mass",
                "oz": "mass",
                "g": "mass",
                "kg": "mass",
                "clove": "count",
                "can": "package",
                "jar": "package",
            },
        )
