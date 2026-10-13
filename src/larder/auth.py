# SPDX-License-Identifier: AGPL-3.0-only
"""People, device pairing and tokens (ADR 0010).

Tokens come from ``secrets`` and only their SHA-256 hashes are stored. The web
layer in ``larder.web.auth_views`` calls these functions; SQL is in ``auth_db``.
"""

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import segno

from larder import auth_db, db
from larder.config import ConfigError, Settings

LOG = logging.getLogger("larder.auth")

PAIR_TOKEN_TTL = timedelta(minutes=10)
LAST_SEEN_THROTTLE = timedelta(hours=1)
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 3600
DEFAULT_DEVICE_LABEL = "New device"
MAX_LABEL_LENGTH = 80
SESSION_KEY_FILE = "session_key"
SESSION_KEY_BYTES = 32

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now() -> datetime:
    """The current time in UTC. Tests replace this to move the clock."""
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(_TIME_FORMAT)


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, _TIME_FORMAT).replace(tzinfo=UTC)


def new_token() -> str:
    """Return a new random URL-safe token (256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest that is stored in place of a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: str, supplied: str) -> bool:
    """Compare two secrets in constant time."""
    return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


def clean_label(label: str) -> str:
    """Trim a device label to a sane length; empty if nothing is left."""
    return label.strip()[:MAX_LABEL_LENGTH]


def admin_names(settings: Settings) -> list[str]:
    return [person.name for person in settings.people if person.role == "admin"]


def require_admin(settings: Settings) -> None:
    """Raise ConfigError unless config names at least one admin."""
    if not admin_names(settings):
        raise ConfigError(
            "no admin in the config: mark at least one [[people]] entry "
            'with role = "admin" in larder.toml'
        )


def sync_people(conn: sqlite3.Connection, settings: Settings) -> None:
    """Mirror config people into the ``person`` table. Never deletes a row."""
    require_admin(settings)
    with db.transaction(conn):
        auth_db.sync_people(conn, [person.name for person in settings.people])


def ensure_bootstrap(conn: sqlite3.Connection, settings: Settings) -> str | None:
    """Log a one-time bootstrap link while no admin has an active device.

    Replaces any unused bootstrap token. Returns the token, or None when an
    admin device already exists. This is the only place a token is logged.
    """
    with db.transaction(conn):
        if auth_db.admin_has_active_device(conn, admin_names(settings)):
            auth_db.delete_unused_bootstrap_tokens(conn)
            return None
        token = new_token()
        auth_db.delete_unused_bootstrap_tokens(conn)
        auth_db.insert_token(
            conn, hash_token(token), "bootstrap", None, _now_iso(), None
        )
    LOG.warning(
        "No admin device is paired yet. Open this path on larder's usual "
        "address to pair the first device (single use): /pair/%s",
        token,
    )
    return token


def bootstrap_person_id(
    conn: sqlite3.Connection, settings: Settings, name: str
) -> int | None:
    """Return the id of the config admin ``name``, or None if it is not one."""
    if name not in admin_names(settings):
        return None
    person = auth_db.active_person(conn, name)
    return None if person is None else int(person["id"])


def create_pair_token(conn: sqlite3.Connection, person_name: str) -> str:
    """Create a 10-minute, single-use pairing token for an active person.

    Raises LookupError when no active person has that name.
    """
    created = now()
    token = new_token()
    with db.transaction(conn):
        person = auth_db.active_person(conn, person_name)
        if person is None:
            raise LookupError(person_name)
        auth_db.insert_token(
            conn,
            hash_token(token),
            "pair",
            person["id"],
            iso(created),
            iso(created + PAIR_TOKEN_TTL),
        )
    return token


def peek_token(
    conn: sqlite3.Connection, token: str, admins: Sequence[str]
) -> sqlite3.Row | None:
    """Return a usable pairing token's kind and person without using it up.

    A bootstrap token is dead once an admin has an active device, so the page
    and the consuming POST agree.
    """
    link = auth_db.peek_token(conn, hash_token(token), _now_iso())
    if (
        link is not None
        and link["kind"] == "bootstrap"
        and auth_db.admin_has_active_device(conn, admins)
    ):
        return None
    return link


class _DeadLink(Exception):
    """Raised inside the pairing transaction to roll it back."""


def complete_pairing(
    conn: sqlite3.Connection,
    token: str,
    label: str,
    bootstrap_person_id: int | None,
    admins: Sequence[str],
) -> str | None:
    """Use a pairing token up and create a device. Return its new device token.

    ``bootstrap_person_id`` is the admin picked on a bootstrap link; a person's
    own link ignores it. ``admins`` are the config admins: a bootstrap link
    stops working once one of them has an active device. Returns None, and
    changes nothing, when the token is unknown, used, expired, dead or its
    person is inactive.
    """
    moment = _now_iso()
    device_token = new_token()
    try:
        with db.transaction(conn):
            _pair(conn, token, label, bootstrap_person_id, admins, moment, device_token)
    except _DeadLink:
        return None
    return device_token


def _pair(
    conn: sqlite3.Connection,
    token: str,
    label: str,
    bootstrap_person_id: int | None,
    admins: Sequence[str],
    moment: str,
    device_token: str,
) -> None:
    used = auth_db.consume_token(conn, hash_token(token), moment)
    if used is None:
        raise _DeadLink
    if used["kind"] == "bootstrap" and auth_db.admin_has_active_device(conn, admins):
        raise _DeadLink  # rolls the consume back
    person_id = used["person_id"]
    if person_id is None:
        person_id = bootstrap_person_id
    if person_id is None:
        raise ValueError("a bootstrap link needs a person")
    auth_db.insert_device(
        conn,
        person_id,
        clean_label(label) or DEFAULT_DEVICE_LABEL,
        hash_token(device_token),
        moment,
    )


def device_for_cookie(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """Return the active device (and person) for a cookie value, or None."""
    return auth_db.device_for_token(conn, hash_token(token))


def touch_device(conn: sqlite3.Connection, device: sqlite3.Row) -> bool:
    """Record a sighting if ``last_seen`` is stale. Return True if it did."""
    moment = now()
    seen = device["last_seen"]
    if seen is not None and moment - parse_iso(seen) < LAST_SEEN_THROTTLE:
        return False
    with db.transaction(conn):
        auth_db.touch_device(conn, device["id"], iso(moment))
    return True


def list_devices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return auth_db.list_devices(conn)


def rename_device(conn: sqlite3.Connection, device_id: int, label: str) -> bool:
    with db.transaction(conn):
        return auth_db.rename_device(conn, device_id, label)


def revoke_device(conn: sqlite3.Connection, device_id: int) -> bool:
    with db.transaction(conn):
        return auth_db.revoke_device(conn, device_id, _now_iso())


def load_session_key(settings: Settings) -> bytes:
    """Return the Flask secret key from ``secrets/session_key``.

    A missing file is created atomically with mode 0600: the bytes are written
    to a private temporary file that is then linked into place, so a
    concurrent starter never reads a half-written key. A file system error is
    a ConfigError that names the path.
    """
    path = settings.secrets_dir / SESSION_KEY_FILE
    try:
        try:
            key = path.read_bytes()
        except FileNotFoundError:
            key = _create_session_key(path)
    except OSError as exc:
        raise ConfigError(
            f"{path}: cannot read or create the session key: {exc}"
        ) from exc
    if len(key) < SESSION_KEY_BYTES:
        raise ConfigError(
            f"{path}: session_key must hold at least {SESSION_KEY_BYTES} bytes; "
            "delete the file to have larder create a new one"
        )
    return key


def _create_session_key(path: Path) -> bytes:
    key = secrets.token_bytes(SESSION_KEY_BYTES)
    temp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        try:
            os.link(temp, path)
        except FileExistsError:
            # Another process won the race: use its key.
            return path.read_bytes()
    finally:
        temp.unlink()
    return key


def qr_svg(link: str) -> str:
    """Return ``link`` as an inline SVG QR code.

    The markup has no style attributes, scripts or links, so the page's CSP
    stays as it is. The light background keeps it scannable in dark mode.
    """
    code = segno.make(link, error="m")
    svg: str = code.svg_inline(
        scale=5, border=4, dark="#000", light="#fff", omitsize=True
    )
    return svg


def _now_iso() -> str:
    return iso(now())
