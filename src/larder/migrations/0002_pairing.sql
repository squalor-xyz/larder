-- SPDX-License-Identifier: AGPL-3.0-only
-- Pairing tokens (ADR 0010). Only the SHA-256 hash of a token is stored.
-- A bootstrap token has no person and no expiry; a pair token has both.

CREATE TABLE pairing_token (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('bootstrap', 'pair')),
    person_id INTEGER REFERENCES person (id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    used_at TEXT,
    CHECK (kind = 'bootstrap' OR (person_id IS NOT NULL AND expires_at IS NOT NULL))
) STRICT;
CREATE INDEX idx_pairing_token_person_id ON pairing_token (person_id);
