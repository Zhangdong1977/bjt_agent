-- Migration 022: duplicate-check project/task/result support.
-- Idempotent for existing PostgreSQL databases; new databases use SQLAlchemy create_all.

ALTER TABLE projects ADD COLUMN IF NOT EXISTS project_type VARCHAR(20) NOT NULL DEFAULT 'review';
CREATE INDEX IF NOT EXISTS ix_projects_project_type ON projects(project_type);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS structure_quality VARCHAR(20);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS structure_index_path VARCHAR(500);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS structure_analysis JSONB;
CREATE INDEX IF NOT EXISTS ix_documents_structure_quality ON documents(structure_quality);

ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS task_type VARCHAR(20) NOT NULL DEFAULT 'review';
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS config_snapshot JSONB;
CREATE INDEX IF NOT EXISTS ix_review_tasks_task_type ON review_tasks(task_type);

ALTER TABLE ai_usage_task_summary ADD COLUMN IF NOT EXISTS task_type VARCHAR(20);
CREATE INDEX IF NOT EXISTS ix_ai_usage_task_summary_task_type ON ai_usage_task_summary(task_type);

ALTER TABLE todo_items ADD COLUMN IF NOT EXISTS todo_type VARCHAR(20) NOT NULL DEFAULT 'review_rule';
ALTER TABLE todo_items ADD COLUMN IF NOT EXISTS display_name VARCHAR(511);
ALTER TABLE todo_items ADD COLUMN IF NOT EXISTS document_a_id VARCHAR(36);
ALTER TABLE todo_items ADD COLUMN IF NOT EXISTS document_b_id VARCHAR(36);
ALTER TABLE todo_items ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(20);
CREATE INDEX IF NOT EXISTS ix_todo_items_todo_type ON todo_items(todo_type);
CREATE INDEX IF NOT EXISTS ix_todo_items_document_a_id ON todo_items(document_a_id);
CREATE INDEX IF NOT EXISTS ix_todo_items_document_b_id ON todo_items(document_b_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_todo_items_session_document_pair
    ON todo_items(session_id, document_a_id, document_b_id);

CREATE TABLE IF NOT EXISTS duplicate_pair_results (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    todo_id VARCHAR(36) NOT NULL REFERENCES todo_items(id) ON DELETE CASCADE,
    document_a_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_b_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    execution_mode VARCHAR(20) NOT NULL,
    conclusion VARCHAR(40) NOT NULL,
    summary TEXT,
    suspicious_count INTEGER NOT NULL DEFAULT 0,
    excluded_count INTEGER NOT NULL DEFAULT 0,
    matches JSONB NOT NULL DEFAULT '[]'::jsonb,
    diagnostics JSONB,
    report_path VARCHAR(500),
    rule_name VARCHAR(255) NOT NULL,
    rule_version VARCHAR(50),
    rule_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_duplicate_pair_results_todo_id UNIQUE(todo_id),
    CONSTRAINT uq_duplicate_pair_results_task_documents UNIQUE(task_id, document_a_id, document_b_id)
);
CREATE INDEX IF NOT EXISTS ix_duplicate_pair_results_task_id ON duplicate_pair_results(task_id);
CREATE INDEX IF NOT EXISTS ix_duplicate_pair_results_conclusion ON duplicate_pair_results(conclusion);
CREATE INDEX IF NOT EXISTS ix_duplicate_pair_results_rule_hash ON duplicate_pair_results(rule_hash);
