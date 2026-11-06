# SPDX-License-Identifier: AGPL-3.0-only
"""Regenerate the active list from all confirmed, unended meal plans.

Unresolved parses retain their raw text and return warnings to the caller.
No pantry subtraction or automatic staple addition occurs here.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from fractions import Fraction

from flask import has_app_context

from larder import db, shopping_db
from larder.ingredients.units import REVIEW_THRESHOLD
from larder.planner import plan

# US customary volume in ml and avoirdupois mass in g. Packages never convert.
_FACTORS = {
    "tsp": ("volume", Fraction("4.92892159375")),
    "tbsp": ("volume", Fraction("14.78676478125")),
    "cup": ("volume", Fraction("236.5882365")),
    "ml": ("volume", Fraction(1)),
    "l": ("volume", Fraction(1000)),
    "oz": ("mass", Fraction("28.349523125")),
    "lb": ("mass", Fraction("453.59237")),
    "g": ("mass", Fraction(1)),
    "kg": ("mass", Fraction(1000)),
}

today = date.today


def _unit(name: str | None, unit_id: int | None) -> tuple[str | int | None, Fraction]:
    return _FACTORS.get(name, (unit_id, Fraction(1)))


def _key(item_id, text, name, unit_id):
    dimension, _ = _unit(name, unit_id)
    return item_id, text, dimension


@dataclass
class _Group:
    item_id: int | None
    text: str | None
    unit_id: int | None
    factor: Fraction
    total: Fraction | None = Fraction(0)
    recipes: set[int] = field(default_factory=set)


def regenerate(conn: sqlite3.Connection, *, today: date) -> list[str]:
    """Atomically replace plan rows; preserve checks only for unchanged totals.

    Unknown quantities make the aggregate unknown. Conversion chooses the
    largest contributing unit. Returned warnings are deduplicated raw lines.
    Caller must not already hold a transaction.
    """
    warnings: list[str] = []
    timestamp = datetime.now(UTC).isoformat()
    with db.transaction(conn):
        groups: dict[tuple, _Group] = {}
        for row in shopping_db.plan_lines(conn, today.isoformat()):
            resolved = row["item_id"] is not None and (
                row["confirmed"] or row["confidence"] >= REVIEW_THRESHOLD
            )
            if resolved and row["is_staple"]:
                continue
            item_id = row["item_id"] if resolved else None
            text = None if resolved else row["raw_text"]
            if not resolved:
                warning = f"Unresolved ingredient: {text}"
                if warning not in warnings:
                    warnings.append(warning)
            key = _key(item_id, text, row["unit_name"], row["unit_id"])
            _, factor = _unit(row["unit_name"], row["unit_id"])
            group = groups.setdefault(
                key, _Group(item_id, text, row["unit_id"], factor)
            )
            qty = row["qty_max"] if row["qty_max"] is not None else row["qty"]
            if qty is None:
                group.total = None
            elif group.total is not None:
                group.total += Fraction(str(qty)) * factor
            if factor > group.factor:
                group.factor, group.unit_id = factor, row["unit_id"]
            group.recipes.add(row["recipe_id"])
        list_id = shopping_db.active_list(conn, timestamp)
        old = {}
        for row in shopping_db.plan_items(conn, list_id):
            key = _key(
                row["item_id"], row["free_text"], row["unit_name"], row["unit_id"]
            )
            _, factor = _unit(row["unit_name"], row["unit_id"])
            old[key] = (row["qty"], row["checked"], factor)
        shopping_db.remove_plan_items(conn, list_id)
        for key, group in groups.items():
            previous = old.get(key)
            previous_qty = (
                (None if group.total is None else float(group.total / previous[2]))
                if previous
                else None
            )
            checked = (
                previous is not None
                and previous[1] == 1
                and previous[0] == previous_qty
            )
            qty = None if group.total is None else float(group.total / group.factor)
            shopping_db.add_plan_item(
                conn,
                list_id,
                group.item_id,
                group.text,
                qty,
                group.unit_id,
                checked,
                timestamp,
                group.recipes,
            )
    return warnings


def _plan_event(event: str, week_start: date) -> None:
    # Resolve the active app at call time: no captured app or database handle.
    if event == "plan_confirmed" and has_app_context():
        from larder.web import get_db

        regenerate(get_db(), today=today())


# Importing larder.web loads this module and registers the callback once.
# App instances share it, using their own connection and teardown lifecycle.
plan.subscribe(_plan_event)
