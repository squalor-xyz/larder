-- SPDX-License-Identifier: AGPL-3.0-only
CREATE TABLE list_item_recipe (
    list_item_id INTEGER NOT NULL REFERENCES list_item (id) ON DELETE CASCADE,
    recipe_id INTEGER NOT NULL REFERENCES recipe (id) ON DELETE RESTRICT,
    PRIMARY KEY (list_item_id, recipe_id)
) STRICT;
CREATE INDEX idx_list_item_recipe_recipe_id ON list_item_recipe (recipe_id);
