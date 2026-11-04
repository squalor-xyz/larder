-- SPDX-License-Identifier: AGPL-3.0-only
-- Quantity ranges ("2-3 cloves") and ingredient alternatives ("honey, maple
-- syrup, or agave") for parsed ingredient lines and parse overrides.
--
-- A range keeps its lower bound in qty and its upper bound in qty_max.
-- The first choice of an alternatives line stays on the parent row. The other
-- choices are child rows, in order, and share the parent's quantity and unit.

ALTER TABLE ingredient_line ADD COLUMN qty_max REAL
    CHECK (qty_max IS NULL OR (qty IS NOT NULL AND qty_max >= qty));

ALTER TABLE parse_override ADD COLUMN qty_max REAL
    CHECK (qty_max IS NULL OR (qty IS NOT NULL AND qty_max >= qty));

-- alt_text is the alternative as written. item_id stays null until it is
-- matched to a canonical item.
CREATE TABLE ingredient_line_alt (
    id INTEGER PRIMARY KEY,
    line_id INTEGER NOT NULL REFERENCES ingredient_line (id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 1),
    alt_text TEXT NOT NULL,
    item_id INTEGER REFERENCES item (id) ON DELETE RESTRICT,
    UNIQUE (line_id, position)
) STRICT;
CREATE INDEX idx_ingredient_line_alt_item_id ON ingredient_line_alt (item_id);

CREATE TABLE parse_override_alt (
    id INTEGER PRIMARY KEY,
    override_id INTEGER NOT NULL REFERENCES parse_override (id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 1),
    alt_text TEXT NOT NULL,
    item_id INTEGER REFERENCES item (id) ON DELETE RESTRICT,
    UNIQUE (override_id, position)
) STRICT;
CREATE INDEX idx_parse_override_alt_item_id ON parse_override_alt (item_id);
