-- Migration 037: bid draft generation and polish tasks (标书生成/扩写润色 bjt-agent 化).
-- bid_draft_tasks: one async tender-analysis -> outline -> per-section generation run.
-- bid_draft_sections: per-outline-node generated markdown (supports regenerate/resume).
-- polish_tasks: short expand/polish/abbreviate tasks driven from the VSTO task pane.
-- New databases are covered by Base.metadata.create_all(); existing databases
-- should run this migration once. PostgreSQL 10 compatible.

CREATE TABLE IF NOT EXISTS bid_draft_tasks (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tender_document_id VARCHAR(36) REFERENCES documents(id) ON DELETE SET NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'pending',
    phase VARCHAR(40),
    analysis_result JSONB,
    outline JSONB,
    generation_options JSONB,
    summary JSONB,
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
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bid_draft_tasks_user_id ON bid_draft_tasks(user_id);
CREATE INDEX IF NOT EXISTS ix_bid_draft_tasks_status ON bid_draft_tasks(status);
CREATE INDEX IF NOT EXISTS ix_bid_draft_tasks_project_id ON bid_draft_tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_bid_draft_tasks_billing_status ON bid_draft_tasks(billing_status);
CREATE INDEX IF NOT EXISTS ix_bid_draft_tasks_continue_of ON bid_draft_tasks(continue_of);

CREATE TABLE IF NOT EXISTS bid_draft_sections (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES bid_draft_tasks(id) ON DELETE CASCADE,
    node_id VARCHAR(200) NOT NULL,
    title VARCHAR(500) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    content_path VARCHAR(1000),
    word_count INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_bid_draft_section_task_node UNIQUE (task_id, node_id)
);
CREATE INDEX IF NOT EXISTS ix_bid_draft_sections_task_id ON bid_draft_sections(task_id);
CREATE INDEX IF NOT EXISTS ix_bid_draft_sections_status ON bid_draft_sections(status);

CREATE TABLE IF NOT EXISTS polish_tasks (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode VARCHAR(20) NOT NULL,
    input_text TEXT NOT NULL,
    requirements TEXT,
    target_length INTEGER,
    result_text TEXT,
    status VARCHAR(40) NOT NULL DEFAULT 'pending',
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
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_polish_tasks_user_id ON polish_tasks(user_id);
CREATE INDEX IF NOT EXISTS ix_polish_tasks_status ON polish_tasks(status);
CREATE INDEX IF NOT EXISTS ix_polish_tasks_billing_status ON polish_tasks(billing_status);

-- Per-feature sales multipliers for the two new task kinds (034 pattern): NULL
-- falls back to the global sales_multiplier until operate-two pushes a value.
ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS bid_draft_multiplier NUMERIC(10, 4);
ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS polish_multiplier NUMERIC(10, 4);
