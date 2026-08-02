-- Migration 025: strengthen duplicate-result provenance and uncertainty handling.
--
-- Existing installations already have duplicate_results from migration 022.
-- New databases are covered by SQLAlchemy metadata; this file is for deployed
-- databases and is intentionally idempotent.

ALTER TABLE IF EXISTS duplicate_results
    ADD COLUMN IF NOT EXISTS source_basis VARCHAR(30);

UPDATE duplicate_results
   SET source_basis = 'unknown'
 WHERE source_basis IS NULL;

ALTER TABLE IF EXISTS duplicate_results
    ALTER COLUMN source_basis SET DEFAULT 'unknown';

ALTER TABLE IF EXISTS duplicate_results
    ALTER COLUMN source_basis SET NOT NULL;

-- 022 only allowed reasonable/suspicious. Replace that constraint so older
-- rows remain valid while the agent can explicitly report insufficient evidence.
ALTER TABLE IF EXISTS duplicate_results
    DROP CONSTRAINT IF EXISTS ck_duplicate_results_verdict;
ALTER TABLE IF EXISTS duplicate_results
    ADD CONSTRAINT ck_duplicate_results_verdict
    CHECK (verdict IN ('reasonable', 'suspicious', 'unknown'));

ALTER TABLE IF EXISTS duplicate_results
    DROP CONSTRAINT IF EXISTS ck_duplicate_results_source_basis;
ALTER TABLE IF EXISTS duplicate_results
    ADD CONSTRAINT ck_duplicate_results_source_basis
    CHECK (source_basis IN ('tender', 'public', 'bidder_authored', 'unknown'));

CREATE INDEX IF NOT EXISTS ix_duplicate_results_source_basis
    ON duplicate_results(source_basis);
