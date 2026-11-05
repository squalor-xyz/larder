-- SPDX-License-Identifier: AGPL-3.0-only
-- Category order is seeded by name; item.category remains editable text.
CREATE TABLE category (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL
) STRICT;
