-- Migration 048: 招标文件解析（tender-parse）模块（doc/招标文件解析需求.md §5）。
-- 解析记录表（独立轻量记录，不强绑 Project，需求 §8 Q10 默认）+ 记录-文档关联表
-- （P0 单文件，多文件 P2 放开，需求 §8 Q4 默认）。记录行兼作计费任务行
-- （列集照 033/040 任务级模式，reconcile_task_billing 统一引用）。
-- 幂等，可重复执行。PostgreSQL 10 compatible。

-- 解析记录：两段解析状态机 pending→running(parsing/extracting)→completed/failed
CREATE TABLE IF NOT EXISTS tender_parse_records (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_name VARCHAR(200) NOT NULL,                     -- 展示名：文件名 stem，提取后回填项目名称
    tender_unit VARCHAR(200),                               -- 招标方名称：提取完成后回填
    status VARCHAR(40) NOT NULL DEFAULT 'pending',          -- pending/running/completed/failed
    stage VARCHAR(40) NOT NULL DEFAULT 'queued',            -- queued/parsing/extracting
    failed_stage VARCHAR(40),                               -- parsing/extracting（重试续跑依据）
    error_message TEXT,
    duration_seconds INTEGER,                               -- 记录创建→终态总耗时
    result JSON,                                            -- AI 提取结果（六 Tab 行模型）
    manual_edit JSON,                                       -- 人工修正版（保存后有值）
    edited_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    billing_multiplier NUMERIC(10, 4),
    billing_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    billing_attempts INTEGER NOT NULL DEFAULT 0,
    billing_error TEXT,
    usage_finalized_at TIMESTAMPTZ,
    billing_settled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tender_parse_records_user ON tender_parse_records(user_id);
CREATE INDEX IF NOT EXISTS ix_tender_parse_records_status ON tender_parse_records(status);
CREATE INDEX IF NOT EXISTS ix_tender_parse_records_billing ON tender_parse_records(billing_status);
-- 列表默认序：创建时间倒序（同 projects/history 口径）
CREATE INDEX IF NOT EXISTS ix_tender_parse_records_user_created ON tender_parse_records(user_id, created_at DESC);

-- 记录-文档关联：文件本体走 documents 草稿口径（project_id IS NULL + owner_user_id，
-- doc_type='tender_parse' 与检查页 'tender' 草稿隔离、不占其配额）
CREATE TABLE IF NOT EXISTS tender_parse_documents (
    id VARCHAR(36) PRIMARY KEY,
    record_id VARCHAR(36) NOT NULL REFERENCES tender_parse_records(id) ON DELETE CASCADE,
    document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tender_parse_documents_record ON tender_parse_documents(record_id);
CREATE INDEX IF NOT EXISTS ix_tender_parse_documents_document ON tender_parse_documents(document_id);
