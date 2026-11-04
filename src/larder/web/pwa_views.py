# SPDX-License-Identifier: AGPL-3.0-only
"""The service worker, served from the site root so its scope is ``/``.

The manifest and the other PWA files sit under ``/static/``, whose endpoint is
already open to unpaired devices. ``pwa.service_worker`` is open too
(``auth_views._OPEN_ENDPOINTS``): a browser fetches the worker before pairing.
"""

import mimetypes

from flask import Blueprint, Response, current_app, send_from_directory

# Some platforms' mime tables lack the manifest type.
mimetypes.add_type("application/manifest+json", ".webmanifest")

pwa = Blueprint("pwa", __name__)


@pwa.get("/sw.js")
def service_worker() -> Response:
    """Serve the worker. ``no-cache`` makes the browser revalidate it each time."""
    folder = current_app.static_folder
    assert folder is not None
    response = send_from_directory(folder, "sw.js", mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response
