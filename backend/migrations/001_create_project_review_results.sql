-- Migration: Create project_review_results table
-- Run this manually or via Alembic

CREATE TABLE IF NOT EXISTS project_review_results (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    requirement_key VARCHAR(255) NOT NULL,
    requirement_content TEXT NOT NULL,
    bid_content TEXT,
    is_compliant BOOLEAN DEFAULT FALSE,
    severity VARCHAR(50) NOT NULL,
    location_page INTEGER,
    location_line INTEGER,
    suggestion TEXT,
    explanation TEXT,
    source_task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id),
    merged_from_count INTEGER DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_review_results_project_id ON project_review_results(project_id);