# SPDX-License-Identifier: AGPL-3.0-only
"""Recipe files mirror into SQLite without replacing household metadata."""

import hashlib
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from larder import db
from larder.config import load_settings
from larder.ingredients.match import seed_items


class RecipeIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = load_settings({"LARDER_DATA": tmp.name})
        self.files = self.settings.recipes_dir / "recipes"
        self.files.mkdir(parents=True)
        self.conn = db.connect(self.settings)
        self.addCleanup(self.conn.close)
        db.migrate(self.conn)
        seed_items(self.conn)

    def index(self) -> None:
        from larder.recipes.index import index_recipes

        index_recipes(self.settings)

    def write(
        self, slug: str = "soup", title: str = "Soup", ingredient: str = "2 onions"
    ) -> str:
        text = f'+++\ntitle = "{title}"\n+++\nIntro\n## Ingredients\n- {ingredient}\n## Directions\nCook.\n'
        (self.files / f"{slug}.md").write_text(text)
        return text

    def rows(self, table: str) -> list[tuple]:
        return [tuple(row) for row in self.conn.execute(f"SELECT * FROM {table}")]

    def seed_meta(self) -> tuple[list[tuple], list[tuple]]:
        with db.transaction(self.conn):
            self.conn.execute("UPDATE recipe_meta SET servings=7, favorite=1")
            self.conn.execute(
                "INSERT INTO parse_override (normalized_text, qty, notes) VALUES ('2 onions', 2, 'confirmed')"
            )
        return self.rows("recipe_meta"), self.rows("parse_override")

    def test_new_file_inserts_recipe_and_raw_lines(self) -> None:
        fixtures = Path(__file__).parent / "fixtures" / "hugo"
        for path in fixtures.glob("*.md"):
            (self.files / path.name).write_bytes(path.read_bytes())
        text = self.write()
        self.index()
        recipe = self.conn.execute("SELECT * FROM recipe WHERE slug='soup'").fetchone()
        self.assertEqual(
            (
                recipe["title"],
                recipe["source_ref"],
                recipe["content_hash"],
                recipe["body"],
                recipe["archived"],
            ),
            (
                "Soup",
                "recipes/soup.md",
                hashlib.sha256(text.encode()).hexdigest(),
                text.split("+++\n", 2)[2],
                0,
            ),
        )
        self.assertEqual(self.rows("recipe_source"), [(1, "recipe-repo", "recipes")])
        lines = self.conn.execute(
            "SELECT position, raw_text, qty, confidence, confirmed FROM ingredient_line WHERE recipe_id=?",
            (recipe["id"],),
        ).fetchall()
        self.assertEqual([tuple(row) for row in lines], [(1, "2 onions", 2.0, 0.7, 0)])
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM recipe").fetchone()[0], 4
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM recipe_meta").fetchone()[0], 4
        )

    def test_unchanged_hash_is_noop(self) -> None:
        self.write()
        self.index()
        with db.transaction(self.conn):
            self.conn.execute(
                "UPDATE ingredient_line SET confirmed=1, qty=2, notes='reviewed'"
            )
        before = {
            table: self.rows(table)
            for table in ("recipe", "recipe_meta", "ingredient_line")
        }
        self.index()
        self.assertEqual({table: self.rows(table) for table in before}, before)

    def test_changed_file_updates_content_keeps_meta(self) -> None:
        self.write()
        self.index()
        meta, overrides = self.seed_meta()
        self.write(ingredient="3 carrots")
        self.index()
        self.assertEqual(
            tuple(self.conn.execute("SELECT title, body FROM recipe").fetchone()),
            ("Soup", "Intro\n## Ingredients\n- 3 carrots\n## Directions\nCook.\n"),
        )
        self.assertEqual(self.rows("recipe_meta"), meta)
        self.assertEqual(self.rows("parse_override"), overrides)
        self.assertEqual(
            [
                tuple(row)
                for row in self.conn.execute(
                    "SELECT raw_text, qty, confirmed FROM ingredient_line"
                )
            ],
            [("3 carrots", 3.0, 0)],
        )

    def test_renamed_slug_handling(self) -> None:
        self.write()
        self.index()
        meta, _ = self.seed_meta()
        (self.files / "soup.md").rename(self.files / "stew.md")
        self.index()
        self.assertEqual(
            [
                tuple(row)
                for row in self.conn.execute(
                    "SELECT slug, archived FROM recipe ORDER BY id"
                )
            ],
            [("soup", 1), ("stew", 0)],
        )
        self.assertEqual(self.rows("recipe_meta"), meta + [(2, None, None, 1, 0, None)])

    def test_deleted_file_archives_recipe(self) -> None:
        self.write()
        self.index()
        meta, overrides = self.seed_meta()
        (self.files / "soup.md").unlink()
        self.index()
        self.assertEqual(
            self.conn.execute("SELECT archived FROM recipe").fetchone()[0], 1
        )
        self.write()
        self.index()
        self.assertEqual(
            self.conn.execute("SELECT archived FROM recipe").fetchone()[0], 0
        )
        self.assertEqual(self.rows("recipe_meta"), meta)
        self.assertEqual(self.rows("parse_override"), overrides)

    def test_draft_file_is_not_indexed(self) -> None:
        text = self.write(slug="draft")
        (self.files / "draft.md").write_text(
            text.replace('title = "Soup"', 'title = "Soup"\ndraft = true')
        )
        self.index()
        self.assertEqual(self.rows("recipe"), [])
        self.write()
        self.index()
        meta, _ = self.seed_meta()
        text = (self.files / "soup.md").read_text()
        (self.files / "soup.md").write_text(
            text.replace('title = "Soup"', 'title = "Soup"\ndraft = true')
        )
        self.index()
        self.assertEqual(
            [
                tuple(row)
                for row in self.conn.execute("SELECT slug, archived FROM recipe")
            ],
            [("soup", 1)],
        )
        self.assertEqual(self.rows("recipe_meta"), meta)

    def test_bad_file_is_skipped_and_rest_indexed(self) -> None:
        from larder.recipes.index import index_recipes

        cases = {
            "missing_title": b'+++\nsecret="PRIVATE_CONTENT"\n+++\n',
            "parse_error": b"PRIVATE_CONTENT without front matter",
            "invalid_utf8": b"\xff\xfePRIVATE_CONTENT",
            "directory": None,
        }
        good = '+++\ntitle="Good"\n+++\n## Ingredients\n- 2 onions\n'
        for case, broken in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                settings = load_settings({"LARDER_DATA": temporary})
                files = settings.recipes_dir / "recipes"
                files.mkdir(parents=True)
                with closing(db.connect(settings)) as conn:
                    db.migrate(conn)
                    seed_items(conn)
                    for slug in ("soup", "bad"):
                        (files / f"{slug}.md").write_text(good)
                    index_recipes(settings)
                    with db.transaction(conn):
                        conn.execute("UPDATE recipe_meta SET servings=7, favorite=1")
                    meta = [
                        tuple(row) for row in conn.execute("SELECT * FROM recipe_meta")
                    ]
                    bad = tuple(
                        conn.execute("SELECT * FROM recipe WHERE slug='bad'").fetchone()
                    )
                    lines = [
                        tuple(row)
                        for row in conn.execute(
                            "SELECT * FROM ingredient_line WHERE recipe_id=?", (bad[0],)
                        )
                    ]
                    (files / "fresh.md").write_text(good)
                    failing_slug = "x" if case == "directory" else "bad"
                    if broken is None:
                        (files / "x.md").mkdir()
                    else:
                        (files / "bad.md").write_bytes(broken)
                    with self.assertLogs("larder.recipes.index", level="ERROR") as logs:
                        index_recipes(settings)
                    self.assertEqual(
                        tuple(
                            conn.execute(
                                "SELECT slug, archived FROM recipe WHERE slug='fresh'"
                            ).fetchone()
                        ),
                        ("fresh", 0),
                    )
                    self.assertEqual(
                        [
                            tuple(row)
                            for row in conn.execute(
                                "SELECT * FROM recipe_meta WHERE recipe_id IN (SELECT id FROM recipe WHERE slug IN ('soup', 'bad'))"
                            )
                        ],
                        meta,
                    )
                    self.assertEqual(
                        tuple(
                            conn.execute(
                                "SELECT * FROM recipe WHERE slug='bad'"
                            ).fetchone()
                        ),
                        bad,
                    )
                    self.assertEqual(
                        [
                            tuple(row)
                            for row in conn.execute(
                                "SELECT * FROM ingredient_line WHERE recipe_id=?",
                                (bad[0],),
                            )
                        ],
                        lines,
                    )
                    self.assertEqual(
                        logs.output,
                        [
                            f"ERROR:larder.recipes.index:Skipping recipe {failing_slug!r}: unreadable or invalid recipe file."
                        ],
                    )
