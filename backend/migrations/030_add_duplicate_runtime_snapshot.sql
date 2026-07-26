-- S2-4: persist the exact duplicate feature/threshold inputs used by a task.
ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS duplicate_algorithm_version VARCHAR(80);
ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS duplicate_feature_snapshot JSONB;

CREATE INDEX IF NOT EXISTS ix_review_tasks_duplicate_algorithm_version
    ON review_tasks(duplicate_algorithm_version);
