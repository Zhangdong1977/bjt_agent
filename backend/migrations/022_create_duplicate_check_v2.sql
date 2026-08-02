-- Technical bid duplicate checking (fresh v2 design).
-- Safe to run on databases that contain only the review schema. The old,
-- abandoned duplicate-check prototype used different result tables and is not
-- required by this migration.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS project_type VARCHAR(20) NOT NULL DEFAULT 'review';
CREATE INDEX IF NOT EXISTS ix_projects_project_type ON projects(project_type);
CREATE INDEX IF NOT EXISTS ix_projects_user_type_created
    ON projects(user_id, project_type, created_at DESC);

ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS task_type VARCHAR(20) NOT NULL DEFAULT 'review';
CREATE INDEX IF NOT EXISTS ix_review_tasks_task_type ON review_tasks(task_type);
CREATE INDEX IF NOT EXISTS ix_review_tasks_project_type_created
    ON review_tasks(project_id, task_type, created_at DESC);

-- The API checks these limits too, while the partial unique indexes close the
-- concurrent-upload race between two requests for the same side.
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_duplicate_draft_side
    ON documents(owner_user_id, doc_type)
    WHERE project_id IS NULL
      AND doc_type IN ('duplicate_left', 'duplicate_right');
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_duplicate_project_side
    ON documents(project_id, doc_type)
    WHERE project_id IS NOT NULL
      AND doc_type IN ('duplicate_left', 'duplicate_right');

DO $$ BEGIN
    ALTER TABLE projects ADD CONSTRAINT ck_projects_project_type
        CHECK (project_type IN ('review', 'duplicate'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE review_tasks ADD CONSTRAINT ck_review_tasks_task_type
        CHECK (task_type IN ('review', 'duplicate'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS duplicate_results (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    todo_id VARCHAR(36) REFERENCES todo_items(id) ON DELETE SET NULL,
    rule_doc_name VARCHAR(255) NOT NULL,
    check_item_name VARCHAR(255) NOT NULL,
    verdict VARCHAR(30) NOT NULL,
    similarity_score NUMERIC(5,4) NOT NULL,
    match_type VARCHAR(30) NOT NULL,
    left_document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    left_excerpt TEXT NOT NULL,
    left_location JSONB NOT NULL DEFAULT '{}'::jsonb,
    right_document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    right_excerpt TEXT NOT NULL,
    right_location JSONB NOT NULL DEFAULT '{}'::jsonb,
    explanation TEXT NOT NULL,
    suggestion TEXT,
    evidence JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_duplicate_results_verdict
        CHECK (verdict IN ('reasonable', 'suspicious')),
    CONSTRAINT ck_duplicate_results_similarity
        CHECK (similarity_score >= 0 AND similarity_score <= 1)
);

CREATE INDEX IF NOT EXISTS ix_duplicate_results_task_id ON duplicate_results(task_id);
CREATE INDEX IF NOT EXISTS ix_duplicate_results_todo_id ON duplicate_results(todo_id);
CREATE INDEX IF NOT EXISTS ix_duplicate_results_verdict ON duplicate_results(verdict);
