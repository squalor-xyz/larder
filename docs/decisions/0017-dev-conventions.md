# 0017: Development tooling and conventions

**Status:** Accepted (2026-10-12)

## Context

Every implementation slice names test commands, file paths and dependency
rules. [ADR 0002](0002-python.md) sets Python and stdlib-first;
[ADR 0008](0008-flask.md) makes Flask the only runtime framework. The
deployment host is not chosen yet (S26), so the Python floor has to fit
common hosts and container base images. `tomllib` needs Python 3.11 or later.

## Decision

| Topic | Convention |
|---|---|
| Python | Minimum 3.12 (`requires-python = ">=3.12"`). Develop on any version ≥ 3.12. |
| Layout | `src/larder/` package; tests in `tests/` |
| Tests | stdlib `unittest`; test names are `tests/test_<area>.py::<Class>.test_<behavior>` |
| Lint and format | ruff (dev-only); config in `pyproject.toml`, `target-version = "py312"` |
| Types | Type hints required on public functions; no type checker |
| Build backend | setuptools, via `pyproject.toml` |
| Dependency pins | `requirements.in` (runtime) and `requirements-dev.in` (dev) compiled with hashes to `requirements.txt` and `requirements-dev.txt` |
| License headers | First line of every `.py` file: `# SPDX-License-Identifier: AGPL-3.0-only` |
| Commit messages | One imperative sentence ending with a period; name slice IDs in the text (e.g., "Add the recipe file parser (S10)."); no AI attribution |

### Commands

```sh
# Setup (fresh venv)
python3.12 -m venv .venv          # or any python ≥ 3.12
. .venv/bin/activate
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
pip install --no-deps -e .

# Test
python -m unittest discover -s tests -t .

# Lint and format
ruff check .
ruff format --check .             # `ruff format .` to apply

# Regenerate pins after editing a .in file
pip-compile --generate-hashes requirements.in
pip-compile --generate-hashes requirements-dev.in
```

`requirements-dev.in` includes `-c requirements.txt` so dev tools never
change runtime pins.

### Dev dependencies

Both are dev-only. Neither ships in the runtime image.

| Tool | License | Why not stdlib | Cost |
|---|---|---|---|
| ruff | MIT | The stdlib has no linter or formatter | One self-contained binary wheel; config in `pyproject.toml`; easy to drop |
| pip-tools | BSD-3-Clause | pip alone can't resolve and hash a full dependency tree in one step | Pure Python, pulls in a few build helpers; only used when pins change |

## Consequences

- No test or type-checker dependency; tests run with a bare Python.
- `pip install --require-hashes` refuses unpinned or tampered packages.
- `pip install -e .` fetches setuptools at build time without hash
  checking. That's acceptable for development only; the container image
  installs from the hashed requirements.
- Adding any runtime or dev dependency still needs approval (NF6) and a
  regenerated, hashed pin file.
