# SPDX-License-Identifier: AGPL-3.0-only
import sqlite3
import tomllib
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

from larder import db
from larder.recipes.doc import read_recipe

FIXTURES = Path(__file__).parent / "fixtures"


class IngredientParseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        from larder.ingredients.parse import ParsedLine, parse_line
        from larder.ingredients.units import REVIEW_THRESHOLD

        self.parse = parse_line
        self.result_type = ParsedLine
        self.threshold = REVIEW_THRESHOLD

    def test_quantities_integers_fractions_mixed_ranges(self) -> None:
        cases = [
            ("2", 2, None),
            ("1.5", 1.5, None),
            (".5", 0.5, None),
            ("1 1/4", 1.25, None),
            ("2/3", 2 / 3, None),
            ("½", 0.5, None),
            ("1¼", 1.25, None),
            ("1 ⅓", 1 + 1 / 3, None),
            ("2-3", 2, 3),
            ("2 – 3", 2, 3),
            ("½–¾", 0.5, 0.75),
            ("1 1/4-1 1/2", 1.25, 1.5),
            ("2 to 3", 2, 3),
        ]
        for text, low, high in cases:
            with self.subTest(text=text):
                parsed = self.parse(f"{text} cups flour")
                self.assertEqual((parsed.qty, parsed.qty_max), (low, high))

    def test_unit_synonyms(self) -> None:
        cases = {
            "tbsp": ("tbsp", "tablespoon", "tablespoons", "TBSP."),
            "tsp": ("tsp", "teaspoon", "teaspoons"),
            "lb": ("lb", "lbs", "pound", "pounds"),
            "oz": ("oz", "ounce", "ounces"),
            "cup": ("cup", "cups"),
            "g": ("g", "gram", "grams"),
            "kg": ("kg", "kilogram", "kilograms"),
            "ml": ("ml", "milliliter", "milliliters"),
            "l": ("l", "liter", "liters"),
            "can": ("can", "cans"),
            "jar": ("jar", "jars"),
            "clove": ("clove", "cloves"),
        }
        for expected, synonyms in cases.items():
            for synonym in synonyms:
                with self.subTest(synonym=synonym):
                    self.assertEqual(self.parse(f"1 {synonym} food").unit, expected)

    def test_package_with_size(self) -> None:
        self.assertEqual(
            self.parse("1 can (15 oz) corn, no salt added"),
            self.result_type(
                "1 can (15 oz) corn, no salt added",
                1,
                None,
                "can",
                "corn",
                (),
                "15 oz; no salt added",
                False,
                True,
                1.0,
            ),
        )

    def test_optional_flag(self) -> None:
        for marker in ("optional", "OPTIONAL"):
            with self.subTest(marker=marker):
                self.assertEqual(
                    self.parse(f"1 tsp salt ({marker})"),
                    self.result_type(
                        f"1 tsp salt ({marker})",
                        1,
                        None,
                        "tsp",
                        "salt",
                        (),
                        "",
                        True,
                        True,
                        1.0,
                    ),
                )
        self.assertFalse(self.parse("1 tsp optional seasoning").optional)

    def test_alternatives(self) -> None:
        for text in (
            "honey, maple syrup, or agave",
            "honey, maple syrup or agave",
            "honey or maple syrup or agave",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    self.parse(f"2 tbsp {text}, warmed"),
                    self.result_type(
                        f"2 tbsp {text}, warmed",
                        2,
                        None,
                        "tbsp",
                        "honey",
                        ("maple syrup", "agave"),
                        "warmed",
                        False,
                        True,
                        0.9,
                    ),
                )
        self.assertEqual(self.parse("1 cup butter or oil").alternatives, ("oil",))

    def test_parenthetical_and_trailing_notes(self) -> None:
        cases = [
            ("1 cup herbs (modify to taste)", "herbs", "modify to taste"),
            ("1 cup herbs, chopped", "herbs", "chopped"),
            ("1 cup herbs (fresh), chopped", "herbs", "fresh; chopped"),
            ("1 cup herbs, chopped, packed", "herbs", "chopped, packed"),
        ]
        for raw, item, notes in cases:
            with self.subTest(raw=raw):
                parsed = self.parse(raw)
                self.assertEqual((parsed.item_text, parsed.notes), (item, notes))
        # A comma in a note is not an item list.
        parsed = self.parse("1 cup herbs (fresh, or dried), chopped")
        self.assertEqual((parsed.item_text, parsed.alternatives), ("herbs", ()))

    def test_non_food_flag(self) -> None:
        for item in ("small mason jar", "foil", "parchment", "toothpicks", "skewers"):
            with self.subTest(item=item):
                self.assertFalse(self.parse(f"1 {item}").is_food)
        for raw in ("1 jar sauce", "1 cup skewerfish", "1 cup oil, for foil"):
            with self.subTest(raw=raw):
                self.assertTrue(self.parse(raw).is_food)

    def test_unparseable_line_low_confidence_not_exception(self) -> None:
        for raw in (
            "",
            "???",
            "1/0 cup flour",
            "3-2 cups flour",
            "-1 cup flour",
            "2..3 cups flour",
            "1 cup ???",
            "1 cup flour (",
            "1 cup flour )",
        ):
            with self.subTest(raw=raw):
                self.assertLess(self.parse(raw).confidence, self.threshold)

    def test_all_fixture_lines_parse_with_expected_results(self) -> None:
        with (FIXTURES / "ingredients" / "golden.toml").open("rb") as stream:
            cases = tomllib.load(stream)["lines"]
        golden_raw = {case["raw"] for case in cases}
        recipe_raw = {
            line.text
            for path in (FIXTURES / "hugo").glob("*.md")
            for line in read_recipe(path).ingredients
        }
        self.assertEqual(recipe_raw - golden_raw, set())
        for case in cases:
            with self.subTest(raw=case["raw"]):
                # TOML has no null; absent quantities/units mean None.
                expected = self.result_type(
                    case["raw"],
                    case.get("qty"),
                    case.get("qty_max"),
                    case.get("unit"),
                    case["item_text"],
                    tuple(case["alternatives"]),
                    case["notes"],
                    case["optional"],
                    case["is_food"],
                    case["confidence"],
                )
                self.assertEqual(self.parse(case["raw"]), expected)

    def test_parsed_line_fields_match_the_ingredient_line_columns(self) -> None:
        parsed = self.parse("2-3 tbsp honey or agave (optional), warmed")
        self.assertEqual(
            [field.name for field in fields(parsed)],
            [
                "raw",
                "qty",
                "qty_max",
                "unit",
                "item_text",
                "alternatives",
                "notes",
                "optional",
                "is_food",
                "confidence",
            ],
        )
        with self.assertRaises(FrozenInstanceError):
            parsed.qty = 9
        conn = sqlite3.connect(":memory:", autocommit=True)
        self.addCleanup(conn.close)
        db.migrate(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ingredient_line)")}
        self.assertEqual(
            {"qty", "qty_max", "notes", "optional", "is_food", "confidence"} - columns,
            set(),
        )
        alt_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(ingredient_line_alt)")
        }
        self.assertEqual({"line_id", "position", "alt_text"} - alt_columns, set())
        self.assertEqual((parsed.item_text, parsed.alternatives), ("honey", ("agave",)))

    def test_confidence_penalties_are_applied_as_listed(self) -> None:
        # One changed cause per pair. Preserve unknown words rather than
        # guessing whether the first one names a unit or part of the food.
        cases = [
            ("1 cup oil", 1.0),
            ("1 glug oil", 0.7),
            ("cup oil", 0.8),
            ("1 cup oil ???", 0.7),
            ("1 cup 12-oz tomatoes", 0.7),
            ("1 cup oil or butter", 0.9),
            ("glug oil or butter ???", 0.4),
            ("???", 0.5),
            ("1 cup flour or", 0.7),
            ("or butter", 0.5),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertAlmostEqual(self.parse(raw).confidence, expected)
        self.assertEqual(self.threshold, 0.8)


if __name__ == "__main__":
    unittest.main()
