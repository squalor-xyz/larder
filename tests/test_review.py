# SPDX-License-Identifier: AGPL-3.0-only
"""Review through the real admin UI and the recipe index."""

import sqlite3

from werkzeug.test import TestResponse

from larder import db
from larder.ingredients.match import parse_and_match_recipe
from larder.recipes.index import index_recipes
from larder.web import create_app
from tests.test_web_shell import WebShellTestCase

PATH = "/admin/review"


class ReviewTest(WebShellTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app(self.settings())
        self.client = self.paired_client(self.app)
        self.conn = db.connect(self.settings())
        self.addCleanup(self.conn.close)
        self.files = self.settings().recipes_dir / "recipes"
        self.files.mkdir()
        self.conn.execute(
            "INSERT INTO recipe_source (id,kind,location) VALUES (1,'test','test')"
        )
        self.conn.execute(
            "INSERT INTO recipe (id,source_id,slug,title,body,source_ref,content_hash) VALUES (1,1,'one','One','','one','hash')"
        )

    def line(self, raw: str) -> int:
        position = self.conn.execute("SELECT count(*) FROM ingredient_line").fetchone()[
            0
        ]
        cursor = self.conn.execute(
            "INSERT INTO ingredient_line (recipe_id,position,raw_text,confidence) VALUES (1,?,?,0)",
            (position, raw),
        )
        parse_and_match_recipe(self.conn, 1)
        return cursor.lastrowid

    def rows(self, sql: str) -> list[tuple]:
        return [tuple(row) for row in self.conn.execute(sql)]

    def answer(
        self, line_id: int, action: str = "confirm", **fields: str
    ) -> TestResponse:
        self.client.get(PATH)
        with self.client.session_transaction() as session:
            token = session["csrf"]
        return self.client.post(
            f"{PATH}/{line_id}",
            data={"csrf_token": token, "action": action, **fields},
        )

    def write(self, raw: str, slug: str = "other") -> None:
        (self.files / f"{slug}.md").write_text(
            f'+++\ntitle="Other"\n+++\n## Ingredients\n- {raw}\n'
        )

    def test_queue_lists_low_confidence_and_unmatched_lines(self) -> None:
        self.line("1 egg")
        self.line("1 cup quinoa")
        self.line("1 cup smoked turkey")
        self.line("1 cup milk")
        page = self.client.get(PATH)
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        for text in (
            "1 egg",
            "1 cup quinoa",
            "Unmatched",
            "· ground turkey ·",
            "Parse review (3)",
            "<details",
            "<summary>Edit</summary>",
            'name="qty"',
            'name="item_id"',
        ):
            self.assertIn(text, body)
        self.assertNotIn("1 cup milk", body)

    def test_confirm_writes_override_and_alias(self) -> None:
        line = self.line("1 cup smoked turkey")
        self.assertEqual(self.answer(line).status_code, 303)
        self.assertEqual(
            self.rows(
                "SELECT o.normalized_text,o.qty,u.name,i.canonical_name,o.notes,o.is_food,o.optional,o.qty_max "
                "FROM parse_override o JOIN item i ON i.id=o.item_id JOIN unit u ON u.id=o.unit_id"
            ),
            [("1 cup smoked turkey", 1.0, "cup", "ground turkey", "", 1, 0, None)],
        )
        self.assertEqual(
            self.rows(
                "SELECT i.canonical_name FROM item_alias a JOIN item i ON i.id=a.item_id WHERE a.alias='smoked turkey'"
            ),
            [("ground turkey",)],
        )
        self.assertEqual(
            self.rows("SELECT confirmed,confidence FROM ingredient_line"), [(1, 1.0)]
        )
        self.assertNotIn(
            "1 cup smoked turkey", self.client.get(PATH).get_data(as_text=True)
        )

    def test_override_applies_to_identical_line_in_other_recipe(self) -> None:
        line = self.line("1 egg")
        milk = self.conn.execute(
            "SELECT id FROM item WHERE canonical_name='milk'"
        ).fetchone()[0]
        self.answer(
            line,
            "edit",
            qty="2",
            qty_max="3",
            unit="cup",
            item_id=str(milk),
            notes="reviewed",
            optional="1",
        )
        self.write("1 EGG")
        index_recipes(self.settings())
        self.assertEqual(
            self.rows(
                "SELECT qty,qty_max,u.name,i.canonical_name,l.notes,l.optional,l.confirmed,l.confidence FROM ingredient_line l JOIN recipe r ON r.id=l.recipe_id JOIN unit u ON u.id=l.unit_id JOIN item i ON i.id=l.item_id WHERE r.slug='other'"
            ),
            [(2.0, 3.0, "cup", "milk", "reviewed", 1, 1, 1.0)],
        )
        self.write("2 egg")
        index_recipes(self.settings())
        self.assertEqual(
            self.rows(
                "SELECT qty,confirmed FROM ingredient_line l JOIN recipe r ON r.id=l.recipe_id WHERE r.slug='other'"
            ),
            [(2.0, 0)],
        )

    def test_mark_non_food_excludes_from_lists(self) -> None:
        line = self.line("1 cup mystery")
        self.assertEqual(self.answer(line, "non_food").status_code, 303)
        self.write("1 cup mystery")
        index_recipes(self.settings())
        self.assertEqual(
            self.rows("SELECT is_food,confirmed FROM ingredient_line ORDER BY id"),
            [(0, 1), (0, 1)],
        )
        self.assertNotIn("1 cup mystery", self.client.get(PATH).get_data(as_text=True))

    def test_create_new_item_from_review(self) -> None:
        line = self.line("1 cup quinoa")
        self.assertEqual(
            self.answer(
                line, "edit", qty="1", unit="cup", new_item="quinoa", notes=""
            ).status_code,
            303,
        )
        self.assertEqual(
            self.rows(
                "SELECT i.canonical_name,l.confirmed FROM ingredient_line l JOIN item i ON i.id=l.item_id"
            ),
            [("quinoa", 1)],
        )
        self.assertEqual(
            self.rows(
                "SELECT i.canonical_name FROM item_alias a JOIN item i ON i.id=a.item_id WHERE a.alias='quinoa'"
            ),
            [("quinoa",)],
        )

    def test_alternatives_preserved_including_null_item(self) -> None:
        line = self.line("1 tbsp honey or quinoa")
        self.answer(line)
        self.write("1 tbsp honey or quinoa")
        index_recipes(self.settings())
        self.assertEqual(
            self.rows("SELECT position,alt_text,item_id FROM parse_override_alt"),
            [(1, "quinoa", None)],
        )
        self.assertEqual(
            self.rows(
                "SELECT a.position,a.alt_text,a.item_id FROM ingredient_line_alt a JOIN ingredient_line l ON l.id=a.line_id JOIN recipe r ON r.id=l.recipe_id WHERE r.slug='other'"
            ),
            [(1, "quinoa", None)],
        )

    def test_normalization_collapses_whitespace_only(self) -> None:
        line = self.line("1 cup smoked turkey")
        self.answer(line)
        self.write("1  CUP   smoked turkey")
        index_recipes(self.settings())
        self.assertEqual(
            self.rows(
                "SELECT confirmed FROM ingredient_line l JOIN recipe r ON r.id=l.recipe_id WHERE r.slug='other'"
            ),
            [(1,)],
        )

    def test_admin_guard_and_csrf(self) -> None:
        line = self.line("1 egg")
        member = self.paired_client(self.app, "Sam")
        self.assertEqual(member.get(PATH).status_code, 403)
        self.assertEqual(member.post(f"{PATH}/{line}").status_code, 403)
        self.assertEqual(self.app.test_client().get(PATH).status_code, 401)
        self.assertEqual(
            self.client.post(f"{PATH}/{line}", data={"action": "confirm"}).status_code,
            400,
        )

    def test_invalid_edit_rolls_back_and_unmatched_confirm_rejected(self) -> None:
        line = self.line("1 cup quinoa")
        self.assertEqual(self.answer(line).status_code, 400)
        milk = str(
            self.conn.execute(
                "SELECT id FROM item WHERE canonical_name='milk'"
            ).fetchone()[0]
        )
        baseline = {"qty": "1", "unit": "cup", "item_id": milk}
        for changed in (
            {"qty": "nan"},
            {"qty": "-1"},
            {"qty_max": "0"},
            {"unit": "bad"},
            {"item_id": "99999"},
        ):
            with self.subTest(changed=changed):
                fields = baseline | changed
                self.assertEqual(self.answer(line, "edit", **fields).status_code, 400)
        self.assertEqual(self.rows("SELECT normalized_text FROM parse_override"), [])

        with self.subTest(action="unknown"):
            matched = self.line("1 egg")
            self.assertEqual(self.answer(matched, "unknown").status_code, 400)
        with self.subTest(line="missing"):
            self.assertEqual(self.answer(99999).status_code, 404)
        with self.subTest(new_item="takes precedence"):
            candidate = self.line("1 cup smoked turkey")
            selected = str(
                self.conn.execute(
                    "SELECT item_id FROM ingredient_line WHERE id=?", (candidate,)
                ).fetchone()[0]
            )
            self.assertEqual(
                self.answer(
                    candidate,
                    "edit",
                    qty="1",
                    unit="cup",
                    item_id=selected,
                    new_item="smoked turkey",
                ).status_code,
                303,
            )
            self.assertEqual(
                self.rows(
                    "SELECT i.canonical_name FROM ingredient_line l JOIN item i ON i.id=l.item_id WHERE l.id="
                    + str(candidate)
                ),
                [("smoked turkey",)],
            )
        with self.subTest(line="confirmed"):
            confirmed = self.line("1 egg")
            self.conn.execute(
                "UPDATE ingredient_line SET confirmed=1 WHERE id=?", (confirmed,)
            )
            self.assertEqual(self.answer(confirmed).status_code, 404)

    def test_answer_resolves_identical_queued_lines(self) -> None:
        for changed in (
            {},
            {"raw": "1  CUP smoked TURKEY"},
            {"raw": "2 cup smoked turkey"},
            {"archived": 1},
            {"confirmed": 1},
        ):
            with self.subTest(changed=changed):
                self.conn.execute("DELETE FROM item_alias WHERE alias='smoked turkey'")
                self.conn.execute("DELETE FROM parse_override")
                self.conn.execute("DELETE FROM ingredient_line")
                self.conn.execute("DELETE FROM recipe WHERE id != 1")
                line = self.line("1 cup smoked turkey")
                self.write(changed.get("raw", "1 cup smoked turkey"))
                index_recipes(self.settings())
                if "archived" in changed:
                    self.conn.execute("UPDATE recipe SET archived=1 WHERE slug='other'")
                if "confirmed" in changed:
                    self.conn.execute(
                        "UPDATE ingredient_line SET confirmed=1,qty=99 WHERE recipe_id != 1"
                    )
                self.answer(line)
                matches = changed.get("raw") != "2 cup smoked turkey"
                resolved = matches and not changed.get("archived")
                self.assertEqual(
                    self.rows(
                        "SELECT qty,confirmed FROM ingredient_line ORDER BY recipe_id"
                    ),
                    [
                        (1.0, 1),
                        (
                            99.0
                            if changed.get("confirmed")
                            else (2.0 if not matches else 1.0),
                            int(resolved or changed.get("confirmed", 0)),
                        ),
                    ],
                )
                page = self.client.get(PATH).get_data(as_text=True)
                self.assertIn(f"Parse review ({0 if matches else 1})", page)

    def test_write_failure_rolls_back_override_and_item(self) -> None:
        line = self.line("1 cup quinoa")
        self.conn.execute(
            "CREATE TRIGGER reject_review BEFORE UPDATE ON ingredient_line WHEN NEW.confirmed=1 BEGIN SELECT RAISE(ABORT,'rejected'); END"
        )
        self.app.testing = False
        with self.assertLogs(self.app.logger, "ERROR"):
            response = self.answer(line, "edit", new_item="quinoa", qty="1", unit="cup")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows("SELECT normalized_text FROM parse_override"), [])
        self.assertEqual(
            self.rows("SELECT canonical_name FROM item WHERE canonical_name='quinoa'"),
            [],
        )

    def test_archived_and_confirmed_lines_absent_and_text_escaped(self) -> None:
        line = self.line("1 egg")
        self.conn.execute("UPDATE ingredient_line SET confirmed=1 WHERE id=?", (line,))
        self.line("<script>mystery</script>")
        body = self.client.get(PATH).get_data(as_text=True)
        self.assertIn("&lt;script&gt;mystery&lt;/script&gt;", body)
        self.assertNotIn("1 egg", body)
        self.conn.execute("UPDATE recipe SET archived=1")
        self.assertNotIn("mystery", self.client.get(PATH).get_data(as_text=True))

    def test_index_failure_rolls_back_entire_mirror(self) -> None:
        self.write("1 cup milk")
        self.conn.execute(
            "CREATE TRIGGER reject_parse BEFORE UPDATE ON ingredient_line BEGIN SELECT RAISE(ABORT,'parse rejected'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            index_recipes(self.settings())
        self.assertEqual(self.rows("SELECT slug FROM recipe WHERE slug='other'"), [])

    def test_cached_answer_replaces_old_alternatives(self) -> None:
        line = self.line("1 tbsp honey or quinoa")
        self.answer(line)
        self.write("1 tbsp honey or quinoa")
        index_recipes(self.settings())
        self.conn.execute("UPDATE ingredient_line SET confirmed=0 WHERE recipe_id != 1")
        self.conn.execute(
            "UPDATE ingredient_line_alt SET alt_text='stale' WHERE line_id != ?",
            (line,),
        )
        index_recipes(self.settings())
        self.assertEqual(
            self.rows(
                "SELECT a.alt_text FROM ingredient_line_alt a JOIN ingredient_line l ON l.id=a.line_id JOIN recipe r ON r.id=l.recipe_id WHERE r.slug='other'"
            ),
            [("quinoa",)],
        )
