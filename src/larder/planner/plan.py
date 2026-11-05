# SPDX-License-Identifier: AGPL-3.0-only
"""Persist weekly planning sessions and publish committed plan confirmations."""

import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta

from larder import db, planner_db, recipes_db
from larder.planner.rank import (
    Candidate,
    History,
    Pick,
    Rules,
    alternatives,
    suggest_week,
)

_subscribers: list[Callable[[str, date], None]] = []


def subscribe(fn: Callable[[str, date], None]) -> None:
    """Register a process-local subscriber, called synchronously after commit."""
    _subscribers.append(fn)


def fire(event: str, week_start: date) -> None:
    """Notify registered subscribers; errors propagate after the saved commit."""
    for fn in tuple(_subscribers):
        fn(event, week_start)


def _inputs(conn: sqlite3.Connection, week_start: date):
    candidates = [
        Candidate(**dict(row)) for row in planner_db.rotation_candidates(conn)
    ]
    history = [
        History(row["recipe_id"], date.fromisoformat(row["date"]))
        for row in planner_db.meal_history(conn)
        if not week_start
        <= date.fromisoformat(row["date"])
        <= week_start + timedelta(days=6)
    ]
    return candidates, history


def _ensure_week(conn: sqlite3.Connection, week_start: date) -> sqlite3.Row:
    existing = planner_db.week_plan(conn, week_start.isoformat())
    if existing is not None:
        return existing
    candidates, history = _inputs(conn, week_start)
    picks = suggest_week(week_start, Rules(), history, candidates=candidates)
    plan_id = planner_db.create_week(conn, week_start.isoformat())
    for pick in picks:
        if pick.recipe_id is not None:
            planner_db.put_meal(conn, plan_id, pick.day.isoformat(), pick.recipe_id)
    return planner_db.week_plan(conn, week_start.isoformat())


def load_week(
    conn: sqlite3.Connection, week_start: date
) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    """Create suggestions once, then retain all saved choices on later visits."""
    with db.transaction(conn):
        week = _ensure_week(conn, week_start)
        meals = planner_db.week_meals(conn, week["id"])
    return week, meals


def save(
    conn: sqlite3.Connection,
    week_start: date,
    action: str,
    day: date,
    *,
    recipe_id: int | None = None,
    seen: Sequence[int] = (),
) -> int | None:
    """Save one action atomically, then notify consumers of confirmed plans.

    Returns the replacement recipe id for swaps. Manual picks can use any
    active recipe, independent of rotation and automatic ranking constraints.
    """
    if action not in {"confirm", "swap", "skip", "lock", "pick", "open"}:
        raise ValueError("unknown action")
    if not week_start <= day <= week_start + timedelta(days=6):
        raise ValueError("day outside week")
    replacement = None
    with db.transaction(conn):
        if action == "pick" and (
            recipe_id is None or recipes_db.get_recipe(conn, recipe_id) is None
        ):
            raise ValueError("unknown recipe")
        week = _ensure_week(conn, week_start)
        if action == "confirm":
            planner_db.confirm_week(conn, week["id"], datetime.now(UTC).isoformat())
        elif action == "pick":
            planner_db.put_meal(conn, week["id"], day.isoformat(), recipe_id)
        elif action == "swap":
            rows = {row["date"]: row for row in planner_db.week_meals(conn, week["id"])}
            picks = []
            for offset in range(7):
                slot = week_start + timedelta(days=offset)
                row = rows.get(slot.isoformat())
                picks.append(
                    Pick(
                        slot,
                        row["recipe_id"]
                        if row and row["status"] != "skipped"
                        else None,
                        bool(row["locked"]) if row else False,
                        row["effort"] if row else None,
                    )
                )
            candidates, history = _inputs(conn, week_start)
            shown = set(seen)
            target = rows.get(day.isoformat())
            if target:
                shown.add(target["recipe_id"])
            choices = alternatives(
                picks,
                day,
                1,
                candidates=[c for c in candidates if c.recipe_id not in shown],
                rules=Rules(),
                history=history,
            )
            if choices:
                replacement = choices[0].recipe_id
                planner_db.put_meal(conn, week["id"], day.isoformat(), replacement)
        else:
            planner_db.edit_meal(conn, week["id"], day.isoformat(), action)
        confirmed = action == "confirm" or week["confirmed_at"] is not None
    if confirmed:
        fire("plan_confirmed", week_start)
    return replacement
