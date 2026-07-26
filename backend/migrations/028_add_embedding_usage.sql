-- S2-2B: embedding/VLM usage audit fields.

ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS embedding_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS embedding_inputs INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS embedding_input_chars INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS embedding_input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS embedding_cache_hits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS vision_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS vision_images INTEGER NOT NULL DEFAULT 0;

ALTER TABLE ai_usage_task_summary
    ADD COLUMN IF NOT EXISTS embedding_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_task_summary
    ADD COLUMN IF NOT EXISTS vision_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_task_summary
    ADD COLUMN IF NOT EXISTS embedding_inputs INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_task_summary
    ADD COLUMN IF NOT EXISTS embedding_input_chars INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_task_summary
    ADD COLUMN IF NOT EXISTS embedding_input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_task_summary
    ADD COLUMN IF NOT EXISTS embedding_cache_hits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_task_summary
    ADD COLUMN IF NOT EXISTS vision_images INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_ai_usage_records_usage_type
    ON ai_usage_records(usage_type);
