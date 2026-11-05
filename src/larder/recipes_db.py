# SPDX-License-Identifier: AGPL-3.0-only
"""SQL for the recipe repository mirror. Writes use the caller's transaction."""

import sqlite3

from larder.recipes.doc import RecipeDoc


def recipe_source(conn: sqlite3.Connection) -> int:
    """Return the stable source for the household recipe repository."""
    conn.execute(
        "INSERT INTO recipe_source (kind, location) VALUES ('recipe-repo', 'recipes') "
        "ON CONFLICT (kind, location) DO NOTHING"
    )
    return conn.execute(
        "SELECT id FROM recipe_source WHERE kind='recipe-repo' AND location='recipes'"
    ).fetchone()[0]


def mirror_recipe(
    conn: sqlite3.Connection, source_id: int, doc: RecipeDoc, content_hash: str
) -> int:
    """Insert or refresh file content without replacing metadata or overrides."""
    existing = conn.execute(
        "SELECT id, content_hash, archived FROM recipe WHERE source_id=? AND slug=?",
        (source_id, doc.slug),
    ).fetchone()
    if existing is not None and existing["content_hash"] == content_hash:
        if existing["archived"]:
            conn.execute("UPDATE recipe SET archived=0 WHERE id=?", (existing["id"],))
        return existing["id"]
    values = (
        doc.front_matter["title"],
        f"recipes/{doc.slug}.md",
        content_hash,
        doc.body,
    )
    if existing is None:
        cursor = conn.execute(
            "INSERT INTO recipe (source_id, slug, title, source_ref, content_hash, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, doc.slug, *values),
        )
        recipe_id = cursor.lastrowid
        conn.execute("INSERT INTO recipe_meta (recipe_id) VALUES (?)", (recipe_id,))
    else:
        recipe_id = existing["id"]
        conn.execute(
            "UPDATE recipe SET title=?, source_ref=?, content_hash=?, body=?, archived=0 "
            "WHERE id=?",
            (*values, recipe_id),
        )
        conn.execute("DELETE FROM ingredient_line WHERE recipe_id=?", (recipe_id,))
    conn.executemany(
        "INSERT INTO ingredient_line (recipe_id, position, raw_text, confidence) "
        "VALUES (?, ?, ?, 0)",
        (
            (recipe_id, position, line.text)
            for position, line in enumerate(doc.ingredients, 1)
        ),
    )
    return recipe_id


def archive_missing(conn: sqlite3.Connection, source_id: int, slugs: set[str]) -> None:
    """Archive missing slugs only in this source, retaining their identities."""
    rows = conn.execute(
        "SELECT id, slug FROM recipe WHERE source_id=? AND archived=0", (source_id,)
    ).fetchall()
    conn.executemany(
        "UPDATE recipe SET archived=1 WHERE id=?",
        ((row["id"],) for row in rows if row["slug"] not in slugs),
    )
