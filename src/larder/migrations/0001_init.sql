-- SPDX-License-Identifier: AGPL-3.0-only
-- MVP schema. See docs/specs/data-model.md.
-- Times are ISO-8601 UTC text, booleans are 0/1, every table is STRICT.

CREATE TABLE person (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
) STRICT;

CREATE TABLE device (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES person (id) ON DELETE RESTRICT,
    label TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    paired_at TEXT NOT NULL,
    last_seen TEXT,
    revoked_at TEXT
) STRICT;
CREATE INDEX idx_device_person_id ON device (person_id);

CREATE TABLE recipe_source (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    location TEXT NOT NULL,
    UNIQUE (kind, location)
) STRICT;

CREATE TABLE recipe (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES recipe_source (id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    body TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    UNIQUE (source_id, slug)
) STRICT;

CREATE TABLE recipe_meta (
    recipe_id INTEGER PRIMARY KEY REFERENCES recipe (id) ON DELETE CASCADE,
    meal_type TEXT,
    servings INTEGER CHECK (servings > 0),
    in_rotation INTEGER NOT NULL DEFAULT 1 CHECK (in_rotation IN (0, 1)),
    favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    effort TEXT
) STRICT;

CREATE TABLE unit (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    dimension TEXT NOT NULL CHECK (dimension IN ('mass', 'volume', 'count', 'package'))
) STRICT;

CREATE TABLE unit_alias (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES unit (id) ON DELETE CASCADE,
    alias TEXT NOT NULL UNIQUE
) STRICT;
CREATE INDEX idx_unit_alias_unit_id ON unit_alias (unit_id);

CREATE TABLE item (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    category TEXT
) STRICT;

CREATE TABLE item_alias (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES item (id) ON DELETE CASCADE,
    alias TEXT NOT NULL UNIQUE
) STRICT;
CREATE INDEX idx_item_alias_item_id ON item_alias (item_id);

CREATE TABLE ingredient_line (
    id INTEGER PRIMARY KEY,
    recipe_id INTEGER NOT NULL REFERENCES recipe (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    qty REAL,
    unit_id INTEGER REFERENCES unit (id) ON DELETE RESTRICT,
    item_id INTEGER REFERENCES item (id) ON DELETE RESTRICT,
    notes TEXT,
    optional INTEGER NOT NULL DEFAULT 0 CHECK (optional IN (0, 1)),
    is_food INTEGER NOT NULL DEFAULT 1 CHECK (is_food IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    UNIQUE (recipe_id, position)
) STRICT;
CREATE INDEX idx_ingredient_line_unit_id ON ingredient_line (unit_id);
CREATE INDEX idx_ingredient_line_item_id ON ingredient_line (item_id);

CREATE TABLE parse_override (
    id INTEGER PRIMARY KEY,
    normalized_text TEXT NOT NULL UNIQUE,
    qty REAL,
    unit_id INTEGER REFERENCES unit (id) ON DELETE RESTRICT,
    item_id INTEGER REFERENCES item (id) ON DELETE RESTRICT,
    notes TEXT,
    is_food INTEGER NOT NULL DEFAULT 1 CHECK (is_food IN (0, 1))
) STRICT;
CREATE INDEX idx_parse_override_unit_id ON parse_override (unit_id);
CREATE INDEX idx_parse_override_item_id ON parse_override (item_id);

CREATE TABLE meal_plan (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE planned_meal (
    id INTEGER PRIMARY KEY,
    meal_plan_id INTEGER NOT NULL REFERENCES meal_plan (id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    recipe_id INTEGER NOT NULL REFERENCES recipe (id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'made', 'skipped')),
    locked INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0, 1))
) STRICT;
CREATE INDEX idx_planned_meal_meal_plan_id ON planned_meal (meal_plan_id);
CREATE INDEX idx_planned_meal_recipe_id ON planned_meal (recipe_id);

CREATE TABLE shopping_list (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    archived_at TEXT
) STRICT;
-- At most one active (unarchived) list.
CREATE UNIQUE INDEX idx_shopping_list_one_active ON shopping_list ((1))
    WHERE archived_at IS NULL;

CREATE TABLE list_item (
    id INTEGER PRIMARY KEY,
    list_id INTEGER NOT NULL REFERENCES shopping_list (id) ON DELETE CASCADE,
    item_id INTEGER REFERENCES item (id) ON DELETE RESTRICT,
    free_text TEXT,
    qty REAL,
    unit_id INTEGER REFERENCES unit (id) ON DELETE RESTRICT,
    source TEXT NOT NULL CHECK (source IN ('plan', 'staple', 'manual', 'runout')),
    checked INTEGER NOT NULL DEFAULT 0 CHECK (checked IN (0, 1)),
    added_by INTEGER REFERENCES person (id) ON DELETE SET NULL,
    added_at TEXT NOT NULL,
    CHECK (item_id IS NOT NULL OR free_text IS NOT NULL)
) STRICT;
CREATE INDEX idx_list_item_list_id ON list_item (list_id);
CREATE INDEX idx_list_item_item_id ON list_item (item_id);
CREATE INDEX idx_list_item_unit_id ON list_item (unit_id);
CREATE INDEX idx_list_item_added_by ON list_item (added_by);

CREATE TABLE staple (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL UNIQUE REFERENCES item (id) ON DELETE CASCADE,
    default_qty REAL,
    unit_id INTEGER REFERENCES unit (id) ON DELETE RESTRICT
) STRICT;
CREATE INDEX idx_staple_unit_id ON staple (unit_id);
