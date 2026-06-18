-- Migration: Create ai_usage_records table (DeepSeek token + Baidu OCR usage ledger)
-- Run this manually via psql for prod; dev/test 环境的全新库由 init_db() create_all 自动建表，
-- 已存在的库需手动跑本文件（index 也会补齐）。
CREATE TABLE IF NOT EXISTS ai_usage_records (
    id VARCHAR(36) PRIMARY KEY,
    external_user_id BIGINT,
    local_user_id   VARCHAR(36),
    user_name       VARCHAR(100) NOT NULL,
    enterprise_name VARCHAR(200),
    interior_user   BOOLEAN,
    project_id      VARCHAR(36),
    task_id         VARCHAR(36),
    todo_id         VARCHAR(36),

    usage_type VARCHAR(20) NOT NULL,
    provider   VARCHAR(50) NOT NULL,
    model      VARCHAR(100),
    status     VARCHAR(20) NOT NULL,

    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,

    ocr_calls           INTEGER NOT NULL DEFAULT 0,
    ocr_images          INTEGER NOT NULL DEFAULT 0,
    ocr_words_result_num INTEGER NOT NULL DEFAULT 0,
    image_size_bytes    BIGINT,

    latency_ms     INTEGER,
    endpoint       VARCHAR(255),
    error_code     VARCHAR(64),
    error_message  TEXT,
    raw_usage      JSONB,
    cost_cny       NUMERIC(12,6),

    usage_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_ai_usage_records_external_user_id ON ai_usage_records(external_user_id);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_local_user_id    ON ai_usage_records(local_user_id);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_user_name        ON ai_usage_records(user_name);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_enterprise_name  ON ai_usage_records(enterprise_name);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_project_id       ON ai_usage_records(project_id);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_task_id          ON ai_usage_records(task_id);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_todo_id          ON ai_usage_records(todo_id);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_provider         ON ai_usage_records(provider);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_status           ON ai_usage_records(status);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_usage_date       ON ai_usage_records(usage_date);
-- 同步游标主键：源端 id 为 UUID 非严格单调，故用 created_at + id 复合游标
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_created_id ON ai_usage_records(created_at, id);
