# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only admin recipe sync status, with host-side recovery guidance.

Status reads local diagnostics without fetching or pushing. An admin fixes the
clone on the host; the next successful sync clears a needs-attention status.
Import this module before registering auth_views.admin.
"""

from flask import current_app, render_template

from larder.recipes import repo
from larder.web import SETTINGS_KEY, auth_views


@auth_views.admin.get("/recipe-repo")
def recipe_repo() -> str:
    """Show sync status without exposing the configured repository URL."""
    return render_template(
        "admin/recipe_repo.html",
        status=repo.status(current_app.config[SETTINGS_KEY]),
    )
