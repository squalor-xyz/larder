# SPDX-License-Identifier: AGPL-3.0-only
"""Household settings and the data directory layout.

Settings come from ``$LARDER_DATA/larder.toml`` plus ``LARDER_*`` environment
overrides. Loading only reads the config file; it creates nothing.
"""

import ipaddress
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Role = Literal["admin", "member"]
Autonomy = Literal["L0", "L1"]
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

ROLES: tuple[str, ...] = ("admin", "member")
AUTONOMY_LEVELS: tuple[str, ...] = ("L0", "L1")

DATA_ENV = "LARDER_DATA"
CONFIG_FILE_NAME = "larder.toml"

_FILE_KEYS = frozenset(
    {
        "timezone",
        "recipe_repo_url",
        "category_order",
        "notify_channels",
        "secrets",
        "people",
        "stores",
        "trusted_proxies",
    }
)
_PERSON_KEYS = frozenset(
    {"name", "role", "autonomy", "email", "matrix_id", "notify_channel"}
)
_STORE_KEYS = frozenset({"name", "senders"})

# Secret key -> (LARDER_* override, default file name under secrets/).
_SECRET_FIELDS: dict[str, tuple[str, str]] = {
    "deploy_key": ("LARDER_SECRETS_DEPLOY_KEY", "deploy_key"),
    "imap_password_file": ("LARDER_SECRETS_IMAP_PASSWORD_FILE", "imap_password"),
    "ntfy_token_file": ("LARDER_SECRETS_NTFY_TOKEN_FILE", "ntfy_token"),
}

# The only LARDER_* variables that override a setting. LARDER_DATA is separate.
_ENV_OVERRIDES = frozenset(
    {"LARDER_TIMEZONE", "LARDER_RECIPE_REPO_URL"}
    | {env_name for env_name, _ in _SECRET_FIELDS.values()}
)

_TOML_NAMES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "float",
    bool: "boolean",
    list: "array",
    dict: "table",
}


class ConfigError(Exception):
    """A setting is missing, unknown or the wrong type.

    The message names the key and where it came from (a file path or an
    environment variable name).
    """


@dataclass(frozen=True, slots=True)
class Person:
    name: str
    role: Role = "member"
    autonomy: Autonomy = "L0"
    email: str | None = None
    matrix_id: str | None = None
    notify_channel: str | None = None


@dataclass(frozen=True, slots=True)
class Store:
    name: str
    senders: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Secrets:
    """Paths to secret files. The files are never read or printed here."""

    deploy_key: Path = field(repr=False)
    imap_password_file: Path = field(repr=False)
    ntfy_token_file: Path = field(repr=False)

    def __repr__(self) -> str:
        return "Secrets(<redacted>)"


