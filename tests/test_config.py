# SPDX-License-Identifier: AGPL-3.0-only
import dataclasses
import ipaddress
import stat
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from larder.config import (
    ConfigError,
    Person,
    Secrets,
    Store,
    ensure_data_dirs,
    load_settings,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class LoadSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)

    def write_config(self, text: str) -> None:
        (self.data_dir / "larder.toml").write_text(text, encoding="utf-8")

    def env(self, **extra: str) -> dict[str, str]:
        return {"LARDER_DATA": str(self.data_dir), **extra}

    def test_loads_defaults_when_file_missing(self) -> None:
        settings = load_settings(self.env())

        self.assertEqual(settings.data_dir, self.data_dir)
        self.assertIsNone(settings.timezone)
        self.assertIsNone(settings.recipe_repo_url)
        self.assertEqual(settings.people, ())
        self.assertEqual(settings.stores, ())
        self.assertEqual(settings.category_order, ())
        self.assertEqual(settings.notify_channels, ())
        self.assertEqual(
            settings.secrets,
            Secrets(
                deploy_key=self.data_dir / "secrets" / "deploy_key",
                imap_password_file=self.data_dir / "secrets" / "imap_password",
                ntfy_token_file=self.data_dir / "secrets" / "ntfy_token",
            ),
        )

    def test_env_overrides_file_values(self) -> None:
        self.write_config(
            'timezone = "Europe/Berlin"\n'
            'recipe_repo_url = "https://git.example/file.git"\n'
            'category_order = ["produce", "dairy"]\n'
            "\n"
            "[secrets]\n"
            'deploy_key = "file-deploy-key"\n'
        )

        settings = load_settings(
            self.env(
                LARDER_TIMEZONE="UTC",
                LARDER_RECIPE_REPO_URL="https://git.example/env.git",
                LARDER_SECRETS_DEPLOY_KEY="env-deploy-key",
            )
        )

        self.assertEqual(settings.timezone, ZoneInfo("UTC"))
        self.assertEqual(settings.recipe_repo_url, "https://git.example/env.git")
        self.assertEqual(settings.secrets.deploy_key, self.data_dir / "env-deploy-key")
        # File values that no env var overrides are kept.
        self.assertEqual(settings.category_order, ("produce", "dairy"))

    def test_reads_file_values(self) -> None:
        self.write_config(
            'timezone = "Europe/Berlin"\n'
            'recipe_repo_url = "https://git.example/file.git"\n'
            'category_order = ["produce", "dairy"]\n'
            'notify_channels = ["ntfy", "email"]\n'
            "\n"
            "[secrets]\n"
            'imap_password_file = "file-imap"\n'
        )

        settings = load_settings(self.env())

        self.assertEqual(settings.timezone, ZoneInfo("Europe/Berlin"))
        self.assertEqual(settings.recipe_repo_url, "https://git.example/file.git")
        self.assertEqual(settings.category_order, ("produce", "dairy"))
        self.assertEqual(settings.notify_channels, ("ntfy", "email"))
        self.assertEqual(
            settings.secrets.imap_password_file, self.data_dir / "file-imap"
        )

    def test_rejects_empty_env_override(self) -> None:
        for name in ("LARDER_TIMEZONE", "LARDER_SECRETS_DEPLOY_KEY"):
            with self.subTest(name=name):
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(self.env(**{name: ""}))
                self.assertIn(name, str(ctx.exception))

    def test_settings_are_frozen(self) -> None:
        settings = load_settings(self.env())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            settings.timezone = ZoneInfo("UTC")

    def test_rejects_unknown_keys_with_clear_error(self) -> None:
        cases = {
            "bogus_key": "bogus_key = 1\n",
            "deploy_kye": '[secrets]\ndeploy_kye = "x"\n',
            "emial": '[[people]]\nname = "Alex"\nemial = "x"\n',
            "sendrs": '[[stores]]\nname = "example-store"\nsendrs = []\n',
        }
        for key, text in cases.items():
            with self.subTest(key=key):
                self.write_config(text)
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(self.env())
                self.assertIn(f"unknown key '{key}'", str(ctx.exception))
                self.assertIn(str(self.data_dir / "larder.toml"), str(ctx.exception))

    def test_data_dir_layout_paths_resolved(self) -> None:
        settings = load_settings(
            self.env(
                LARDER_SECRETS_IMAP_PASSWORD_FILE="custom/imap",
                LARDER_SECRETS_NTFY_TOKEN_FILE=str(
                    self.data_dir / "elsewhere" / "ntfy"
                ),
            )
        )

        self.assertEqual(settings.db_path, self.data_dir / "larder.db")
        self.assertEqual(settings.recipes_dir, self.data_dir / "recipes")
        self.assertEqual(settings.emails_dir, self.data_dir / "emails")
        self.assertEqual(settings.secrets_dir, self.data_dir / "secrets")
        self.assertEqual(
            settings.secrets.deploy_key, self.data_dir / "secrets" / "deploy_key"
        )
        # Relative secret paths resolve against the data dir; absolute ones stay.
        self.assertEqual(
            settings.secrets.imap_password_file, self.data_dir / "custom" / "imap"
        )
        self.assertEqual(
            settings.secrets.ntfy_token_file, self.data_dir / "elsewhere" / "ntfy"
        )

    def test_secret_values_not_in_repr(self) -> None:
        settings = load_settings(
            self.env(
                LARDER_SECRETS_DEPLOY_KEY="deploy-SECRETPATH-7f3a",
                LARDER_SECRETS_IMAP_PASSWORD_FILE="imap-SECRETPATH-91bc",
                LARDER_SECRETS_NTFY_TOKEN_FILE="ntfy-SECRETPATH-c2d4",
            )
        )

        for text in (repr(settings), str(settings), repr(settings.secrets)):
            for marker in ("7f3a", "91bc", "c2d4"):
                self.assertNotIn(f"SECRETPATH-{marker}", text)
        self.assertEqual(repr(settings.secrets), "Secrets(<redacted>)")
        self.assertIn("secrets=Secrets(<redacted>)", repr(settings))

    def test_requires_larder_data(self) -> None:
        for env in ({}, {"LARDER_DATA": ""}, {"LARDER_DATA": "   "}):
            with self.subTest(env=env):
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(env)
                self.assertIn("LARDER_DATA", str(ctx.exception))

    def test_requires_existing_data_dir(self) -> None:
        missing = self.data_dir / "missing"
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"LARDER_DATA": str(missing)})
        self.assertIn("LARDER_DATA", str(ctx.exception))

    def test_rejects_invalid_timezone(self) -> None:
        for name in ("Mars/Olympus", "../escape"):
            with self.subTest(name=name):
                self.write_config(f'timezone = "{name}"\n')
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(self.env())
                self.assertIn(name, str(ctx.exception))

        with self.assertRaises(ConfigError) as ctx:
            load_settings(self.env(LARDER_TIMEZONE="Not/AZone"))
        self.assertIn("LARDER_TIMEZONE", str(ctx.exception))
        self.assertIn("Not/AZone", str(ctx.exception))

    def test_timezone_defaults_to_host(self) -> None:
        self.write_config("")
        self.assertIsNone(load_settings(self.env()).timezone)

    def test_rejects_unknown_larder_env_var(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            load_settings(self.env(LARDER_RECIPE_URL="https://git.example/x.git"))
        self.assertIn("LARDER_RECIPE_URL", str(ctx.exception))

    def test_parses_people_and_stores(self) -> None:
        self.write_config(
            "[[people]]\n"
            'name = "Alex"\n'
            'role = "admin"\n'
            'autonomy = "L1"\n'
            'email = "alex@example.test"\n'
            'matrix_id = "@alex:matrix.example"\n'
            'notify_channel = "ntfy"\n'
            "\n"
            "[[people]]\n"
            'name = "Sam"\n'
            "\n"
            "[[stores]]\n"
            'name = "example-store"\n'
            'senders = ["orders@store.example"]\n'
        )

        settings = load_settings(self.env())

        self.assertEqual(
            settings.people,
            (
                Person(
                    name="Alex",
                    role="admin",
                    autonomy="L1",
                    email="alex@example.test",
                    matrix_id="@alex:matrix.example",
                    notify_channel="ntfy",
                ),
                Person(name="Sam"),
            ),
        )
        self.assertEqual(settings.people[1].role, "member")
        self.assertEqual(settings.people[1].autonomy, "L0")
        self.assertEqual(
            settings.stores,
            (Store(name="example-store", senders=("orders@store.example",)),),
        )

    def test_rejects_autonomy_above_l1(self) -> None:
        for level in ("L2", "L3"):
            with self.subTest(level=level):
                self.write_config(f'[[people]]\nname = "Alex"\nautonomy = "{level}"\n')
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(self.env())
                self.assertIn("people[0].autonomy", str(ctx.exception))

    def test_requires_person_name(self) -> None:
        self.write_config('[[people]]\nrole = "admin"\n')
        with self.assertRaises(ConfigError) as ctx:
            load_settings(self.env())
        self.assertIn("people[0].name is required", str(ctx.exception))

    def test_rejects_wrong_value_types(self) -> None:
        cases = {
            "category_order is a string": (
                'category_order = "produce"\n',
                "category_order",
            ),
            "timezone is an integer": ("timezone = 3\n", "timezone"),
            "secrets is a string": ('secrets = "x"\n', "secrets"),
            "people is a table": ('[people]\nname = "Alex"\n', "people"),
            "person name is an integer": ("[[people]]\nname = 3\n", "people[0].name"),
            "person role is unknown": (
                '[[people]]\nname = "Alex"\nrole = "owner"\n',
                "people[0].role",
            ),
            "store senders is a string": (
                '[[stores]]\nname = "s"\nsenders = "a@store.example"\n',
                "stores[0].senders",
            ),
        }
        for case, (text, label) in cases.items():
            with self.subTest(case=case):
                self.write_config(text)
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(self.env())
                self.assertIn(f"{label} must be", str(ctx.exception))

    def test_example_config_loads(self) -> None:
        example = (REPO_ROOT / "larder.example.toml").read_text(encoding="utf-8")
        self.write_config(example)

        settings = load_settings(self.env())

        self.assertTrue(settings.people)
        self.assertTrue(settings.stores)

    def test_parses_trusted_proxies_as_addresses_and_networks(self) -> None:
        self.write_config(
            'trusted_proxies = ["127.0.0.1", "10.0.0.0/8", "::1", "fd00::/8"]\n'
        )

        settings = load_settings(self.env())

        self.assertEqual(
            settings.trusted_proxies,
            (
                ipaddress.ip_network("127.0.0.1/32"),
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("::1/128"),
                ipaddress.ip_network("fd00::/8"),
            ),
        )

    def test_rejects_bad_trusted_proxy_entry(self) -> None:
        cases = {
            "host bits set": (
                'trusted_proxies = ["127.0.0.1", "10.0.0.1/24"]\n',
                "trusted_proxies[1]",
            ),
            "not an address": (
                'trusted_proxies = ["proxy.example"]\n',
                "trusted_proxies[0]",
            ),
            "empty string": ('trusted_proxies = [""]\n', "trusted_proxies[0]"),
            "not a list": (
                'trusted_proxies = "127.0.0.1"\n',
                "trusted_proxies must be",
            ),
            "entry not a string": (
                "trusted_proxies = [127]\n",
                "trusted_proxies[0] must be",
            ),
        }
        for case, (text, label) in cases.items():
            with self.subTest(case=case):
                self.write_config(text)
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(self.env())
                self.assertIn(label, str(ctx.exception))
                self.assertIn(str(self.data_dir / "larder.toml"), str(ctx.exception))

    def test_trusted_proxies_default_empty(self) -> None:
        self.assertEqual(load_settings(self.env()).trusted_proxies, ())


class EnsureDataDirsTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)

    def test_ensure_data_dirs_creates_layout_idempotently(self) -> None:
        settings = load_settings({"LARDER_DATA": str(self.data_dir)})
        self.assertFalse(settings.recipes_dir.exists())

        ensure_data_dirs(settings)
        ensure_data_dirs(settings)

        self.assertTrue(settings.recipes_dir.is_dir())
        self.assertTrue(settings.emails_dir.is_dir())
        self.assertTrue(settings.secrets_dir.is_dir())
        self.assertEqual(stat.S_IMODE(settings.secrets_dir.stat().st_mode) & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
