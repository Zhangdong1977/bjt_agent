-- S2-3: pair/batch duplicate tasks, document membership, occurrences,
-- pair matrix summaries and cross-document evidence clusters.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS duplicate_mode VARCHAR(10) NOT NULL DEFAULT 'pair';
ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS duplicate_mode VARCHAR(10) NOT NULL DEFAULT 'pair';
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS duplicate_party_key VARCHAR(120);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS duplicate_display_name VARCHAR(255);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS duplicate_ordinal INTEGER;

DO $$ BEGIN
    ALTER TABLE projects ADD CONSTRAINT ck_projects_duplicate_mode
        CHECK (duplicate_mode IN ('pair', 'batch'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE review_tasks ADD CONSTRAINT ck_review_tasks_duplicate_mode
        CHECK (duplicate_mode IN ('pair', 'batch'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS duplicate_document_members (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    party_key VARCHAR(120) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    ordinal INTEGER NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ux_duplicate_member_task_document UNIQUE(task_id, document_id),
    CONSTRAINT ux_duplicate_member_task_ordinal UNIQUE(task_id, ordinal)
);
CREATE INDEX IF NOT EXISTS ix_duplicate_members_task ON duplicate_document_members(task_id, ordinal);

CREATE TABLE IF NOT EXISTS duplicate_evidence_clusters (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    finding_id VARCHAR(36) REFERENCES duplicate_results(id) ON DELETE SET NULL,
    cluster_key VARCHAR(128) NOT NULL,
    content_type VARCHAR(30) NOT NULL,
    document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    representative_excerpt TEXT NOT NULL DEFAULT '',
    evidence_strength NUMERIC(5,4),
    coverage_status VARCHAR(20) NOT NULL DEFAULT 'insufficient',
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ux_duplicate_cluster_task_key UNIQUE(task_id, cluster_key),
    CONSTRAINT ck_duplicate_cluster_coverage
        CHECK (coverage_status IN ('complete', 'partial', 'insufficient'))
);
CREATE INDEX IF NOT EXISTS ix_duplicate_clusters_task ON duplicate_evidence_clusters(task_id);

CREATE TABLE IF NOT EXISTS duplicate_occurrences (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    finding_id VARCHAR(36) REFERENCES duplicate_results(id) ON DELETE CASCADE,
    cluster_id VARCHAR(36) REFERENCES duplicate_evidence_clusters(id) ON DELETE CASCADE,
    document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    block_id VARCHAR(255),
    excerpt TEXT NOT NULL DEFAULT '',
    location JSONB NOT NULL DEFAULT '{}'::jsonb,
    channel VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_duplicate_occurrences_task ON duplicate_occurrences(task_id, document_id);
CREATE INDEX IF NOT EXISTS ix_duplicate_occurrences_finding ON duplicate_occurrences(finding_id);
CREATE INDEX IF NOT EXISTS ix_duplicate_occurrences_cluster ON duplicate_occurrences(cluster_id);

CREATE TABLE IF NOT EXISTS duplicate_pair_summaries (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    left_document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    right_document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    finding_count INTEGER NOT NULL DEFAULT 0,
    suspicious_count INTEGER NOT NULL DEFAULT 0,
    unknown_count INTEGER NOT NULL DEFAULT 0,
    max_evidence_strength NUMERIC(5,4),
    coverage_status VARCHAR(20) NOT NULL DEFAULT 'insufficient',
    channel_hits JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ux_duplicate_pair_summary UNIQUE(task_id, left_document_id, right_document_id),
    CONSTRAINT ck_duplicate_pair_summary_coverage
        CHECK (coverage_status IN ('complete', 'partial', 'insufficient'))
);
CREATE INDEX IF NOT EXISTS ix_duplicate_pair_summaries_task
    ON duplicate_pair_summaries(task_id);
