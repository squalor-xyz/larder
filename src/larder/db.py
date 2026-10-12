# SPDX-License-Identifier: AGPL-3.0-only
"""SQLite connection, transactions and numbered SQL migrations.

Migrations are ``NNNN_name.sql`` files in ``larder/migrations/``, shipped as
package data. ``migrate`` applies the pending ones in version order, each in
its own transaction, and records them in ``schema_migrations``. Connections use
``autocommit=True``, so every transaction is explicit (see ``transaction``).
"""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable

from larder.config import Settings

BUSY_TIMEOUT_MS = 5000

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class DatabaseError(Exception):
    """The database cannot be opened, configured or migrated."""


def connect(settings: Settings) -> sqlite3.Connection:
    """Open ``settings.db_path`` with foreign keys, WAL and a busy timeout.

    Rows come back as ``sqlite3.Row``. The connection is in autocommit mode;
    use ``transaction`` for writes. This does not migrate.
    """
    conn = sqlite3.connect(settings.db_path, autocommit=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if mode != "wal":
            raise DatabaseError(
                f"{settings.db_path}: could not enable WAL journal mode (got '{mode}')"
            )
    except sqlite3.Error as exc:
        conn.close()
        raise DatabaseError(f"{settings.db_path}: cannot configure: {exc}") from exc
    except DatabaseError:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in ``BEGIN IMMEDIATE``: commit on success, roll back on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:  # SQLite already rolled back after some errors
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection, source: Traversable | None = None) -> list[int]:
    """Apply pending migrations and return the versions applied, in order.

    ``source`` is a directory of ``NNNN_name.sql`` files; it defaults to the
    packaged ``larder/migrations``. Raises DatabaseError for a bad file name, a
    duplicate version, a recorded version that no file provides (the database is
    newer than this code) or a migration that fails; a failed migration leaves
    no trace.
    """
    if source is None:
        source = resources.files("larder") / "migrations"
    migrations = _discover(source)

    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "applied_at TEXT NOT NULL) STRICT"
    )
    recorded = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    unknown = sorted(recorded - migrations.keys())
    if unknown:
        raise DatabaseError(
            "database has migration version(s) "
            f"{', '.join(str(v) for v in unknown)} that this code does not provide; "
            "the database is newer than larder"
        )

    applied: list[int] = []
    for version in sorted(migrations.keys() - recorded):
        name, entry = migrations[version]
        try:
            sql = entry.read_text(encoding="utf-8")
            with transaction(conn):
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (version, name, _utc_now()),
                )
        except (sqlite3.Error, OSError) as exc:
            raise DatabaseError(f"migration {name} failed: {exc}") from exc
        applied.append(version)
    return applied


def _discover(source: Traversable) -> dict[int, tuple[str, Traversable]]:
    found: dict[int, tuple[str, Traversable]] = {}
    for entry in source.iterdir():
        if not entry.name.endswith(".sql") or not entry.is_file():
            continue
        match = _MIGRATION_NAME.match(entry.name)
        if match is None:
            raise DatabaseError(
                f"bad migration file name '{entry.name}'; expected NNNN_name.sql "
                "(four digits, then lowercase letters, digits and underscores)"
            )
        version = int(match.group(1))
        if version in found:
            raise DatabaseError(
                f"duplicate migration version {version}: "
                f"{found[version][0]} and {entry.name}"
            )
        found[version] = (entry.name, entry)
    return found


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
