# SPDX-License-Identifier: AGPL-3.0-only
"""SQL for people, devices and pairing tokens (ADR 0010).

Plain functions that take a connection first. Writes assume the caller has
opened ``db.transaction()``. Times are ISO-8601 UTC text, so they compare as
strings.
"""

import sqlite3
from collections.abc import Sequence

# A link is usable while unused, unexpired and, for a person's link, while that
# person is active. Shared by the read-only peek and the consuming update.
_USABLE = (
    "token_hash = :hash AND used_at IS NULL "
    "AND (expires_at IS NULL OR expires_at > :now) "
    "AND (person_id IS NULL OR person_id IN (SELECT id FROM person WHERE active = 1))"
)


def sync_people(conn: sqlite3.Connection, names: Sequence[str]) -> None:
    """Make ``names`` the active people: add new ones, deactivate the rest."""
    for name in names:
        conn.execute(
            "INSERT INTO person (name, active) VALUES (?, 1) "
            "ON CONFLICT (name) DO UPDATE SET active = 1",
            (name,),
        )
    marks = ", ".join("?" for _ in names)
    conn.execute(
        f"UPDATE person SET active = 0 WHERE name NOT IN ({marks})", tuple(names)
    )


def active_person(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, name FROM person WHERE name = ? AND active = 1", (name,)
    ).fetchone()


def admin_has_active_device(conn: sqlite3.Connection, admins: Sequence[str]) -> bool:
    """Return True if an active person named in ``admins`` has an unrevoked device."""
    marks = ", ".join("?" for _ in admins)
    row = conn.execute(
        "SELECT 1 FROM device JOIN person ON person.id = device.person_id "
        f"WHERE device.revoked_at IS NULL AND person.active = 1 "
        f"AND person.name IN ({marks}) LIMIT 1",
        tuple(admins),
    ).fetchone()
    return row is not None


def delete_unused_bootstrap_tokens(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM pairing_token WHERE kind = 'bootstrap' AND used_at IS NULL"
    )


def insert_token(
    conn: sqlite3.Connection,
    token_hash: str,
    kind: str,
    person_id: int | None,
    created_at: str,
    expires_at: str | None,
) -> None:
    conn.execute(
        "INSERT INTO pairing_token "
        "(token_hash, kind, person_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (token_hash, kind, person_id, created_at, expires_at),
    )


def peek_token(
    conn: sqlite3.Connection, token_hash: str, now: str
) -> sqlite3.Row | None:
    """Return a usable token (kind, person name) without using it up."""
    return conn.execute(
        "SELECT kind, person_id, "
        "(SELECT name FROM person WHERE id = pairing_token.person_id) AS person_name "
        f"FROM pairing_token WHERE {_USABLE}",
        {"hash": token_hash, "now": now},
    ).fetchone()


def consume_token(
    conn: sqlite3.Connection, token_hash: str, now: str
) -> sqlite3.Row | None:
    """Mark a usable token used and return it, or None. Call inside a transaction."""
    params = {"hash": token_hash, "now": now}
    spent = conn.execute(
        f"UPDATE pairing_token SET used_at = :now WHERE {_USABLE}", params
    )
    if spent.rowcount != 1:
        return None
    return conn.execute(
        "SELECT kind, person_id FROM pairing_token WHERE token_hash = :hash", params
    ).fetchone()


def insert_device(
    conn: sqlite3.Connection, person_id: int, label: str, token_hash: str, now: str
) -> None:
    conn.execute(
        "INSERT INTO device (person_id, label, token_hash, paired_at, last_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        (person_id, label, token_hash, now, now),
    )


def device_for_token(conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
    """Return the unrevoked device with this hash if its person is active."""
    return conn.execute(
        "SELECT device.id AS id, device.last_seen AS last_seen, "
        "person.id AS person_id, person.name AS person_name "
        "FROM device JOIN person ON person.id = device.person_id "
        "WHERE device.token_hash = ? AND device.revoked_at IS NULL "
        "AND person.active = 1",
        (token_hash,),
    ).fetchone()


def touch_device(conn: sqlite3.Connection, device_id: int, now: str) -> None:
    conn.execute("UPDATE device SET last_seen = ? WHERE id = ?", (now, device_id))


def list_devices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT device.id AS id, device.label AS label, "
        "device.paired_at AS paired_at, device.last_seen AS last_seen, "
        "device.revoked_at AS revoked_at, person.name AS person_name "
        "FROM device JOIN person ON person.id = device.person_id "
        "ORDER BY device.revoked_at IS NOT NULL, person.name, device.id"
    ).fetchall()


def rename_device(conn: sqlite3.Connection, device_id: int, label: str) -> bool:
    """Set a device's label. Return False if there is no such device."""
    done = conn.execute("UPDATE device SET label = ? WHERE id = ?", (label, device_id))
    return done.rowcount == 1


def revoke_device(conn: sqlite3.Connection, device_id: int, now: str) -> bool:
    """Revoke a device, keeping the first revocation time. False if it is unknown."""
    done = conn.execute(
        "UPDATE device SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
        (now, device_id),
    )
    return done.rowcount == 1