@dataclass(frozen=True, slots=True)
class Settings:
    """Loaded settings. ``timezone`` None means the host's local time."""

    data_dir: Path
    timezone: ZoneInfo | None
    recipe_repo_url: str | None
    people: tuple[Person, ...]
    stores: tuple[Store, ...]
    category_order: tuple[str, ...]
    notify_channels: tuple[str, ...]
    secrets: Secrets
    # Peers whose forwarded headers larder trusts. File only; no env override.
    trusted_proxies: tuple[IPNetwork, ...] = ()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "larder.db"

    @property
    def recipes_dir(self) -> Path:
        return self.data_dir / "recipes"

    @property
    def emails_dir(self) -> Path:
        return self.data_dir / "emails"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from ``LARDER_DATA/larder.toml`` and the environment.

    ``env`` defaults to ``os.environ``, read at call time. An environment
    override wins over the file. Raises ConfigError on any problem.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    data_dir = _data_dir(environ)
    _check_env_names(environ)

    config_path = data_dir / CONFIG_FILE_NAME
    origin = str(config_path)
    raw = _read_config(config_path) if config_path.exists() else {}
    _check_keys(raw, _FILE_KEYS, "top level", origin)

    timezone = _scalar(environ, raw, "timezone", "timezone", "LARDER_TIMEZONE", origin)
    recipe_repo_url = _scalar(
        environ,
        raw,
        "recipe_repo_url",
        "recipe_repo_url",
        "LARDER_RECIPE_REPO_URL",
        origin,
    )
    secrets_table = _as_table(raw.get("secrets", {}), "secrets", origin)
    _check_keys(secrets_table, frozenset(_SECRET_FIELDS), "[secrets]", origin)

    return Settings(
        data_dir=data_dir,
        timezone=None if timezone is None else _zone(*timezone),
        recipe_repo_url=None if recipe_repo_url is None else recipe_repo_url[0],
        people=_people(raw.get("people", []), origin),
        stores=_stores(raw.get("stores", []), origin),
        category_order=_as_str_list(
            raw.get("category_order", []), "category_order", origin
        ),
        notify_channels=_as_str_list(
            raw.get("notify_channels", []), "notify_channels", origin
        ),
        secrets=Secrets(
            deploy_key=_secret_path(
                "deploy_key", secrets_table, environ, data_dir, origin
            ),
            imap_password_file=_secret_path(
                "imap_password_file", secrets_table, environ, data_dir, origin
            ),
            ntfy_token_file=_secret_path(
                "ntfy_token_file", secrets_table, environ, data_dir, origin
            ),
        ),
        trusted_proxies=_trusted_proxies(raw.get("trusted_proxies", []), origin),
    )


def ensure_data_dirs(settings: Settings) -> None:
    """Create ``recipes/``, ``emails/`` and ``secrets/`` if missing. Idempotent.

    The data dir itself must already exist. ``secrets/`` is created with mode
    0o700.
    """
    settings.recipes_dir.mkdir(exist_ok=True)
    settings.emails_dir.mkdir(exist_ok=True)
    settings.secrets_dir.mkdir(mode=0o700, exist_ok=True)


def _data_dir(environ: Mapping[str, str]) -> Path:
    raw = environ.get(DATA_ENV, "")
    if not raw.strip():
        raise ConfigError(f"{DATA_ENV} is required: set it to the data directory")
    data_dir = Path(raw)
    if not data_dir.is_dir():
        raise ConfigError(f"{DATA_ENV} is not an existing directory: {raw}")
    return data_dir


def _check_env_names(environ: Mapping[str, str]) -> None:
    allowed = _ENV_OVERRIDES | {DATA_ENV}
    for name in sorted(environ):
        if name.startswith("LARDER_") and name not in allowed:
            raise ConfigError(
                f"{name}: unknown LARDER_* environment variable; "
                f"allowed are {', '.join(sorted(allowed))}"
            )


def _read_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config file: {exc}") from exc


def _scalar(
    environ: Mapping[str, str],
    table: Mapping[str, object],
    key: str,
    label: str,
    env_name: str,
    origin: str,
) -> tuple[str, str] | None:
    """Return a string setting as (value, where it came from), or None if unset.

    An environment override wins over the file value.
    """
    if env_name in environ:
        return _as_str(environ[env_name], label, env_name), env_name
    if key in table:
        return _as_str(table[key], label, origin), origin
    return None


def _zone(name: str, origin: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"{origin}: '{name}' is not a known IANA time zone name"
        ) from exc


def _secret_path(
    key: str,
    table: Mapping[str, object],
    environ: Mapping[str, str],
    data_dir: Path,
    origin: str,
) -> Path:
    env_name, default_name = _SECRET_FIELDS[key]
    value = _scalar(environ, table, key, f"secrets.{key}", env_name, origin)
    if value is None:
        return data_dir / "secrets" / default_name
    # A relative path resolves against the data dir; an absolute one replaces it.
    return data_dir / value[0]


