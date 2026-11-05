-- SPDX-License-Identifier: AGPL-3.0-only
-- Preserve optional edits when the same ingredient line is indexed again.
ALTER TABLE parse_override ADD COLUMN optional INTEGER NOT NULL DEFAULT 0 CHECK (optional IN (0, 1));
