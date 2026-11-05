# SPDX-License-Identifier: AGPL-3.0-only
"""Recipe git sync, serialized across requests and processes by recipes.lock.

Only command verbs are logged: URLs, paths, messages and git's diagnostic
output may contain secrets. Public failures are sanitized for the scheduler.
"""

import fcntl
import json
import logging
import os
import shlex
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from larder.config import Settings
from larder.recipes.index import index_recipes
from larder.scheduler import Scheduler

logger = logging.getLogger(__name__)


class RepoError(Exception):
    """A git operation failed; deliberately excludes git's raw diagnostics."""


@dataclass(frozen=True)
class RepoStatus:
    state: Literal["not_cloned", "ok", "needs_attention"] = "not_cloned"
    detail: str = "Recipe repository is not cloned."
    last_sync_at: str | None = None
    ahead: int = 0
    behind: int = 0
    conflicted_files: tuple[str, ...] = ()


@contextmanager
def _locked(settings: Settings) -> Iterator[None]:
    with (settings.data_dir / "recipes.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _git(settings: Settings, *args: str) -> str:
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = shlex.join(
        [
            "ssh",
            "-i",
            str(settings.secrets.deploy_key),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            f"UserKnownHostsFile={settings.secrets_dir / 'known_hosts'}",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
    )
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = settings.git_author_name
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = settings.git_author_email
    logger.debug("git %s", args[0])
    try:
        return subprocess.run(
            [
                "git",
                "-C",
                str(settings.data_dir),
                "-c",
                f"user.name={settings.git_author_name}",
                "-c",
                f"user.email={settings.git_author_email}",
                *(["-C", str(settings.recipes_dir)] if args[0] != "clone" else []),
                *args,
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        raise RepoError(f"Recipe git {args[0]} failed; needs attention.") from None


def _has_ref(settings: Settings, ref: str) -> bool:
    try:
        _git(settings, "rev-parse", "--verify", ref)
    except RepoError:
        return False
    return True


def _ensure_clone(settings: Settings) -> None:
    if (settings.recipes_dir / ".git").is_dir():
        return
    if not settings.recipe_repo_url:
        raise RepoError("Recipe repository URL is not configured.")
    _git(settings, "clone", "--", settings.recipe_repo_url, str(settings.recipes_dir))
    # An empty remote has no branch yet. Always write to main, including the
    # first commit, regardless of the host's or local git's default branch.
    if _has_ref(settings, "refs/remotes/origin/main"):
        _git(settings, "checkout", "main")
    elif not _has_ref(settings, "HEAD"):
        _git(settings, "symbolic-ref", "HEAD", "refs/heads/main")
    else:
        _require_main(settings)


def ensure_clone(settings: Settings) -> None:
    """Clone the configured repository if missing, leaving an existing clone alone."""
    with _locked(settings):
        _ensure_clone(settings)


def _status_path(settings: Settings) -> Path:
    return settings.recipes_dir / ".git" / "larder-status.json"


def _read_status(settings: Settings) -> RepoStatus:
    path = _status_path(settings)
    if not path.exists():
        return RepoStatus(state="ok", detail="Recipe repository is cloned.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["conflicted_files"] = tuple(raw["conflicted_files"])
    return RepoStatus(**raw)


def _write_status(settings: Settings, result: RepoStatus) -> None:
    path = _status_path(settings)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(result)), encoding="utf-8")
    temporary.replace(path)


def _counts(settings: Settings) -> tuple[int, int]:
    if not _has_ref(settings, "HEAD"):
        return 0, 0
    if not _has_ref(settings, "refs/remotes/origin/main"):
        return int(_git(settings, "rev-list", "--count", "HEAD")), 0
    ahead, behind = _git(
        settings, "rev-list", "--left-right", "--count", "HEAD...origin/main"
    ).split()
    return int(ahead), int(behind)


def _require_main(settings: Settings) -> None:
    try:
        branch = _git(settings, "symbolic-ref", "--short", "HEAD")
    except RepoError:
        branch = None
    if branch != "main":
        detail = "Recipe repository must be on main; needs attention."
        _write_status(
            settings,
            replace(_read_status(settings), state="needs_attention", detail=detail),
        )
        raise RepoError(detail)


def _pending_status(result: RepoStatus, ahead: int) -> RepoStatus:
    if ahead > 0 and result.state == "ok":
        return replace(
            result,
            state="needs_attention",
            detail=f"{ahead} unpushed recipe commit(s); needs attention.",
        )
    return result


def _pull(settings: Settings) -> bool:
    previous = RepoStatus()
    try:
        _ensure_clone(settings)
        previous = _read_status(settings)
        _require_main(settings)
        _git(settings, "fetch", "origin")
        if _has_ref(settings, "refs/remotes/origin/main"):
            if not _has_ref(settings, "HEAD"):
                _git(settings, "checkout", "-B", "main", "origin/main")
            else:
                try:
                    _git(settings, "rebase", "origin/main")
                except RepoError:
                    conflicted = tuple(
                        filter(
                            None,
                            _git(
                                settings, "diff", "--name-only", "--diff-filter=U", "-z"
                            ).split("\0"),
                        )
                    )
                    if conflicted:
                        _git(settings, "rebase", "--abort")
                        ahead, behind = _counts(settings)
                        _write_status(
                            settings,
                            replace(
                                previous,
                                state="needs_attention",
                                detail="Rebase conflict; needs attention.",
                                ahead=ahead,
                                behind=behind,
                                conflicted_files=conflicted,
                            ),
                        )
                        return False
                    raise
        ahead, behind = _counts(settings)
        _write_status(
            settings,
            _pending_status(
                RepoStatus(
                    state="ok",
                    detail="Recipe repository synced.",
                    last_sync_at=previous.last_sync_at
                    if ahead
                    else datetime.now(UTC).isoformat(),
                    ahead=ahead,
                    behind=behind,
                ),
                ahead,
            ),
        )
        _index(settings)
        return True
    except RepoError as error:
        if (settings.recipes_dir / ".git").is_dir():
            _write_status(
                settings,
                replace(previous, state="needs_attention", detail=str(error)),
            )
        raise


def _index(settings: Settings) -> None:
    try:
        index_recipes(settings)
    except Exception:  # noqa: BLE001 - indexing must never fail a successful sync
        # Parser and database diagnostics can include private recipe content.
        logger.error("Recipe indexing failed; will retry after the next sync.")


def pull(settings: Settings) -> None:
    """Clone or fetch and rebase onto origin/main; abort and persist conflicts."""
    with _locked(settings):
        _pull(settings)


def _push(settings: Settings) -> None:
    try:
        _git(settings, "push", "--set-upstream", "origin", "main")
    except RepoError:
        _write_status(
            settings,
            replace(
                _read_status(settings),
                state="needs_attention",
                detail="Recipe push failed; needs attention.",
            ),
        )
        raise
    _write_status(
        settings,
        RepoStatus(
            state="ok",
            detail="Recipe repository synced.",
            last_sync_at=datetime.now(UTC).isoformat(),
        ),
    )


def commit(settings: Settings, paths: Sequence[str | Path], message: str) -> None:
    """Commit the given relative paths with the configured identity, then sync.

    Callers write recipe files first. A conflicting rebase preserves the local
    commit and records needs_attention instead of pushing it.
    """
    with _locked(settings):
        if not paths:
            raise ValueError("At least one recipe path is required.")
        if not message.strip():
            raise ValueError("A nonempty recipe commit message is required.")
        _ensure_clone(settings)
        _require_main(settings)
        _git(settings, "add", "--", *(str(path) for path in paths))
        _git(settings, "commit", "-m", message, "--", *(str(path) for path in paths))
        _index(settings)
        if _pull(settings):
            _push(settings)


def push(settings: Settings) -> None:
    """Rebase and push main using only the configured deploy key for SSH."""
    with _locked(settings):
        if _pull(settings):
            _push(settings)


def status(settings: Settings) -> RepoStatus:
    """Return persisted sync diagnostics and current local ahead/behind counts."""
    with _locked(settings):
        if not (settings.recipes_dir / ".git").is_dir():
            return RepoStatus()
        ahead, behind = _counts(settings)
        return _pending_status(
            replace(_read_status(settings), ahead=ahead, behind=behind), ahead
        )


def _sync(settings: Settings) -> None:
    with _locked(settings):
        if _pull(settings) and _counts(settings)[0] > 0:
            _push(settings)


def register_sync(
    scheduler: Scheduler, settings: Settings, interval_seconds: float = 900
) -> None:
    """Schedule a pull and push of pending commits now and every 15 minutes."""
    scheduler.register(
        "recipe-sync", interval_seconds, lambda: _sync(settings), first_run_delay=0
    )