def _trusted_proxies(value: object, origin: str) -> tuple[IPNetwork, ...]:
    entries = _as_str_list(value, "trusted_proxies", origin)
    networks: list[IPNetwork] = []
    for index, text in enumerate(entries):
        try:
            # strict: "10.0.0.1/24" is an error, not a silently widened network.
            networks.append(ipaddress.ip_network(text))
        except ValueError as exc:
            raise ConfigError(
                f"{origin}: trusted_proxies[{index}] must be an IP address or "
                f"network such as 10.0.0.0/8, got '{text}'"
            ) from exc
    return tuple(networks)


def _people(value: object, origin: str) -> tuple[Person, ...]:
    tables = _tables(value, "people", origin)
    return tuple(_person(table, index, origin) for index, table in enumerate(tables))


def _person(table: Mapping[str, Any], index: int, origin: str) -> Person:
    label = f"people[{index}]"
    _check_keys(table, _PERSON_KEYS, label, origin)
    return Person(
        name=_required_name(table, label, origin),
        role=cast(
            Role,
            _as_choice(table.get("role", "member"), ROLES, f"{label}.role", origin),
        ),
        autonomy=cast(
            Autonomy,
            _as_choice(
                table.get("autonomy", "L0"),
                AUTONOMY_LEVELS,
                f"{label}.autonomy",
                origin,
            ),
        ),
        email=_optional_str(table, "email", label, origin),
        matrix_id=_optional_str(table, "matrix_id", label, origin),
        notify_channel=_optional_str(table, "notify_channel", label, origin),
    )


def _stores(value: object, origin: str) -> tuple[Store, ...]:
    tables = _tables(value, "stores", origin)
    return tuple(_store(table, index, origin) for index, table in enumerate(tables))


def _store(table: Mapping[str, Any], index: int, origin: str) -> Store:
    label = f"stores[{index}]"
    _check_keys(table, _STORE_KEYS, label, origin)
    return Store(
        name=_required_name(table, label, origin),
        senders=_as_str_list(table.get("senders", []), f"{label}.senders", origin),
    )


def _tables(value: object, label: str, origin: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ConfigError(
            f"{origin}: {label} must be an array of tables ([[{label}]]), "
            f"got {_type_name(value)}"
        )
    return value


def _required_name(table: Mapping[str, Any], label: str, origin: str) -> str:
    if "name" not in table:
        raise ConfigError(f"{origin}: {label}.name is required")
    return _as_str(table["name"], f"{label}.name", origin)


def _optional_str(
    table: Mapping[str, Any], key: str, label: str, origin: str
) -> str | None:
    if key not in table:
        return None
    return _as_str(table[key], f"{label}.{key}", origin)


def _check_keys(
    table: Mapping[str, object], allowed: frozenset[str], label: str, origin: str
) -> None:
    for key in table:
        if key not in allowed:
            raise ConfigError(
                f"{origin}: unknown key '{key}' in {label}; "
                f"allowed keys are {', '.join(sorted(allowed))}"
            )


def _as_str(value: object, label: str, origin: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(
            f"{origin}: {label} must be a string, got {_type_name(value)}"
        )
    if not value.strip():
        raise ConfigError(f"{origin}: {label} must not be empty")
    return value


def _as_str_list(value: object, label: str, origin: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(
            f"{origin}: {label} must be an array of strings, got {_type_name(value)}"
        )
    return tuple(_as_str(item, f"{label}[{i}]", origin) for i, item in enumerate(value))


def _as_choice(value: object, choices: tuple[str, ...], label: str, origin: str) -> str:
    text = _as_str(value, label, origin)
    if text not in choices:
        raise ConfigError(
            f"{origin}: {label} must be one of {', '.join(choices)}, got '{text}'"
        )
    return text


def _as_table(value: object, label: str, origin: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{origin}: {label} must be a table, got {_type_name(value)}")
    return value


def _type_name(value: object) -> str:
    return _TOML_NAMES.get(type(value), type(value).__name__)
