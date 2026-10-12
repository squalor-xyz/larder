# SPDX-License-Identifier: AGPL-3.0-only
import sqlite3
import tempfile
import tomllib
import unittest
from datetime import datetime
from importlib import resources
from pathlib import Path
from unittest import mock

from larder import db
from larder.config import load_settings

REPO_ROOT = Path(__file__).resolve().parent.parent

MVP_TABLES = {
    "person",
    "device",
    "recipe_source",
    "recipe",
    "recipe_meta",
    "item",
    "item_alias",
    "unit",
    "unit_alias",
    "ingredient_line",
    "parse_override",
    "meal_plan",
    "planned_meal",
    "shopping_list",
    "list_item",
    "staple",
}


class DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        self.settings = load_settings({"LARDER_DATA": str(self.data_dir)})

    def connect(self) -> sqlite3.Connection:
        conn = db.connect(self.settings)
        self.addCleanup(conn.close)
        return conn

    def migrated(self) -> sqlite3.Connection:
        conn = self.connect()
        db.migrate(conn)
        return conn

    def source(self, files: dict[str, str]) -> Path:
        """Write a migrations directory and return it."""
        directory = Path(tempfile.mkdtemp(dir=self.data_dir))
        for name, sql in files.items():
            (directory / name).write_text(sql, encoding="utf-8")
        return directory

    @staticmethod
    def table_names(conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        return {row["name"] for row in rows}

    @staticmethod
    def master(conn: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        )
        return [tuple(row) for row in rows]


class ConnectTest(DbTestCase):
    def test_wal_mode_enabled(self) -> None:
        conn = self.connect()

        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

        self.assertEqual(mode, "wal")

    def test_connect_rejects_non_wal_journal_mode(self) -> None:
        real_connect = sqlite3.connect

        def in_memory(_path: object, **kwargs: bool) -> sqlite3.Connection:
            return real_connect(":memory:", **kwargs)

        with (
            mock.patch("larder.db.sqlite3.connect", side_effect=in_memory),
            self.assertRaises(db.DatabaseError),
        ):
            db.connect(self.settings)

    def test_busy_timeout_set(self) -> None:
        # sqlite3.connect defaults to a 5 s timeout; zero it so only connect()
        # can account for the value.
        real_connect = sqlite3.connect

        def no_default_timeout(path: object, **kwargs: object) -> sqlite3.Connection:
            return real_connect(path, **{**kwargs, "timeout": 0})

        with mock.patch("larder.db.sqlite3.connect", side_effect=no_default_timeout):
            conn = self.connect()

        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_connect_does_not_migrate(self) -> None:
        conn = self.connect()

        self.assertEqual(self.table_names(conn), set())

    def test_rows_are_addressable_by_name(self) -> None:
        conn = self.connect()

        row = conn.execute("SELECT 1 AS one").fetchone()

        self.assertEqual(row["one"], 1)


class MigrateTest(DbTestCase):
    def test_migrate_creates_all_mvp_tables(self) -> None:
        conn = self.connect()

        db.migrate(conn)

        self.assertEqual(self.table_names(conn), MVP_TABLES | {"schema_migrations"})

    def test_migrate_is_idempotent(self) -> None:
        conn = self.connect()
        first = db.migrate(conn)
        before = self.master(conn)

        second = db.migrate(conn)

        self.assertEqual(first, [1])
        self.assertEqual(second, [])
        self.assertEqual(self.master(conn), before)

    def test_migrations_apply_in_order_and_record_version(self) -> None:
        # 0002 is written first; it needs the table from 0001.
        source = self.source(
            {
                "0002_add_row.sql": "INSERT INTO thing (label) VALUES ('seed');",
                "0001_thing.sql": "CREATE TABLE thing (label TEXT NOT NULL) STRICT;",
            }
        )
        conn = self.connect()

        applied = db.migrate(conn, source)

        self.assertEqual(applied, [1, 2])
        rows = conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual(
            [(row["version"], row["name"]) for row in rows],
            [(1, "0001_thing.sql"), (2, "0002_add_row.sql")],
        )
        for row in rows:
            stamp = datetime.strptime(row["applied_at"], "%Y-%m-%dT%H:%M:%SZ")
            self.assertIsNotNone(stamp)
        self.assertEqual(conn.execute("SELECT label FROM thing").fetchone()[0], "seed")

    def test_migrate_applies_only_pending_versions(self) -> None:
        conn = self.connect()
        files = {"0001_thing.sql": "CREATE TABLE thing (n INTEGER) STRICT;"}
        self.assertEqual(db.migrate(conn, self.source(files)), [1])
        files["0002_other.sql"] = "CREATE TABLE other (n INTEGER) STRICT;"

        applied = db.migrate(conn, self.source(files))

        self.assertEqual(applied, [2])

    def test_schema_version_reports_highest_applied_version(self) -> None:
        conn = self.connect()
        source = self.source(
            {
                "0001_a.sql": "CREATE TABLE a (n INTEGER) STRICT;",
                "0002_b.sql": "CREATE TABLE b (n INTEGER) STRICT;",
            }
        )
        db.migrate(conn, source)

        self.assertEqual(db.schema_version(conn), 2)

    def test_concurrent_migrate_skips_version_applied_by_other_connection(self) -> None:
        # The second connection reads the recorded versions (none yet). Just
        # before its first transaction, the first connection migrates fully.
        # The second must re-check inside its transaction and skip version 1
        # instead of running the script again.
        first = self.connect()
        second = self.connect()
        real_transaction = db.transaction
        first_applied: list[list[int]] = []
        interleaved = False

        def other_connection_migrates_first(conn: sqlite3.Connection) -> object:
            nonlocal interleaved
            if not interleaved:
                interleaved = True  # set first: the nested migrate calls us too
                first_applied.append(db.migrate(first))
            return real_transaction(conn)

        with mock.patch(
            "larder.db.transaction", side_effect=other_connection_migrates_first
        ):
            applied = db.migrate(second)

        self.assertEqual(first_applied, [[1]])
        self.assertEqual(applied, [])
        self.assertIn("person", self.table_names(second))

    def test_ignores_non_sql_entries(self) -> None:
        source = self.source(
            {
                "0001_thing.sql": "CREATE TABLE thing (n INTEGER) STRICT;",
                "README.txt": "not a migration",
                "notes.md": "also not",
            }
        )
        (source / "subdir.sql").mkdir()
        conn = self.connect()

        self.assertEqual(db.migrate(conn, source), [1])

    def test_failed_migration_rolls_back(self) -> None:
        source = self.source(
            {
                "0001_ok.sql": "CREATE TABLE ok (n INTEGER) STRICT;",
                "0002_bad.sql": (
                    "CREATE TABLE half (n INTEGER) STRICT;\n"
                    "INSERT INTO half (n) VALUES (1);\n"
                    "INSERT INTO missing_table (n) VALUES (1);\n"
                ),
            }
        )
        conn = self.connect()

        with self.assertRaises(db.DatabaseError) as raised:
            db.migrate(conn, source)

        self.assertIn("0002_bad.sql", str(raised.exception))
        self.assertEqual(self.table_names(conn), {"ok", "schema_migrations"})
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        self.assertEqual(versions, [1])
        self.assertFalse(conn.in_transaction)

    def test_rejects_bad_migration_name_or_duplicate_version(self) -> None:
        good = "CREATE TABLE thing (n INTEGER) STRICT;"
        cases = {
            "short version": {"001_init.sql": good},
            "no version": {"init.sql": good},
            "uppercase": {"0001_Init.sql": good},
            "dash": {"0001-init.sql": good},
            "no name": {"0001_.sql": good},
            "duplicate version": {"0001_a.sql": good, "0001_b.sql": good},
        }
        for label, files in cases.items():
            with self.subTest(label):
                conn = self.connect()
                good_and_bad = {"0003_fine.sql": "CREATE TABLE fine (n INTEGER);"}
                good_and_bad.update(files)

                with self.assertRaises(db.DatabaseError):
                    db.migrate(conn, self.source(good_and_bad))

                # Rejected before anything was applied.
                self.assertNotIn("fine", self.table_names(conn))
                self.assertNotIn("thing", self.table_names(conn))

    def test_rejects_db_newer_than_code(self) -> None:
        conn = self.connect()
        db.migrate(
            conn,
            self.source(
                {
                    "0001_a.sql": "CREATE TABLE a (n INTEGER) STRICT;",
                    "0002_b.sql": "CREATE TABLE b (n INTEGER) STRICT;",
                }
            ),
        )

        with self.assertRaises(db.DatabaseError) as raised:
            db.migrate(conn, self.source({"0001_a.sql": "CREATE TABLE a (n INTEGER);"}))

        self.assertIn("2", str(raised.exception))

    def test_migrations_ship_as_package_data(self) -> None:
        default = resources.files("larder") / "migrations"
        names = {entry.name for entry in default.iterdir()}
        self.assertIn("0001_init.sql", names)

        pyproject = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        package_data = pyproject["tool"]["setuptools"]["package-data"]
        self.assertIn("migrations/*.sql", package_data["larder"])

    def test_shipped_migration_names_are_valid(self) -> None:
        default = resources.files("larder") / "migrations"
        sql_names = sorted(e.name for e in default.iterdir() if e.name.endswith(".sql"))

        self.assertTrue(sql_names)
        for name in sql_names:
            self.assertRegex(name, r"^\d{4}_[a-z0-9_]+\.sql$")
        versions = [int(name[:4]) for name in sql_names]
        self.assertEqual(versions, list(range(1, len(versions) + 1)))

    def test_shipped_migrations_start_with_license_header(self) -> None:
        default = resources.files("larder") / "migrations"
        for entry in default.iterdir():
            if entry.name.endswith(".sql"):
                first = entry.read_text(encoding="utf-8").splitlines()[0]
                self.assertEqual(first, "-- SPDX-License-Identifier: AGPL-3.0-only")


class TransactionTest(DbTestCase):
    def test_transaction_commits_on_success(self) -> None:
        conn = self.migrated()

        with db.transaction(conn) as tx:
            tx.execute("INSERT INTO person (name) VALUES ('Ada')")

        self.assertFalse(conn.in_transaction)
        other = self.connect()
        self.assertEqual(other.execute("SELECT name FROM person").fetchone()[0], "Ada")

    def test_transaction_rolls_back_on_error(self) -> None:
        conn = self.migrated()

        with self.assertRaises(RuntimeError), db.transaction(conn) as tx:
            tx.execute("INSERT INTO person (name) VALUES ('Ada')")
            raise RuntimeError("boom")

        self.assertFalse(conn.in_transaction)
        self.assertIsNone(conn.execute("SELECT name FROM person").fetchone())

    def test_transaction_takes_write_lock_immediately(self) -> None:
        conn = self.migrated()
        other = self.connect()
        other.execute("PRAGMA busy_timeout = 0")

        # No write yet inside the block, but a second writer must be locked out.
        with (
            db.transaction(conn),
            self.assertRaises(sqlite3.OperationalError) as raised,
        ):
            other.execute("INSERT INTO person (name) VALUES ('Bob')")

        self.assertIn("locked", str(raised.exception))


class SchemaTest(DbTestCase):
    def test_foreign_keys_enforced(self) -> None:
        conn = self.migrated()

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO device (person_id, label, token_hash, paired_at) "
                "VALUES (999, 'phone', 'hash', '2026-01-01T00:00:00Z')"
            )

    def test_person_with_device_cannot_be_deleted(self) -> None:
        conn = self.migrated()
        conn.execute("INSERT INTO person (id, name) VALUES (1, 'Ada')")
        conn.execute(
            "INSERT INTO device (person_id, label, token_hash, paired_at) "
            "VALUES (1, 'phone', 'hash', '2026-01-01T00:00:00Z')"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM person WHERE id = 1")

    def test_deleting_recipe_cascades_to_meta_and_ingredient_lines(self) -> None:
        conn = self.migrated()
        conn.execute("INSERT INTO recipe_source (kind, location) VALUES ('x', 'y')")
        conn.execute(
            "INSERT INTO recipe (id, source_id, slug, title, source_ref, "
            "content_hash, body) VALUES (1, 1, 'soup', 'Soup', 'r', 'h', 'b')"
        )
        conn.execute("INSERT INTO recipe_meta (recipe_id) VALUES (1)")
        conn.execute(
            "INSERT INTO ingredient_line (recipe_id, position, raw_text, confidence) "
            "VALUES (1, 0, '1 onion', 0.5)"
        )

        conn.execute("DELETE FROM recipe WHERE id = 1")

        for table in ("recipe_meta", "ingredient_line"):
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)

    def test_planned_recipe_cannot_be_deleted(self) -> None:
        conn = self.migrated()
        conn.execute("INSERT INTO recipe_source (kind, location) VALUES ('x', 'y')")
        conn.execute(
            "INSERT INTO recipe (id, source_id, slug, title, source_ref, "
            "content_hash, body) VALUES (1, 1, 'soup', 'Soup', 'r', 'h', 'b')"
        )
        conn.execute("INSERT INTO meal_plan (id, week_start) VALUES (1, '2026-10-12')")
        conn.execute(
            "INSERT INTO planned_meal (meal_plan_id, date, recipe_id) "
            "VALUES (1, '2026-10-13', 1)"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM recipe WHERE id = 1")

    def test_deleting_person_clears_list_item_added_by(self) -> None:
        conn = self.migrated()
        conn.execute("INSERT INTO person (id, name) VALUES (1, 'Ada')")
        conn.execute(
            "INSERT INTO shopping_list (id, created_at) VALUES (1, '2026-10-12T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO list_item (list_id, free_text, source, added_by, added_at) "
            "VALUES (1, 'milk', 'manual', 1, '2026-10-12T00:00:00Z')"
        )

        conn.execute("DELETE FROM person WHERE id = 1")

        row = conn.execute("SELECT added_by FROM list_item").fetchone()
        self.assertIsNone(row["added_by"])

    def test_list_item_requires_item_or_free_text(self) -> None:
        conn = self.migrated()
        conn.execute(
            "INSERT INTO shopping_list (id, created_at) VALUES (1, '2026-10-12T00:00:00Z')"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO list_item (list_id, source, added_at) "
                "VALUES (1, 'manual', '2026-10-12T00:00:00Z')"
            )
        conn.execute(
            "INSERT INTO list_item (list_id, free_text, source, added_at) "
            "VALUES (1, 'milk', 'manual', '2026-10-12T00:00:00Z')"
        )

    def test_only_one_active_shopping_list(self) -> None:
        conn = self.migrated()
        conn.execute(
            "INSERT INTO shopping_list (created_at) VALUES ('2026-10-01T00:00:00Z')"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO shopping_list (created_at) VALUES ('2026-10-02T00:00:00Z')"
            )

        # Archived lists do not count against the limit.
        conn.execute("UPDATE shopping_list SET archived_at = '2026-10-02T00:00:00Z'")
        conn.execute(
            "INSERT INTO shopping_list (created_at) VALUES ('2026-10-02T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO shopping_list (created_at, archived_at) "
            "VALUES ('2026-10-03T00:00:00Z', '2026-10-04T00:00:00Z')"
        )

    def test_enum_and_boolean_columns_reject_bad_values(self) -> None:
        conn = self.migrated()
        bad = {
            "unit dimension": "INSERT INTO unit (name, dimension) VALUES ('x', 'time')",
            "person active": "INSERT INTO person (name, active) VALUES ('x', 2)",
            "meal status": (
                "INSERT INTO planned_meal (meal_plan_id, date, recipe_id, status) "
                "VALUES (1, '2026-10-13', 1, 'eaten')"
            ),
            "list source": (
                "INSERT INTO list_item (list_id, free_text, source, added_at) "
                "VALUES (1, 'x', 'barter', '2026-10-12T00:00:00Z')"
            ),
            "servings": "INSERT INTO recipe_meta (recipe_id, servings) VALUES (1, 0)",
            "confidence": (
                "INSERT INTO ingredient_line (recipe_id, position, raw_text, "
                "confidence) VALUES (1, 0, 'x', 1.5)"
            ),
        }
        conn.execute("PRAGMA foreign_keys = OFF")
        for label, sql in bad.items():
            with self.subTest(label):
                with self.assertRaises(sqlite3.IntegrityError) as raised:
                    conn.execute(sql)
                self.assertIn("CHECK", str(raised.exception))

    def test_all_tables_are_strict(self) -> None:
        conn = self.migrated()

        rows = conn.execute(
            "SELECT name, strict FROM pragma_table_list "
            "WHERE schema = 'main' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()

        self.assertEqual(
            {row["name"] for row in rows}, MVP_TABLES | {"schema_migrations"}
        )
        self.assertEqual([row["name"] for row in rows if not row["strict"]], [])

    def test_every_foreign_key_column_is_indexed(self) -> None:
        conn = self.migrated()
        missing: list[str] = []
        for table in sorted(MVP_TABLES):
            pk_cols = [
                r["name"]
                for r in conn.execute(f"PRAGMA table_info({table})")
                if r["pk"]
            ]
            indexed = set(pk_cols) if len(pk_cols) == 1 else set()
            for index in conn.execute(f"PRAGMA index_list({table})"):
                first = conn.execute(f"PRAGMA index_info({index['name']})").fetchone()
                indexed.add(first["name"])
            for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):
                if fk["from"] not in indexed:
                    missing.append(f"{table}.{fk['from']}")
        self.assertEqual(missing, [])

    def test_every_foreign_key_names_an_on_delete_action(self) -> None:
        conn = self.migrated()
        implicit: list[str] = []
        for table in sorted(MVP_TABLES):
            for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):
                if fk["on_delete"] == "NO ACTION":
                    implicit.append(f"{table}.{fk['from']}")
        self.assertEqual(implicit, [])


if __name__ == "__main__":
    unittest.main()
