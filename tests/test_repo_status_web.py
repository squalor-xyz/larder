# SPDX-License-Identifier: AGPL-3.0-only
from unittest import mock

from larder.recipes.repo import RepoStatus
from larder.web import create_app
from tests.test_web_shell import WebShellTestCase

PATH = "/admin/recipe-repo"


class RepoStatusWebTests(WebShellTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app(self.settings())
        self.client = self.paired_client(self.app)

    def test_admin_sees_in_sync(self) -> None:
        with mock.patch(
            "larder.recipes.repo.status",
            return_value=RepoStatus(
                state="ok", last_sync_at="2026-11-05T12:34:56Z", ahead=2, behind=3
            ),
        ):
            response = self.client.get(PATH)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for text in (
            "In sync",
            "2026-11-05 12:34 UTC",
            "Ahead: 2",
            "Behind: 3",
            '<a href="/admin/recipe-repo">Recipe repo</a>',
        ):
            with self.subTest(text=text):
                self.assertIn(text, page)

    def test_admin_sees_needs_attention_with_detail_and_conflicted_files(self) -> None:
        with mock.patch(
            "larder.recipes.repo.status",
            return_value=RepoStatus(
                state="needs_attention",
                detail="Rebase needs a manual fix.",
                conflicted_files=("recipes/soup.md", "recipes/stew.md"),
            ),
        ):
            response = self.client.get(PATH)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for text in (
            "Needs attention",
            "Rebase needs a manual fix.",
            "recipes/soup.md",
            "recipes/stew.md",
            "Fix the clone in the recipes directory on the host.",
            "The next successful sync clears the status.",
        ):
            with self.subTest(text=text):
                self.assertIn(text, page)

    def test_admin_sees_not_cloned_yet(self) -> None:
        with mock.patch("larder.recipes.repo.status", return_value=RepoStatus()):
            response = self.client.get(PATH)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Not cloned yet", page)
        self.assertIn("Last sync: never", page)

    def test_member_gets_403(self) -> None:
        client = self.paired_client(self.app, "Sam")
        self.assertEqual(client.get(PATH).status_code, 403)

    def test_unpaired_device_gets_401(self) -> None:
        # The guard also protects unknown paths: prove this route exists first.
        with mock.patch("larder.recipes.repo.status", return_value=RepoStatus()):
            self.assertEqual(self.client.get(PATH).status_code, 200)
        self.assertEqual(self.app.test_client().get(PATH).status_code, 401)

    def test_detail_text_is_html_escaped(self) -> None:
        with mock.patch(
            "larder.recipes.repo.status",
            return_value=RepoStatus(
                state="needs_attention",
                detail='<script>alert("detail")</script>',
                conflicted_files=("<b>soup</b>.md",),
            ),
        ):
            response = self.client.get(PATH)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("&lt;script&gt;alert(&#34;detail&#34;)&lt;/script&gt;", page)
        self.assertIn("&lt;b&gt;soup&lt;/b&gt;.md", page)

    def test_page_has_no_form_and_never_shows_the_repo_url(self) -> None:
        self.write_config('recipe_repo_url = "https://example.invalid/private.git"\n')
        app = create_app(self.settings())
        client = self.paired_client(app)
        with mock.patch("larder.recipes.repo.status", return_value=RepoStatus()):
            response = client.get(PATH)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertNotIn("<form", page.lower())
        self.assertNotIn("https://example.invalid/private.git", page)
