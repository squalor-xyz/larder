# SPDX-License-Identifier: AGPL-3.0-only
"""The landing page for paired devices.

A placeholder until the Today view replaces its body and keeps the URL.
"""

import datetime

from flask import Blueprint, current_app, render_template

from larder import planner_db
from larder.config import Settings
from larder.web import SETTINGS_KEY, get_db

home = Blueprint("home", __name__)


def today(settings: Settings) -> datetime.date:
    """Return today's date in the household timezone (host local time if unset)."""
    return datetime.datetime.now(settings.timezone).date()


@home.get("/")
def index() -> str:
    """Say the device is paired and show tonight's planned meal, if any."""
    settings: Settings = current_app.config[SETTINGS_KEY]
    meal = planner_db.meal_on(get_db(), today(settings).isoformat())
    return render_template("home.html", meal=meal)
