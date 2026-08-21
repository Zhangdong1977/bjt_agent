-- Store the check-item categories (rule doc filenames) the user selected when
-- starting a review. NULL = not specified (check everything, legacy clients).
-- Safe to run after 001 on PostgreSQL; new databases are covered by the model
-- definition and this statement remains idempotent.
ALTER TABLE IF EXISTS review_tasks
    ADD COLUMN IF NOT EXISTS selected_rule_docs JSONB;
