# SPDX-License-Identifier: AGPL-3.0-only
"""``python -m larder``: run the web app, or pair a device from the command line.

Without a subcommand this runs the Flask development server. It is not for
production: put a TLS reverse proxy in front of it (ADR 0015). The production
WSGI server is chosen when the container is built.

``python -m larder pair --person NAME`` prints a one-time pairing link for a
person in the config (ADR 0010). It is the recovery path when every admin
device is lost.
"""

import argparse
import sys
from collections.abc import Sequence

from larder import auth, db
from larder.config import ConfigError, Settings, ensure_data_dirs, load_settings
from larder.db import DatabaseError
from larder.web import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dev server. Return 2 and print to stderr on a config or DB error."""
    parser = argparse.ArgumentParser(
        prog="python -m larder",
        description="Run the larder web app on the Flask development server.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"address to listen on (default {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to listen on (default {DEFAULT_PORT})",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    pair = commands.add_parser(
        "pair", help="print a one-time pairing link for a person in the config"
    )
    pair.add_argument("--person", required=True, help="name of a person in larder.toml")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        if args.command == "pair":
            return _pair(settings, args.person)
        app = create_app(settings)
    except (ConfigError, DatabaseError) as exc:
        print(f"larder: {exc}", file=sys.stderr)
        return 2
    app.run(host=args.host, port=args.port, debug=False)
    return 0


def _pair(settings: Settings, person: str) -> int:
    """Print a pairing path for ``person`` (10 minutes, single use)."""
    auth.require_admin(settings)
    if person not in {p.name for p in settings.people}:
        names = ", ".join(p.name for p in settings.people)
        print(
            f"larder: no person named '{person}' in the config ({names})",
            file=sys.stderr,
        )
        return 2
    ensure_data_dirs(settings)
    conn = db.connect(settings)
    try:
        db.migrate(conn)
        auth.sync_people(conn, settings)
        token = auth.create_pair_token(conn, person)
    finally:
        conn.close()
    print(f"/pair/{token}")
    print("Open this path on larder's usual address within 10 minutes. It works once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
