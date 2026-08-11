-- Migration 034: per-feature sales multipliers.
-- PostgreSQL 10 compatible (ADD COLUMN IF NOT EXISTS is supported on PG 9.6+).
--
-- The legacy single sales_multiplier stays as the global fallback.  The three
-- new columns are nullable: when NULL, the feature falls back to the global
-- sales_multiplier, preserving the previous behaviour for unconfigured
-- features and for snapshots that predate this migration (operate-two still
-- pushing the old single multiplier only).

ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS review_multiplier NUMERIC(10, 4);
ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS duplicate_multiplier NUMERIC(10, 4);
ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS blind_check_multiplier NUMERIC(10, 4);
