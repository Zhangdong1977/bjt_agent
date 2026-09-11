-- Migration 040: AI编标（bid-wizard）模块骨架（doc/workspace/20-bid-wizard.md §5.1）。
-- 七张表 + projects.project_type 第四值 'bid_wizard'（038 教训：扩枚举必同步放宽 CHECK 约束）
-- + sales_configs 三个向导倍率列（NULL 回退全局 sales_multiplier）。
-- 幂等，可重复执行。PostgreSQL 10 compatible。

ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_project_type;

DO $$ BEGIN
    ALTER TABLE projects ADD CONSTRAINT ck_projects_project_type
        CHECK (project_type IN ('review', 'duplicate', 'bid_draft', 'bid_wizard'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 向导主表：阶段状态 + 三个 JSON 产物（问卷/编写需求/Spec）
CREATE TABLE IF NOT EXISTS bid_wizards (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage VARCHAR(40) NOT NULL DEFAULT 'material',          -- material/requirement/outline/writing
    status VARCHAR(40) NOT NULL DEFAULT 'active',           -- active/completed/archived
    analysis JSON,                                           -- 招标要素（复用 V1 tender_analysis 语义）
    questionnaire JSON,                                      -- 问卷 + 作答
    requirements JSON,                                       -- 编写需求
    spec JSON,                                               -- 编写大纲（目录树+每章摘要+图表规划）
    spec_previous JSON,                                      -- AI 修订前的一版（一步回退）
    spec_confirmed_at TIMESTAMPTZ,
    requirements_stale BOOLEAN NOT NULL DEFAULT FALSE,       -- 素材/招标文件变更后，需求产物过期
    spec_stale BOOLEAN NOT NULL DEFAULT FALSE,               -- 需求/素材变更后，Spec 过期
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bid_wizards_user_id ON bid_wizards(user_id);
CREATE INDEX IF NOT EXISTS ix_bid_wizards_project_id ON bid_wizards(project_id);
-- 一 project 同时只有一个活动向导（决策 9）
CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_wizards_active_per_project
    ON bid_wizards(project_id) WHERE status = 'active';

-- 素材池：向导 ↔ 文档 关联 + 索引状态（文件本体/解析复用 documents 管道）
CREATE TABLE IF NOT EXISTS bid_wizard_materials (
    id VARCHAR(36) PRIMARY KEY,
    wizard_id VARCHAR(36) NOT NULL REFERENCES bid_wizards(id) ON DELETE CASCADE,
    document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category VARCHAR(100),
    index_status VARCHAR(40) NOT NULL DEFAULT 'pending',     -- pending/indexing/indexed/failed
    indexed_at TIMESTAMPTZ,
    index_error TEXT,
    chunk_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bid_wizard_materials_wizard ON bid_wizard_materials(wizard_id);
CREATE INDEX IF NOT EXISTS ix_bid_wizard_materials_document ON bid_wizard_materials(document_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_wizard_materials ON bid_wizard_materials(wizard_id, document_id);

-- 素材索引任务（Celery，generation 队列）：LLM 分段元数据 + chunks/index.md 产物
CREATE TABLE IF NOT EXISTS bid_wizard_index_tasks (
    id VARCHAR(36) PRIMARY KEY,
    wizard_id VARCHAR(36) NOT NULL REFERENCES bid_wizards(id) ON DELETE CASCADE,
    material_id VARCHAR(36) NOT NULL REFERENCES bid_wizard_materials(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(40) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    celery_task_id VARCHAR(255),
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
CREATE INDEX IF NOT EXISTS ix_bid_wizard_index_tasks_material ON bid_wizard_index_tasks(material_id);
CREATE INDEX IF NOT EXISTS ix_bid_wizard_index_tasks_user ON bid_wizard_index_tasks(user_id);

-- 同步问答微任务（API 请求内执行：问卷生成 / Spec 生成 / Spec AI 修订）
CREATE TABLE IF NOT EXISTS bid_wizard_qa_tasks (
    id VARCHAR(36) PRIMARY KEY,
    wizard_id VARCHAR(36) NOT NULL REFERENCES bid_wizards(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action VARCHAR(40) NOT NULL,                             -- questionnaire/spec_generate/spec_revise
    status VARCHAR(40) NOT NULL DEFAULT 'running',
    error_message TEXT,
    result JSON,
    billing_multiplier NUMERIC(10, 4),
    billing_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    billing_attempts INTEGER NOT NULL DEFAULT 0,
    billing_error TEXT,
    usage_finalized_at TIMESTAMPTZ,
    billing_settled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bid_wizard_qa_tasks_wizard ON bid_wizard_qa_tasks(wizard_id);
CREATE INDEX IF NOT EXISTS ix_bid_wizard_qa_tasks_user ON bid_wizard_qa_tasks(user_id);

-- 撰写任务（Celery，generation 队列）：选定章节集合一个任务
CREATE TABLE IF NOT EXISTS bid_writing_tasks (
    id VARCHAR(36) PRIMARY KEY,
    wizard_id VARCHAR(36) NOT NULL REFERENCES bid_wizards(id) ON DELETE CASCADE,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(40) NOT NULL DEFAULT 'pending',
    selected_nodes JSON,
    summary JSON,
    continue_of VARCHAR(36),
    celery_task_id VARCHAR(255),
    error_message TEXT,
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
CREATE INDEX IF NOT EXISTS ix_bid_writing_tasks_wizard ON bid_writing_tasks(wizard_id);
CREATE INDEX IF NOT EXISTS ix_bid_writing_tasks_user ON bid_writing_tasks(user_id);
CREATE INDEX IF NOT EXISTS ix_bid_writing_tasks_continue ON bid_writing_tasks(continue_of);

-- 撰写章节行：written 由页面写入 Word 成功后回报（ADR-0001 逐章写入）
CREATE TABLE IF NOT EXISTS bid_wizard_sections (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES bid_writing_tasks(id) ON DELETE CASCADE,
    node_id VARCHAR(200) NOT NULL,
    title VARCHAR(500) NOT NULL,
    summary TEXT,
    chart_plan JSON,
    status VARCHAR(40) NOT NULL DEFAULT 'pending',           -- pending/generating/generated/written/failed
    content_path VARCHAR(1000),
    word_count INTEGER,
    written_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bid_wizard_sections_task ON bid_wizard_sections(task_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_wizard_sections_node ON bid_wizard_sections(task_id, node_id);

-- 功能开放白名单（开关模式 = whitelist 时生效；模式本身在环境变量 BID_WIZARD_ACCESS_MODE）
CREATE TABLE IF NOT EXISTS bid_wizard_whitelist (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Base 模型自动注入 created_at+updated_at（models/base.py:56-57），
-- 旧版 040 建表漏 updated_at 致 ORM 查询 500；已执行过旧版 040 的库靠本条补齐
ALTER TABLE bid_wizard_whitelist
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_wizard_whitelist_user ON bid_wizard_whitelist(user_id);

-- 向导倍率列（NULL = 走全局 sales_multiplier；运营台配置 UI 属 M2）
ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS bid_wizard_qa_multiplier NUMERIC(10, 4);
ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS bid_wizard_index_multiplier NUMERIC(10, 4);
ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS bid_wizard_write_multiplier NUMERIC(10, 4);
