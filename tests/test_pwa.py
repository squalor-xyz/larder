# SPDX-License-Identifier: AGPL-3.0-only
import importlib
import json
import mimetypes
import re
import struct
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from larder.web import create_app, pwa_views
from tests.test_web_shell import REPO_ROOT, WebShellTestCase

STATIC = REPO_ROOT / "src" / "larder" / "web" / "static"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JS_TYPES = {"text/javascript", "application/javascript"}


def png_size(path: Path) -> tuple[int, int]:
    """Read the width and height from a PNG's IHDR chunk."""
    data = path.read_bytes()
    assert data[:8] == PNG_SIGNATURE, f"{path.name} is not a PNG"
    length, kind, width, height = struct.unpack(">I4sII", data[8:24])
    assert (length, kind) == (13, b"IHDR"), f"{path.name} has no IHDR first"
    return width, height


def precache_list(source: str) -> list[str]:
    found = re.search(r"const PRECACHE\s*=\s*\[(.*?)\]", source, re.DOTALL)
    assert found is not None, "sw.js has no PRECACHE list"
    return re.findall(r"""["']([^"']+)["']""", found.group(1))


class ManifestTest(WebShellTestCase):
    def test_manifest_served_with_correct_mime_and_required_fields(self) -> None:
        app = create_app(self.settings())

        response = self.get(app, "/static/manifest.webmanifest")
        self.addCleanup(response.close)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/manifest+json")
        manifest = json.loads(response.get_data(as_text=True))
        self.assertEqual(manifest["name"], "larder")
        self.assertEqual(manifest["short_name"], "larder")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["background_color"], "#0f1419")
        self.assertEqual(manifest["theme_color"], "#0f1419")
        icons = {
            (icon["sizes"], icon["purpose"]): icon["src"] for icon in manifest["icons"]
        }
        self.assertEqual(
            icons,
            {
                ("192x192", "any"): "/static/icons/larder-192.png",
                ("512x512", "any"): "/static/icons/larder-512.png",
                ("512x512", "maskable"): "/static/icons/larder-maskable-512.png",
            },
        )
        for icon in manifest["icons"]:
            self.assertEqual(icon["type"], "image/png")

    def test_manifest_type_is_registered_when_the_platform_lacks_it(self) -> None:
        with mock.patch.dict(mimetypes.types_map):
            mimetypes.types_map.pop(".webmanifest", None)
            self.assertIsNone(mimetypes.guess_type("a.webmanifest")[0])

            importlib.reload(pwa_views)

            self.assertEqual(
                mimetypes.guess_type("a.webmanifest")[0], "application/manifest+json"
            )

    def test_icons_192_and_512_and_maskable_present(self) -> None:
        self.assertEqual(png_size(STATIC / "icons" / "larder-192.png"), (192, 192))
        self.assertEqual(png_size(STATIC / "icons" / "larder-512.png"), (512, 512))
        self.assertEqual(
            png_size(STATIC / "icons" / "larder-maskable-512.png"), (512, 512)
        )


class ServiceWorkerTest(WebShellTestCase):
    def test_service_worker_served_from_root_scope(self) -> None:
        app = create_app(self.settings())

        response = self.get(app, "/sw.js")
        self.addCleanup(response.close)

        self.assertEqual(response.status_code, 200)
        self.assertIn(response.mimetype, JS_TYPES)
        self.assertEqual(response.headers.get("Cache-Control"), "no-cache")
        self.assertIn("fetch", response.get_data(as_text=True))

    def test_manifest_and_service_worker_served_without_a_device_cookie(self) -> None:
        app = create_app(self.settings())
        paths = (
            "/static/manifest.webmanifest",
            "/sw.js",
            "/static/register-sw.js",
            "/static/icons/larder-192.png",
            "/static/icons/larder-512.png",
            "/static/icons/larder-maskable-512.png",
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.get(app, path)
                self.addCleanup(response.close)

                self.assertEqual(response.status_code, 200)

    def test_service_worker_source_never_caches_a_non_ok_redirected_or_no_store_response(
        self,
    ) -> None:
        text = (STATIC / "sw.js").read_text(encoding="utf-8")
        # Comments mention these words too; only the code counts.
        source = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)

        self.assertRegex(source, r"!response\.ok\b")
        self.assertRegex(source, r"\bresponse\.redirected\b")
        self.assertRegex(source, r"""\.includes\(["']no-store["']\)""")
        never = re.search(r"const NEVER_CACHE\s*=\s*\[(.*?)\]", source, re.DOTALL)
        assert never is not None, "sw.js has no NEVER_CACHE list"
        self.assertEqual(
            re.findall(r"""["']([^"']+)["']""", never.group(1)), ["/pair/", "/admin/"]
        )
        # The list is applied to what gets stored and to what gets intercepted.
        self.assertGreaterEqual(source.count("NEVER_CACHE.some("), 2)

    def test_unpaired_401_page_is_not_served_from_a_cache_policy(self) -> None:
        app = create_app(self.settings())

        response = self.get(app, "/")

        self.assertEqual(response.status_code, 401)
        cache_control = response.headers.get("Cache-Control", "")
        self.assertNotIn("public", cache_control)
        precache = precache_list((STATIC / "sw.js").read_text(encoding="utf-8"))
        self.assertTrue(precache)
        self.assertNotIn("/", precache)
        for entry in precache:
            self.assertTrue(entry.startswith("/static/"), entry)


class BaseTemplateTest(WebShellTestCase):
    def test_base_template_links_the_manifest_and_registers_the_worker_from_a_file(
        self,
    ) -> None:
        app = create_app(self.settings())

        # The unpaired page renders base.html and is the first page a device sees.
        response = self.get(app, "/")

        body = response.get_data(as_text=True)
        self.assertIn('<link rel="manifest" href="/static/manifest.webmanifest">', body)
        self.assertIn('<meta name="theme-color" content="#0f1419">', body)
        self.assertIn('<script src="/static/register-sw.js" defer></script>', body)
        self.assertEqual(len(re.findall(r"<script\b", body)), 1)
        register = (STATIC / "register-sw.js").read_text(encoding="utf-8")
        self.assertIn('"/sw.js"', register)


class PackageDataTest(unittest.TestCase):
    def test_icons_and_static_subdirectories_ship_as_package_data(self) -> None:
        pyproject = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        patterns = pyproject["tool"]["setuptools"]["package-data"]["larder"]

        self.assertIn("web/static/*/*", patterns)
        package_root = REPO_ROOT / "src" / "larder"
        shipped = {path.name for path in package_root.glob("web/static/*/*")}
        self.assertEqual(
            shipped, {"larder-192.png", "larder-512.png", "larder-maskable-512.png"}
        )


if __name__ == "__main__":
    unittest.main()
