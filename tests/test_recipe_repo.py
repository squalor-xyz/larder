# SPDX-License-Identifier: AGPL-3.0-only
"""Exercise recipe sync against disposable local git repositories."""

import dataclasses
import fcntl
import importlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from larder.config import Person, load_settings
from larder.scheduler import Scheduler
from larder.web import create_app


class RecipeRepoTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data with spaces"
        self.data.mkdir()
        self.remote = self.root / "remote.git"
        self.writer = self.root / "writer"
        self.env = dict(os.environ, HOME=str(self.root), XDG_CONFIG_HOME=str(self.root))
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Fixture",
            GIT_AUTHOR_EMAIL="fixture@example.test",
            GIT_COMMITTER_NAME="Fixture",
            GIT_COMMITTER_EMAIL="fixture@example.test",
        )
        environment = mock.patch.dict(os.environ, self.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git(self.root, "clone", str(self.remote), str(self.writer))
        self.remote_write("soup.md", "Original soup\n")
        self.settings = load_settings(
            {"LARDER_DATA": str(self.data), "LARDER_RECIPE_REPO_URL": str(self.remote)}
        )
        self.repo = importlib.import_module("larder.recipes.repo")

    def git(self, path: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def remote_write(self, path: str, text: str) -> None:
        (self.writer / path).write_text(text)
        self.git(self.writer, "add", "--", path)
        self.git(self.writer, "commit", "-m", "Update fixture.")
        self.git(self.writer, "push", "origin", "main")

    def diverge(self, conflict: bool = False) -> None:
        self.repo.ensure_clone(self.settings)
        (self.settings.recipes_dir / "soup.md").write_text("Local soup\n")
        self.git(self.settings.recipes_dir, "add", "soup.md")
        self.git(self.settings.recipes_dir, "commit", "-m", "Update local soup.")
        self.remote_write("soup.md" if conflict else "salad.md", "Remote recipe\n")

    def test_clone_when_missing_then_pull_when_present(self) -> None:
        self.repo.ensure_clone(self.settings)
        self.assertEqual(
            (self.settings.recipes_dir / "soup.md").read_text(), "Original soup\n"
        )
        self.remote_write("soup.md", "New soup\n")
        self.repo.pull(self.settings)
        self.assertEqual(
            (self.settings.recipes_dir / "soup.md").read_text(), "New soup\n"
        )
        empty = self.root / "empty.git"
        self.git(self.root, "init", "--bare", "--initial-branch=unused", str(empty))
        data = self.root / "empty-data"
        data.mkdir()
        settings = dataclasses.replace(
            self.settings, data_dir=data, recipe_repo_url=str(empty)
        )
        self.repo.pull(settings)
        (settings.recipes_dir / "first.md").write_text("First recipe\n")
        self.repo.commit(settings, ["first.md"], "Add first.")
        self.assertEqual(
            (
                self.git(empty, "show", "main:first.md"),
                self.git(empty, "log", "main", "-1", "--format=%an <%ae>"),
            ),
            ("First recipe", "larder <larder@localhost>"),
        )

    def test_commit_and_push_writes_file_with_configured_identity(self) -> None:
        (self.data / "larder.toml").write_text(
            '[git]\nauthor_name = "Recipe Bot"\nauthor_email = "recipes@example.test"\n'
        )
        settings = load_settings(
            {"LARDER_DATA": str(self.data), "LARDER_RECIPE_REPO_URL": str(self.remote)}
        )
        self.repo.ensure_clone(settings)
        (settings.recipes_dir / "stew.md").write_text("Stew\n")
        self.repo.commit(settings, ["stew.md"], "Add stew.")
        self.assertEqual(
            self.git(self.remote, "show", "main:stew.md"), "Stew"
        )  # commit must sync after every write, without a separate push call
        self.repo.push(settings)
        self.assertEqual(
            self.git(self.remote, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%s"),
            "Recipe Bot <recipes@example.test>|Recipe Bot <recipes@example.test>|Add stew.",
        )

    def test_push_uses_deploy_key_via_GIT_SSH_COMMAND_only(self) -> None:
        self.repo.ensure_clone(self.settings)
        run = subprocess.run
        calls = []

        def capture(args, **kwargs):
            calls.append((args, kwargs))
            return run(args, **kwargs)

        with mock.patch("larder.recipes.repo.subprocess.run", side_effect=capture):
            self.repo.push(self.settings)
        push = next((args, kw) for args, kw in calls if "push" in args)
        args, kw = push
        self.assertEqual(
            (
                shlex.split(kw["env"]["GIT_SSH_COMMAND"]),
                kw.get("shell", False),
                kw["check"],
                str(self.settings.secrets.deploy_key) in " ".join(args),
            ),
            (
                [
                    "ssh",
                    "-i",
                    str(self.settings.secrets.deploy_key),
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"UserKnownHostsFile={self.settings.secrets_dir / 'known_hosts'}",
                    "-o",
                    "StrictHostKeyChecking=yes",
                ],
                False,
                True,
                False,
            ),
        )

    def test_diverged_remote_rebases_cleanly(self) -> None:
        self.diverge()
        self.repo.pull(self.settings)
        self.assertEqual(
            (
                (self.settings.recipes_dir / "soup.md").read_text(),
                (self.settings.recipes_dir / "salad.md").read_text(),
                self.git(
                    self.settings.recipes_dir,
                    "rev-list",
                    "--left-right",
                    "--count",
                    "HEAD...origin/main",
                ),
            ),
            ("Local soup\n", "Remote recipe\n", "1\t0"),
        )

    def test_conflict_surfaces_status_and_leaves_repo_clean(self) -> None:
        self.diverge(conflict=True)
        head = self.git(self.settings.recipes_dir, "rev-parse", "HEAD")
        self.repo.pull(self.settings)
        status = self.repo.status(self.settings)
        self.assertEqual(
            (status.state, status.conflicted_files, status.ahead, status.behind),
            ("needs_attention", ("soup.md",), 1, 1),
        )
        self.assertEqual(
            (
                self.git(self.settings.recipes_dir, "status", "--porcelain"),
                self.git(self.settings.recipes_dir, "rev-parse", "HEAD"),
                (self.settings.recipes_dir / ".git" / "rebase-merge").exists(),
            ),
            ("", head, False),
        )

    def test_secrets_never_logged(self) -> None:
        secret = "PRIVATE-CONTENT-93a7"
        self.settings.secrets_dir.mkdir()
        self.settings.secrets.deploy_key.write_text(secret)
        with self.assertLogs("larder.recipes.repo", "DEBUG") as logs:
            self.repo.ensure_clone(self.settings)
            with (
                mock.patch(
                    "larder.recipes.repo.subprocess.run",
                    side_effect=subprocess.CalledProcessError(
                        1,
                        ["git", secret],
                        stderr=f"{secret} {self.settings.secrets.deploy_key}",
                    ),
                ),
                self.assertRaises(self.repo.RepoError) as caught,
            ):
                self.repo.push(self.settings)
        output = "\n".join(logs.output) + str(caught.exception)
        self.assertEqual(
            [
                marker
                for marker in (
                    secret,
                    str(self.settings.secrets.deploy_key),
                    str(self.settings.secrets_dir / "known_hosts"),
                )
                if marker in output
            ],
            [],
        )

    def test_status_before_first_clone_and_when_in_sync(self) -> None:
        status = self.repo.status(self.settings)
        self.assertEqual(
            dataclasses.astuple(status),
            ("not_cloned", "Recipe repository is not cloned.", None, 0, 0, ()),
        )
        self.repo.pull(self.settings)
        status = self.repo.status(self.settings)
        self.assertEqual(
            (status.state, status.ahead, status.behind, status.conflicted_files),
            ("ok", 0, 0, ()),
        )
        self.assertIsNotNone(status.last_sync_at)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            status.state = "needs_attention"

    def test_conflict_status_is_readable_from_a_new_process(self) -> None:
        self.diverge(conflict=True)
        self.repo.pull(self.settings)
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import dataclasses,json; from larder.config import load_settings; from larder.recipes.repo import status; print(json.dumps(dataclasses.asdict(status(load_settings()))))",
            ],
            env=dict(self.env, LARDER_DATA=str(self.data)),
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(child.stdout)
        self.assertEqual(
            (
                result["state"],
                result["conflicted_files"],
                result["ahead"],
                result["behind"],
            ),
            ("needs_attention", ["soup.md"], 1, 1),
        )

    def test_sync_job_is_registered_only_when_the_repo_url_is_set(self) -> None:
        now = [0.0]
        scheduler = Scheduler(self.data, clock=lambda: now[0])
        settings = dataclasses.replace(
            self.settings, people=(Person("Admin", role="admin"),)
        )
        with mock.patch("larder.web.Scheduler", return_value=scheduler):
            create_app(settings, bootstrap=False)
        scheduler.run_due()
        self.assertEqual(
            (settings.recipes_dir / "soup.md").read_text(), "Original soup\n"
        )
        self.remote_write("soup.md", "Scheduled soup\n")
        now[0] = 899.0
        scheduler.run_due()
        self.assertEqual(
            (settings.recipes_dir / "soup.md").read_text(), "Original soup\n"
        )
        now[0] = 900.0
        scheduler.run_due()
        self.assertEqual(
            (settings.recipes_dir / "soup.md").read_text(), "Scheduled soup\n"
        )
        # Change only the configured URL; the scheduler must leave an existing clone alone.
        scheduler = Scheduler(self.data, clock=lambda: now[0])
        self.remote_write("soup.md", "Disabled soup\n")
        with mock.patch("larder.web.Scheduler", return_value=scheduler):
            create_app(
                dataclasses.replace(settings, recipe_repo_url=None), bootstrap=False
            )
        scheduler.run_due()
        now[0] += 900
        scheduler.run_due()
        self.assertEqual(
            (settings.recipes_dir / "soup.md").read_text(), "Scheduled soup\n"
        )

    def test_git_calls_are_serialized_by_the_lock_file(self) -> None:
        started = threading.Event()
        errors = []

        def clone():
            started.set()
            try:
                self.repo.ensure_clone(self.settings)
            except (self.repo.RepoError, OSError) as exc:
                errors.append(exc)

        with (self.data / "recipes.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            thread = threading.Thread(target=clone)
            thread.start()
            try:
                started.wait(5)
                thread.join(0.2)
                self.assertFalse((self.settings.recipes_dir / ".git").exists())
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                thread.join(10)
        self.assertEqual(
            (errors, (self.settings.recipes_dir / "soup.md").read_text()),
            ([], "Original soup\n"),
        )
        # Probe the real flock during every git subprocess across the public API.
        run = subprocess.run

        def probe(args, **kwargs):
            with (
                (self.data / "recipes.lock").open("a") as other,
                self.assertRaises(BlockingIOError),
            ):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return run(args, **kwargs)

        with mock.patch("larder.recipes.repo.subprocess.run", side_effect=probe):
            self.repo.pull(self.settings)
            (self.settings.recipes_dir / "new.md").write_text("New recipe\n")
            self.repo.commit(self.settings, ["new.md"], "Add new.")
            self.repo.push(self.settings)
            self.repo.status(self.settings)

    def test_scheduled_sync_retries_unpushed_commits(self) -> None:
        self.repo.pull(self.settings)
        (self.settings.recipes_dir / "stew.md").write_text("Retry stew\n")
        run = subprocess.run
        advance_remote = True

        def race_push(args, **kwargs):
            nonlocal advance_remote
            if "push" in args and advance_remote:
                advance_remote = False
                self.remote_write("salad.md", "Concurrent salad\n")
            return run(args, **kwargs)

        with (
            mock.patch("larder.recipes.repo.subprocess.run", side_effect=race_push),
            self.assertRaises(self.repo.RepoError),
        ):
            self.repo.commit(self.settings, ["stew.md"], "Add stew.")
        pending = self.repo.status(self.settings)
        self.assertEqual(
            (pending.state, pending.detail, pending.ahead),
            ("needs_attention", "Recipe push failed; needs attention.", 1),
        )
        now = [0.0]
        scheduler = Scheduler(self.data, clock=lambda: now[0])
        settings = dataclasses.replace(
            self.settings, people=(Person("Admin", role="admin"),)
        )
        with mock.patch("larder.web.Scheduler", return_value=scheduler):
            create_app(settings, bootstrap=False)
        scheduler.run_due()
        result = self.repo.status(settings)
        self.assertEqual(
            (self.git(self.remote, "show", "main:stew.md"), result.state, result.ahead),
            ("Retry stew", "ok", 0),
        )

    def test_pull_reports_unpushed_commits(self) -> None:
        self.repo.pull(self.settings)
        (self.settings.recipes_dir / "soup.md").write_text("Local soup\n")
        self.git(self.settings.recipes_dir, "add", "soup.md")
        self.git(self.settings.recipes_dir, "commit", "-m", "Update soup.")
        for phase in ("before pull", "after pull"):
            if phase == "after pull":
                self.repo.pull(self.settings)
            with self.subTest(phase=phase):
                pending = self.repo.status(self.settings)
                self.assertEqual(
                    (pending.state, pending.detail, pending.ahead),
                    (
                        "needs_attention",
                        "1 unpushed recipe commit(s); needs attention.",
                        1,
                    ),
                )
        persisted = json.loads(
            (self.settings.recipes_dir / ".git" / "larder-status.json").read_text()
        )
        self.assertEqual(
            (persisted["state"], persisted["detail"], persisted["ahead"]),
            ("needs_attention", "1 unpushed recipe commit(s); needs attention.", 1),
        )

    def test_missing_main_stays_needs_attention(self) -> None:
        self.git(self.writer, "branch", "-m", "master")
        self.git(self.writer, "push", "origin", "master")
        self.git(self.remote, "symbolic-ref", "HEAD", "refs/heads/master")
        self.git(self.writer, "push", "origin", "--delete", "main")
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                with self.assertRaises(self.repo.RepoError):
                    self.repo.pull(self.settings)
                result = self.repo.status(self.settings)
                self.assertEqual(
                    (
                        result.state,
                        "main" in result.detail,
                        self.git(
                            self.settings.recipes_dir, "symbolic-ref", "--short", "HEAD"
                        ),
                    ),
                    ("needs_attention", True, "master"),
                )

    def test_commit_rejects_non_main_branch(self) -> None:
        self.repo.pull(self.settings)
        self.git(self.settings.recipes_dir, "checkout", "-b", "other")
        (self.settings.recipes_dir / "soup.md").write_text("Other soup\n")
        with self.assertRaises(self.repo.RepoError):
            self.repo.commit(self.settings, ["soup.md"], "Update soup.")
        result = self.repo.status(self.settings)
        self.assertEqual(
            (
                result.state,
                "main" in result.detail,
                self.git(self.settings.recipes_dir, "diff", "--cached", "--name-only"),
                self.git(self.settings.recipes_dir, "show", "HEAD:soup.md"),
            ),
            ("needs_attention", True, "", "Original soup"),
        )

    def test_empty_commit_message_does_not_stage_paths(self) -> None:
        self.repo.pull(self.settings)
        (self.settings.recipes_dir / "soup.md").write_text("Uncommitted soup\n")
        for message in ("", " \n\t"):
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    self.repo.commit(self.settings, ["soup.md"], message)
                self.assertEqual(
                    (
                        self.git(
                            self.settings.recipes_dir, "diff", "--cached", "--name-only"
                        ),
                        (self.settings.recipes_dir / "soup.md").read_text(),
                    ),
                    ("", "Uncommitted soup\n"),
                )

    def test_git_failures_persist_needs_attention(self) -> None:
        self.repo.pull(self.settings)
        run = subprocess.run
        for operation in ("fetch", "rebase", "push"):
            with self.subTest(operation=operation):
                # Each injected failure starts after a successful sync.
                self.repo.pull(self.settings)

                def fail_operation(args, selected_operation=operation, **kwargs):
                    if selected_operation in args:
                        raise subprocess.CalledProcessError(1, args)
                    return run(args, **kwargs)

                with (
                    mock.patch(
                        "larder.recipes.repo.subprocess.run", side_effect=fail_operation
                    ),
                    self.assertRaises(self.repo.RepoError),
                ):
                    self.repo.push(self.settings)
                result = self.repo.status(self.settings)
                expected = (
                    "Recipe push failed; needs attention."
                    if operation == "push"
                    else f"Recipe git {operation} failed; needs attention."
                )
                self.assertEqual(
                    (result.state, result.detail), ("needs_attention", expected)
                )
