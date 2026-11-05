# SPDX-License-Identifier: AGPL-3.0-only
"""Recipe browsing and metadata through paired household clients."""

import re
from unittest import mock

from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from larder import db
from larder.recipes.index import index_recipes
from larder.web import create_app
from tests.test_web_shell import WebShellTestCase


class RecipesWebTest(WebShellTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app(self.settings())
        self.client = self.paired_client(self.app)
        self.member = self.paired_client(self.app, "Sam")
        self.conn = db.connect(self.settings())
        self.addCleanup(self.conn.close)
        self.files = self.settings().recipes_dir / "recipes"
        self.files.mkdir()
        self.write("soup", "Soup")
        self.write("toast", "Toast")
        self.write("cake", "Cake")
        index_recipes(self.settings())
        self.ids = dict(self.conn.execute("SELECT slug,id FROM recipe"))
        self.conn.execute("UPDATE recipe_meta SET meal_type='dinner'")
        self.conn.execute(
            "UPDATE recipe_meta SET in_rotation=0 WHERE recipe_id=?",
            (self.ids["toast"],),
        )
        self.conn.execute(
            "UPDATE recipe_meta SET meal_type='dessert' WHERE recipe_id=?",
            (self.ids["cake"],),
        )
        self.path = f"/recipes/{self.ids['soup']}"
        self.edit = f"/admin/recipes/{self.ids['soup']}/meta"

    def write(self, slug: str, title: str, extra: str = "") -> None:
        (self.files / f"{slug}.md").write_text(
            f'+++\ntitle="{title}"\n+++\nA **warm** meal.\n\n## Ingredients\n- 2 onions\n- <script>alert(1)</script>\n\n## Directions\n1. **Cook** slowly.\n2. Serve <img src=x onerror=alert(1)>.\n{extra}'
        )

    def post(
        self, path: str, fields: dict, client: FlaskClient | None = None
    ) -> TestResponse:
        client = client or self.client
        with client.session_transaction() as session:
            token = session["csrf"]
        return client.post(path, data={"csrf_token": token, **fields})

    def metadata(self) -> tuple:
        return tuple(
            self.conn.execute(
                "SELECT meal_type,servings,in_rotation,favorite,effort FROM recipe_meta WHERE recipe_id=?",
                (self.ids["soup"],),
            ).fetchone()
        )

    def fields(self) -> dict:
        return {
            "meal_type": "lunch",
            "servings": "6",
            "favorite": "1",
            "effort": "quick",
        }

    def test_list_filters_by_meal_type_and_rotation(self) -> None:
        for query, expected in (
            ("", ["Cake", "Soup", "Toast"]),
            ("?meal_type=dinner", ["Soup", "Toast"]),
            ("?in_rotation=1", ["Cake", "Soup"]),
            ("?in_rotation=0", ["Toast"]),
            ("?meal_type=dinner&in_rotation=1", ["Soup"]),
        ):
            with self.subTest(query=query):
                page = self.member.get("/recipes" + query)
                self.assertEqual(page.status_code, 200)
                self.assertEqual(
                    re.findall(r'<a href="/recipes/\d+">([^<]+)</a>', page.text),
                    expected,
                )

    def test_view_renders_ingredients_and_directions_safely(self) -> None:
        page = self.member.get(self.path)
        self.assertEqual(page.status_code, 200)
        for fragment in (
            "<p>A <strong>warm</strong> meal.</p>",
            "<h2>Ingredients</h2>",
            "<ul>",
            "<li>2 onions</li>",
            "<h2>Directions</h2>",
            "<ol>",
            "<li><strong>Cook</strong> slowly.</li>",
            "&lt;script&gt;alert(1)&lt;/script&gt;",
            "&lt;img src=x onerror=alert(1)&gt;",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page.text)
        self.assertNotIn("<script>alert", page.text)
        self.assertNotIn("<img src=x", page.text)

    def test_admin_can_edit_meta_member_cannot(self) -> None:
        page = self.client.get(self.edit)
        self.assertEqual(page.status_code, 200)
        for field in (
            "csrf_token",
            "meal_type",
            "servings",
            "in_rotation",
            "favorite",
            "effort",
        ):
            with self.subTest(field=field):
                self.assertIn(f'name="{field}"', page.text)
        self.assertEqual(self.post(self.edit, self.fields()).status_code, 303)
        self.assertEqual(self.metadata(), ("lunch", 6, 0, 1, "quick"))
        self.assertEqual(self.member.get(self.edit).status_code, 403)
        self.assertEqual(
            self.post(self.edit, {"meal_type": "dinner"}, self.member).status_code, 403
        )
        self.assertEqual(self.metadata(), ("lunch", 6, 0, 1, "quick"))
        self.assertNotIn("Edit metadata", self.member.get(self.path).text)
        self.assertIn("Edit metadata", self.client.get(self.path).text)

    def test_meta_survives_reindex(self) -> None:
        self.assertEqual(self.post(self.edit, self.fields()).status_code, 303)
        self.write("soup", "New soup", "Changed directions.")
        index_recipes(self.settings())
        self.assertEqual(self.metadata(), ("lunch", 6, 0, 1, "quick"))
        page = self.member.get(self.path).text
        self.assertIn("New soup", page)
        self.assertIn("Changed directions.", page)

    def test_bulk_rotation_changes_only_displayed_rows(self) -> None:
        page = self.client.get("/recipes?meal_type=dinner")
        for slug, checked in (("soup", True), ("toast", False)):
            with self.subTest(slug=slug):
                self.assertIn(f'name="page_id" value="{self.ids[slug]}"', page.text)
                self.assertRegex(
                    page.text,
                    rf'name="in_rotation" value="{self.ids[slug]}"'
                    + (r" checked" if checked else r"(?! checked)"),
                )
        response = self.post(
            "/admin/recipes/rotation?meal_type=dinner",
            {
                "page_id": [str(self.ids["soup"]), str(self.ids["toast"])],
                "in_rotation": [str(self.ids["toast"])],
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            [
                tuple(r)
                for r in self.conn.execute(
                    "SELECT r.slug,m.in_rotation FROM recipe r JOIN recipe_meta m ON m.recipe_id=r.id ORDER BY r.slug"
                )
            ],
            [("cake", 1), ("soup", 0), ("toast", 1)],
        )
        self.assertNotIn('name="page_id"', self.member.get("/recipes").text)
        self.assertEqual(
            self.post("/admin/recipes/rotation", {}, self.member).status_code, 403
        )

    def test_writes_require_csrf(self) -> None:
        self.assertEqual(self.client.get(self.edit).status_code, 200)
        for path in (self.edit, "/admin/recipes/rotation"):
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.post(path, data=self.fields()).status_code, 400
                )
        self.assertEqual(self.metadata(), ("dinner", None, 1, 0, None))

    def test_invalid_metadata_does_not_write(self) -> None:
        for changed in (
            {"meal_type": "snack"},
            {"effort": "hard"},
            {"servings": "0"},
            {"servings": "-1"},
            {"servings": "2.5"},
            {"servings": "9999999999999999999999999"},
        ):
            with self.subTest(changed=changed):
                response = self.post(self.edit, self.fields() | changed)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.metadata(), ("dinner", None, 1, 0, None))
        self.assertEqual(
            self.post(
                self.edit,
                {"meal_type": "", "servings": "", "effort": "", "in_rotation": "1"},
            ).status_code,
            303,
        )
        self.assertEqual(self.metadata(), (None, None, 1, 0, None))

    def test_member_browse_navigation_and_today_link(self) -> None:
        self.assertIn('href="/recipes"', self.member.get("/").text)
        self.conn.execute(
            "INSERT INTO meal_plan (id,week_start) VALUES (1,'2026-11-02')"
        )
        self.conn.execute(
            "INSERT INTO planned_meal (meal_plan_id,date,recipe_id) VALUES (1,'2026-11-05',?)",
            (self.ids["soup"],),
        )
        with mock.patch("larder.web.home_views.today") as today:
            today.return_value.isoformat.return_value = "2026-11-05"
            page = self.member.get("/")
        self.assertIn(f'href="{self.path}">View recipe</a>', page.text)
        self.assertEqual(self.app.test_client().get("/recipes").status_code, 401)
        self.assertEqual(self.app.test_client().get(self.path).status_code, 401)

    def test_unknown_and_archived_recipes_are_not_available(self) -> None:
        self.assertEqual(self.member.get(self.path).status_code, 200)
        for recipe_id in (self.ids["soup"], 999999):
            with self.subTest(recipe_id=recipe_id):
                self.conn.execute(
                    "UPDATE recipe SET archived=1 WHERE id=?", (recipe_id,)
                )
                self.assertEqual(
                    self.member.get(f"/recipes/{recipe_id}").status_code, 404
                )
                self.assertEqual(
                    self.client.get(f"/admin/recipes/{recipe_id}/meta").status_code, 404
                )
                self.assertEqual(
                    self.post(
                        f"/admin/recipes/{recipe_id}/meta", self.fields()
                    ).status_code,
                    404,
                )
        self.assertNotIn(f'href="{self.path}"', self.member.get("/recipes").text)

    def test_bulk_rejects_rows_outside_filter(self) -> None:
        for fields in (
            {"page_id": [str(self.ids["cake"])]},
            {
                "page_id": [str(self.ids["soup"])],
                "in_rotation": [str(self.ids["cake"])],
            },
            {"page_id": ["invalid"]},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(
                    self.post(
                        "/admin/recipes/rotation?meal_type=dinner", fields
                    ).status_code,
                    400,
                )
                self.assertEqual(self.metadata(), ("dinner", None, 1, 0, None))
