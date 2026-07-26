-- Add an explicit dark-mark scope to existing blind-check tasks.
-- Safe to run after 023 on PostgreSQL; new databases are covered by the
-- updated 023 definition and this statement remains idempotent.
ALTER TABLE IF EXISTS blind_check_tasks
    ADD COLUMN IF NOT EXISTS scope JSONB;
