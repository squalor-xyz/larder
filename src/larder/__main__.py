# SPDX-License-Identifier: AGPL-3.0-only
"""``python -m larder``: run the web app on the Flask development server.

This is the Flask development server. It is not for production: put a TLS
reverse proxy in front of it (ADR 0015). The production WSGI server is chosen
when the container is built.
"""

import argparse
import sys
from collections.abc import Sequence

from larder.config import ConfigError, load_settings
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
    args = parser.parse_args(argv)

    try:
        app = create_app(load_settings())
    except (ConfigError, DatabaseError) as exc:
        print(f"larder: {exc}", file=sys.stderr)
        return 2
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
