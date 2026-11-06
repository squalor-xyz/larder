# SPDX-License-Identifier: AGPL-3.0-only
from larder import auth, db
from larder.web import create_app
from tests.test_web_shell import CSRF_FIELD, WebShellTestCase


class HomeTest(WebShellTestCase):
    def test_unpaired_device_still_gets_401_at_root(self) -> None:
        app = create_app(self.settings())

        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("You're paired", response.get_data(as_text=True))

    def test_pairing_redirect_lands_on_a_200_page(self) -> None:
        app = create_app(self.settings())
        conn = db.connect(self.settings())
        try:
            token = auth.create_pair_token(conn, "Ada")
        finally:
            conn.close()
        client = app.test_client()
        page = client.get(f"/pair/{token}")
        csrf = CSRF_FIELD.search(page.get_data(as_text=True))
        assert csrf is not None, "pair page has no CSRF field"
        done = client.post(
            f"/pair/{token}", data={"csrf_token": csrf.group(1), "label": "Test"}
        )
        self.assertEqual(done.status_code, 303)
        location = done.headers["Location"]

        landed = client.get(location)

        self.assertEqual(landed.status_code, 200)
        self.assertIn("Tonight’s dinner", landed.get_data(as_text=True))
