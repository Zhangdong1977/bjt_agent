-- Migration 014: 创建 ai_usage_task_summary（任务级用量汇总表）
-- 背景：运营台 AI 用量看板原本同步 ai_usage_records（一行=一次 completion），列表爆炸。
--       改为在 bjt-agent 侧把每个 ReviewTask 的全部 LLM/OCR 调用聚合成一行，运营台只同步本表，
--       同步量从"每次 completion 一行"降到"每个任务一行"。
-- 一行 = 一个 ReviewTask（主键 id = ReviewTask.id = ai_usage_records.task_id）。
-- 数据来源：ai_usage_records GROUP BY task_id 聚合 + JOIN review_tasks 取状态/时长。
-- 全新库由 init_db() create_all 自动建表；已存在的库需手动跑本文件。
-- 执行方式：docker exec bjt-postgres psql -U bjt_user -d bjt_db -f backend/migrations/014_create_ai_usage_task_summary.sql
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS ai_usage_task_summary (
    id VARCHAR(36) PRIMARY KEY,                      -- = ReviewTask.id = ai_usage_records.task_id

    -- 任务状态维度（来自 review_tasks）
    task_status      VARCHAR(50),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    duration_seconds INTEGER,
    error_message    TEXT,

    -- 归属维度（聚合自 ai_usage_records）
    external_user_id BIGINT,
    local_user_id    VARCHAR(36),
    user_name        VARCHAR(100),
    enterprise_name  VARCHAR(200),
    interior_user    BOOLEAN,
    project_id       VARCHAR(36),

    -- 调用次数
    llm_calls    INTEGER NOT NULL DEFAULT 0,
    ocr_calls    INTEGER NOT NULL DEFAULT 0,
    failed_calls INTEGER NOT NULL DEFAULT 0,

    -- LLM token（含 DeepSeek 缓存拆分）
    prompt_tokens          INTEGER NOT NULL DEFAULT 0,
    completion_tokens      INTEGER NOT NULL DEFAULT 0,
    total_tokens           INTEGER NOT NULL DEFAULT 0,
    prompt_cache_hit_tokens   INTEGER NOT NULL DEFAULT 0,
    prompt_cache_miss_tokens  INTEGER NOT NULL DEFAULT 0,

    -- OCR 指标
    ocr_images          INTEGER NOT NULL DEFAULT 0,
    ocr_words_result_num INTEGER NOT NULL DEFAULT 0,

    -- 费用（cost_cny 在流水写入时已按 cache 三档计好，此处直接 SUM）
    cost_cny NUMERIC(12,6),

    -- 任务用量首末时间
    first_usage_at TIMESTAMPTZ,
    last_usage_at  TIMESTAMPTZ,

    -- Base 继承字段（onupdate 自动刷新，作增量同步游标）
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_ai_usage_task_summary_task_status     ON ai_usage_task_summary(task_status);
CREATE INDEX IF NOT EXISTS ix_ai_usage_task_summary_external_user_id ON ai_usage_task_summary(external_user_id);
CREATE INDEX IF NOT EXISTS ix_ai_usage_task_summary_user_name       ON ai_usage_task_summary(user_name);
CREATE INDEX IF NOT EXISTS ix_ai_usage_task_summary_enterprise_name ON ai_usage_task_summary(enterprise_name);
CREATE INDEX IF NOT EXISTS ix_ai_usage_task_summary_project_id      ON ai_usage_task_summary(project_id);
-- 增量同步游标：updated_at + id 复合（updated_at 非严格单调，用 id 兜底排序）
CREATE INDEX IF NOT EXISTS ix_ai_usage_task_summary_updated_id ON ai_usage_task_summary(updated_at, id);
