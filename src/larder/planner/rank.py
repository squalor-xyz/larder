# SPDX-License-Identifier: AGPL-3.0-only
"""Pure dinner ranking. Callers load candidates/history and persist picks.

Scores add capped age in days, a favorite bonus and an effort-fit bonus.
Eligibility always precedes scoring. A local seeded RNG only breaks ties.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from random import Random


@dataclass(frozen=True)
class Candidate:
    """One unarchived rotation dinner; null/unknown effort has no fit bonus."""

    recipe_id: int
    title: str
    favorite: bool = False
    effort: str | None = None


@dataclass(frozen=True)
class History:
    """One made or planned event, including future plans; skips are omitted."""

    recipe_id: int
    day: date


@dataclass(frozen=True)
class Rules:
    """Tuning in score points; configuration here is independent of TOML.

    Nights includes the open night which the household chooses later.
    Project limits (one per week, weekend only) are hard eligibility rules.
    """

    nights: int = 7
    min_repeat_days: int = 10
    recency_weight: float = 1
    favorite_weight: float = 7
    effort_weight: float = 1
    recency_cap: int = 365


@dataclass(frozen=True)
class Seed:
    """Reproducible local randomness; no process-global RNG is changed."""

    value: int = 0


_DEFAULT_SEED = Seed()


@dataclass(frozen=True)
class Pick:
    """A dated slot, empty when recipe_id is None.

    Supply effort for existing picks absent from the candidate pool (such as
    an archived locked project meal), so swaps can count those projects too.
    """

    day: date
    recipe_id: int | None
    locked: bool = False
    effort: str | None = None


def _age(
    candidate: Candidate, day: date, rules: Rules, history: Sequence[History]
) -> int:
    latest = max(
        (event.day for event in history if event.recipe_id == candidate.recipe_id),
        default=None,
    )
    return rules.recency_cap if latest is None else max(0, (day - latest).days)


def score(
    candidate: Candidate, day: date, rules: Rules, history: Sequence[History]
) -> float:
    """Score one candidate; callers must separately enforce eligibility.

    Never-made meals receive recency_cap days. Future plans have age zero.
    Quick meals fit weekdays; project meals fit Saturday/Sunday. Other effort
    values have zero fit bonus, including unspecified metadata.
    """
    age = min(rules.recency_cap, _age(candidate, day, rules, history))
    weekend = day.weekday() in (5, 6)
    effort_fit = (candidate.effort == "quick" and not weekend) or (
        candidate.effort == "project" and weekend
    )
    return (
        age * rules.recency_weight
        + candidate.favorite * rules.favorite_weight
        + effort_fit * rules.effort_weight
    )


def _rank(
    day: date,
    rules: Rules,
    history: Sequence[History],
    candidates: Sequence[Candidate],
    used: set[int],
    projects: int,
    seed: Seed,
) -> tuple[Candidate, ...]:
    rng = Random(f"{seed.value}:{day.isoformat()}")
    ranked = []
    # Canonical ordering makes both SQL and caller ordering irrelevant to ties.
    for candidate in sorted(candidates, key=lambda c: c.recipe_id):
        if candidate.recipe_id in used:
            continue
        if candidate.effort == "project" and (
            day.weekday() not in (5, 6) or projects >= 1
        ):
            continue
        recent = any(
            event.recipe_id == candidate.recipe_id
            and abs((day - event.day).days) < rules.min_repeat_days
            for event in history
        )
        points = score(candidate, day, rules, history)
        # Relaxed repeats are a second tier, ordered by uncapped recency first.
        key = (
            recent,
            -_age(candidate, day, rules, history) if recent else 0,
            -points,
            rng.random(),
        )
        ranked.append((key, candidate))
    ranked.sort(key=lambda pair: pair[0])
    return tuple(candidate for _, candidate in ranked)


def suggest_week(
    week_start: date,
    rules: Rules,
    history: Sequence[History],
    *,
    candidates: Sequence[Candidate],
    seed: Seed = _DEFAULT_SEED,
) -> tuple[Pick, ...]:
    """Fill Sunday onward, up to seven nights, without repeating a recipe.

    Strict repeat-gap candidates come first. Only when none fit a slot is the
    gap relaxed, choosing the least recent remaining meal. Project constraints
    never relax. Unfillable slots retain dates and a null recipe_id.
    """
    if week_start.weekday() != 6:
        raise ValueError("week_start must be a Sunday")
    if not 1 <= rules.nights <= 7:
        raise ValueError("nights must be between 1 and 7")
    picks = []
    used: set[int] = set()
    projects = 0
    for offset in range(rules.nights):
        day = week_start + timedelta(days=offset)
        ranked = _rank(day, rules, history, candidates, used, projects, seed)
        if not ranked:
            picks.append(Pick(day, None))
            continue
        chosen = ranked[0]
        picks.append(Pick(day, chosen.recipe_id, effort=chosen.effort))
        used.add(chosen.recipe_id)
        projects += chosen.effort == "project"
    return tuple(picks)


def alternatives(
    week: Sequence[Pick],
    night: date,
    k: int,
    *,
    candidates: Sequence[Candidate],
    rules: Rules,
    history: Sequence[History],
    seed: Seed = _DEFAULT_SEED,
) -> tuple[Candidate, ...]:
    """Return at most k swaps, preserving every occupied slot and locked pick.

    A night absent from week raises ValueError. A locked target yields no
    alternatives.
    The current recipe is excluded too. Strict candidates precede relaxed
    repeats. Effort on Pick lets callers count projects outside the rotation.
    """
    target = next((pick for pick in week if pick.day == night), None)
    if target is None:
        raise ValueError("night is not in week")
    if target.locked or k <= 0:
        return ()
    by_id = {candidate.recipe_id: candidate for candidate in candidates}
    used = {pick.recipe_id for pick in week if pick.recipe_id is not None}
    projects = sum(
        pick.recipe_id is not None
        and (
            pick.effort == "project"
            or (pick.recipe_id in by_id and by_id[pick.recipe_id].effort == "project")
        )
        for pick in week
        if pick.day != night
    )
    return _rank(night, rules, history, candidates, used, projects, seed)[:k]
