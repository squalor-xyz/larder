# AGENTS.md

Rules for people and coding agents working on larder.

## Project

larder is a self-hosted, FLOSS household food system: recipes, weekly dinner
planning and a shared shopping list. It runs on the household's own hardware.
It reads grocery order emails and recipe files.

**Status:** planning. The package skeleton exists; there are no features yet.

Read these before you propose features or change behavior:

- [Goals](docs/goals.md) and [requirements](docs/requirements.md)
- [Architecture](docs/specs/architecture.md) and [data model](docs/specs/data-model.md)
- [Roadmap](docs/roadmap.md) and [open questions](docs/open-questions.md)
- [Decisions (ADRs)](docs/decisions/). An accepted ADR sets the convention. Change it only with the owner.

## Commands

Python 3.12 or later. Run these from the repository root ([ADR 0017](docs/decisions/0017-dev-conventions.md)).

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
```

Regenerate the pins after you edit a `.in` file:

```sh
pip-compile --generate-hashes requirements.in
pip-compile --generate-hashes --allow-unsafe requirements-dev.in
```

`--allow-unsafe` is needed for the dev file. Without it, pip-compile leaves out
`pip` and `setuptools`, and `pip install --require-hashes` then fails.

## Conventions

- Code lives in `src/larder/`. Tests live in `tests/`.
- Every `.py` file starts with `# SPDX-License-Identifier: AGPL-3.0-only`.
- Tests use the stdlib `unittest`. Name them `tests/test_<area>.py::<Class>.test_<behavior>`.
- Type hints are required on public functions. There is no type checker.
- Lint and format with ruff, configured in `pyproject.toml` (`target-version = "py312"`).

### Data access

- SQL lives only in `src/larder/db.py`, `src/larder/migrations/` and per-area `src/larder/<area>_db.py` modules.
- An area module is plain functions that take a `sqlite3.Connection` first. No classes, no ORM.
- Writes go through `db.transaction()`. Connections are autocommit, so every transaction is explicit.
- A schema change is a new numbered migration `NNNN_name.sql`. Never edit an applied migration.

## Dependencies

- Stdlib first. Prefer in-repo code and the standard library ([ADR 0002](docs/decisions/0002-python.md)).
- Flask is the only approved runtime dependency ([ADR 0008](docs/decisions/0008-flask.md)).
- ruff and pip-tools are dev-only. They never ship in the runtime image ([ADR 0017](docs/decisions/0017-dev-conventions.md)).
- Ask the owner before you add any dependency, including dev and test ones. State why the stdlib or in-repo code is not enough, and the license. FLOSS only.
- A new dependency needs an edit to the `.in` file, a regenerated hashed pin file, and owner approval.

## Secrets and household data

- Never commit secrets: deploy keys, IMAP and ntfy credentials, API keys, private keys and `.env` files. Check `git status` before you commit.
- Read secrets from files or environment variables. Never log them or print them in full.
- In this repository, household-specific data (names, addresses, store accounts, real orders) goes only in the gitignored `local/` folder. At runtime, household data lives in the data directory and the database, never in the repo.
- Raw order emails are not committed. Only redacted fixtures under `tests/fixtures/` are.

## Commits

Commit only when the owner asks. Write one imperative sentence that ends with a period, and name slice IDs in the text, for example "Add the recipe file parser (S10)." Do not add AI attribution to commit messages or PR descriptions.

## Tracking with slicer

Work items are tracked in slicer. Read and change them through the CLI only:

- `slicer goals --json --lean` for product goals.
- `slicer next --ready --json --lean` for the next item to implement.
- `slicer show ID --json --lean` for one slice.
- `slicer check` to validate tracking before you hand off.

Do not open, search, parse or edit files under `.slicer/`. They hold tracking state and generated output.
