# SPDX-License-Identifier: AGPL-3.0-only
import datetime
import unittest
from pathlib import Path

from larder.recipes.doc import (
    RecipeDoc,
    RecipeParseError,
    parse_recipe,
    read_recipe,
    serialize_recipe,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hugo"
FIXTURE_NAMES = ["lemon-pasta", "weeknight-curry", "pancakes"]


def load(name: str) -> RecipeDoc:
    return read_recipe(FIXTURES / f"{name}.md")


def wrap(body: str, front_matter: str = 'title = "T"\n') -> str:
    return f"+++\n{front_matter}+++\n{body}"


class RecipeDocParseTestCase(unittest.TestCase):
    def test_parses_toml_front_matter_fields(self) -> None:
        doc = load("lemon-pasta")
        self.assertEqual(doc.slug, "lemon-pasta")
        self.assertEqual(doc.front_matter["title"], "Lemon Pasta")
        self.assertEqual(
            doc.front_matter["date"],
            datetime.datetime(2024, 3, 9, 18, 30, tzinfo=datetime.UTC),
        )
        self.assertEqual(doc.front_matter["tags"], ["pasta", "quick", "vegetarian"])
        self.assertEqual(doc.front_matter["source"], "https://example.com/lemon-pasta")
        # Keys outside the known set are kept as parsed.
        self.assertEqual(doc.front_matter["weight"], 3)
        self.assertEqual(
            doc.intro, "A bright pasta for a weeknight. Ready in twenty minutes."
        )

    def test_extracts_ingredient_lines_in_order(self) -> None:
        doc = load("lemon-pasta")
        self.assertEqual(
            [line.text for line in doc.ingredients],
            [
                "400 g spaghetti",
                "2 lemons, zest and juice",
                "60 g butter",
                "1/2 cup grated parmesan",
                "salt and black pepper",
            ],
        )
        # Line numbers are 1-based, counted over the body.
        raw = doc.body.split("\n")
        for line in doc.ingredients:
            self.assertEqual(raw[line.line - 1], f"- {line.text}")
            self.assertIsNone(line.group)
        self.assertEqual(
            doc.directions,
            [
                "Boil the spaghetti in salted water until al dente.",
                "Melt the butter in a wide pan and add the lemon zest.",
                (
                    "Toss the drained pasta with the butter, lemon juice and "
                    "parmesan. Add a splash of pasta water if it looks dry."
                ),
            ],
        )
        self.assertEqual(
            doc.notes,
            "Keeps for a day in the fridge. Add fresh lemon juice when reheating.",
        )

    def test_heading_variants_normalized(self) -> None:
        doc = load("weeknight-curry")  # `## Ingredients:` and `## Steps:`
        self.assertEqual(len(doc.ingredients), 6)
        self.assertEqual(len(doc.directions), 3)
        self.assertEqual(doc.notes, "Freezes well.")
        pancakes = load("pancakes")  # `## directions`
        self.assertEqual(
            pancakes.directions,
            [
                "Whisk everything together.",
                "Fry ladles of batter in a buttered pan until golden on both sides.",
            ],
        )
        upper = parse_recipe(wrap("## INGREDIENTS :\n\n- a\n\n## STEPS\n\n1. b\n"), "x")
        self.assertEqual([i.text for i in upper.ingredients], ["a"])
        self.assertEqual(upper.directions, ["b"])

    def test_heading_closing_hashes_are_ignored(self) -> None:
        text = wrap(
            "## Ingredients ##\n\n### Sauce ###\n\n- a\n\n## Steps: ##\n\n1. b\n"
        )
        doc = parse_recipe(text, "x")
        self.assertEqual([(i.group, i.text) for i in doc.ingredients], [("Sauce", "a")])
        self.assertEqual(doc.directions, ["b"])

    def test_ignores_stray_h1_and_nutrition_block_but_keeps_body(self) -> None:
        doc = load("pancakes")
        self.assertEqual(doc.intro, "Weekend pancakes.")
        self.assertEqual(
            [i.text for i in doc.ingredients],
            ["1 1/2 cups flour", "1 tbsp sugar", "1 egg", "1 cup milk"],
        )
        self.assertEqual(len(doc.directions), 2)
        self.assertEqual(doc.notes, "")
        for text in ("Calories", "Protein", "Fat"):
            self.assertFalse(any(text in i.text for i in doc.ingredients))
            self.assertFalse(any(text in d for d in doc.directions))
        raw = (FIXTURES / "pancakes.md").read_text()
        self.assertEqual(doc.body, raw.split("+++\n", 2)[2])
        self.assertIn("# Fluffy Pancakes\n", doc.body)
        self.assertIn("## Nutrition\n", doc.body)
        self.assertIn("| Fat | 7 g |", doc.body)

    def test_missing_front_matter_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(RecipeParseError, r"no-fm.*no TOML front matter"):
            parse_recipe("## Ingredients\n\n- a\n", "no-fm")
        with self.assertRaisesRegex(RecipeParseError, r"open.*closing"):
            parse_recipe('+++\ntitle = "x"\n\n## Ingredients\n', "open")
        with self.assertRaisesRegex(RecipeParseError, r"bad.*TOML"):
            parse_recipe("+++\ntitle = \n+++\n", "bad")
        self.assertTrue(issubclass(RecipeParseError, ValueError))

    def test_ingredient_subgroups(self) -> None:
        doc = load("weeknight-curry")
        self.assertEqual(
            [(i.group, i.text) for i in doc.ingredients],
            [
                ("Curry", "2 tbsp oil"),
                ("Curry", "1 onion, diced"),
                ("Curry", "2 cans chickpeas, drained"),
                ("Curry", "1 can coconut milk"),
                ("Rice", "1.5 cups basmati rice"),
                ("Rice", "3 cups water"),
            ],
        )

    def test_draft_flag(self) -> None:
        self.assertFalse(load("lemon-pasta").is_draft)
        self.assertFalse(load("weeknight-curry").is_draft)  # draft = false
        draft = parse_recipe(wrap("", 'title = "T"\ndraft = true\n'), "d")
        self.assertTrue(draft.is_draft)

    def test_headings_inside_code_fences_are_not_sections(self) -> None:
        text = wrap("## Ingredients\n\n- a\n\n```\n## Directions\n- x\n```\n")
        doc = parse_recipe(text, "x")
        self.assertEqual([i.text for i in doc.ingredients], ["a"])
        self.assertEqual(doc.directions, [])

    def test_long_fence_is_not_closed_by_a_shorter_one(self) -> None:
        text = wrap("## Ingredients\n\n````\n```\n- x\n````\n- a\n")
        doc = parse_recipe(text, "x")
        self.assertEqual([i.text for i in doc.ingredients], ["a"])

    def test_fence_is_not_closed_by_a_line_with_an_info_string(self) -> None:
        text = wrap("## Ingredients\n\n```\n```python\n- x\n```\n- a\n")
        doc = parse_recipe(text, "x")
        self.assertEqual([i.text for i in doc.ingredients], ["a"])

    def test_fence_is_not_closed_by_the_other_fence_character(self) -> None:
        text = wrap("## Ingredients\n\n```\n~~~\n- x\n```\n- a\n")
        doc = parse_recipe(text, "x")
        self.assertEqual([i.text for i in doc.ingredients], ["a"])

    def test_thematic_break_is_not_a_list_item(self) -> None:
        for rule in ("* * *", "- - -", "***", "_ _ _"):
            with self.subTest(rule=rule):
                text = wrap(f"## Ingredients\n\n- a\n\n{rule}\n\n- b\n")
                doc = parse_recipe(text, "x")
                self.assertEqual([i.text for i in doc.ingredients], ["a", "b"])

    def test_crlf_input_is_normalized(self) -> None:
        text = wrap("## Ingredients\n\n- a\n- b\n").replace("\n", "\r\n")
        doc = parse_recipe(text, "x")
        self.assertEqual([i.text for i in doc.ingredients], ["a", "b"])
        self.assertNotIn("\r", doc.body)

    def test_read_recipe_takes_slug_from_filename(self) -> None:
        self.assertEqual(load("pancakes").slug, "pancakes")


class RecipeDocSerializeTestCase(unittest.TestCase):
    def test_round_trip_preserves_content(self) -> None:
        for name in FIXTURE_NAMES:
            with self.subTest(name=name):
                path = FIXTURES / f"{name}.md"
                doc = read_recipe(path)
                out = serialize_recipe(doc)
                self.assertEqual(parse_recipe(out, name), doc)
                # The body is written back byte for byte.
                self.assertEqual(out.split("+++\n", 2)[2], doc.body)
                self.assertEqual(doc.body, path.read_text().split("+++\n", 2)[2])

    def test_serialized_fixture_is_stable(self) -> None:
        for name in FIXTURE_NAMES:
            with self.subTest(name=name):
                once = serialize_recipe(load(name))
                twice = serialize_recipe(parse_recipe(once, name))
                self.assertEqual(once, twice)

    def test_writes_known_value_types(self) -> None:
        fm = {
            "title": 'He said "hi"\\ there\n\ttab \x7f é',
            "date": datetime.datetime(2024, 3, 9, 18, 30, tzinfo=datetime.UTC),
            "day": datetime.date(2024, 3, 9),
            "draft": True,
            "off": False,
            "tags": ["a", "b c"],
            "empty": [],
            "count": 3,
            "ratio": 1.5,
            "weird key": "v",
            "params": {"servings": 4, "names": ["x"]},
        }
        doc = RecipeDoc("t", fm, "", [], [], "", "\nbody\n")
        out = serialize_recipe(doc)
        self.assertTrue(out.startswith("+++\n"))
        self.assertIn('title = "He said \\"hi\\"\\\\ there\\n\\ttab \\u007f é"\n', out)
        self.assertIn("draft = true\n", out)
        self.assertIn("off = false\n", out)
        self.assertIn('tags = ["a", "b c"]\n', out)
        self.assertIn('"weird key" = "v"\n', out)
        self.assertEqual(parse_recipe(out, "t").front_matter, fm)

    def test_unknown_keys_are_preserved_in_order(self) -> None:
        doc = load("weeknight-curry")
        self.assertEqual(doc.front_matter["params"], {"servings": 4})
        again = parse_recipe(serialize_recipe(doc), "weeknight-curry")
        self.assertEqual(list(again.front_matter), list(doc.front_matter))
        self.assertEqual(again.front_matter["params"], {"servings": 4})

    def test_unsupported_value_type_raises(self) -> None:
        doc = RecipeDoc("t", {"bad": object()}, "", [], [], "", "")
        with self.assertRaises(TypeError):
            serialize_recipe(doc)

    def test_time_with_tzinfo_raises(self) -> None:
        # TOML has no offset time, so the writer would emit invalid TOML.
        aware = datetime.time(1, 2, tzinfo=datetime.UTC)
        doc = RecipeDoc("t", {"t": aware}, "", [], [], "", "")
        with self.assertRaises(TypeError):
            serialize_recipe(doc)
        naive = RecipeDoc("t", {"t": datetime.time(1, 2)}, "", [], [], "", "")
        self.assertEqual(
            parse_recipe(serialize_recipe(naive), "t").front_matter,
            {"t": datetime.time(1, 2)},
        )


if __name__ == "__main__":
    unittest.main()
