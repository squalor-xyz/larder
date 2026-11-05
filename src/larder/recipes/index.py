# SPDX-License-Identifier: AGPL-3.0-only
"""Mirror recipe files; callers serialize repository access with recipes.lock."""

import hashlib
import logging
from contextlib import closing

from larder import db, recipes_db
from larder.config import Settings
from larder.recipes.doc import RecipeParseError, parse_recipe

logger = logging.getLogger(__name__)


def index_recipes(settings: Settings) -> None:
    """Index recipes/<slug>.md atomically, keeping metadata and parse overrides.

    Skip unreadable or invalid files, retaining their existing rows. Drafts
    are excluded and any existing rows for them are archived. All database
    writes share one transaction. The app migrates the database at startup;
    this function opens and closes its own connection. Ingredient lines remain
    raw and unconfirmed.
    """
    documents = []
    skipped: set[str] = set()
    for path in sorted((settings.recipes_dir / "recipes").glob("*.md")):
        try:
            content = path.read_bytes()
            document = parse_recipe(content.decode("utf-8"), path.stem)
            if "title" not in document.front_matter:
                raise RecipeParseError("Missing recipe title.")
        except (OSError, UnicodeDecodeError, RecipeParseError):
            logger.error(
                "Skipping recipe %r: unreadable or invalid recipe file.", path.stem
            )
            skipped.add(path.stem)
            continue
        if document.is_draft:
            continue
        documents.append((document, hashlib.sha256(content).hexdigest()))
    with closing(db.connect(settings)) as conn, db.transaction(conn):
        source_id = recipes_db.recipe_source(conn)
        for document, content_hash in documents:
            recipes_db.mirror_recipe(conn, source_id, document, content_hash)
        recipes_db.archive_missing(
            conn, source_id, {doc.slug for doc, _ in documents} | skipped
        )
