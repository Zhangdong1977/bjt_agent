-- Blind-mark compliance check: task, VSTO capability session/calls and findings.
-- New databases are covered by Base.metadata.create_all(); existing databases
-- should run this migration once.

CREATE TABLE IF NOT EXISTS vsto_tool_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_instance_id VARCHAR(255),
    document_name VARCHAR(500),
    document_key VARCHAR(500),
    document_revision VARCHAR(255),
    snapshot_id VARCHAR(36),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    last_seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_vsto_tool_sessions_user_id ON vsto_tool_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_vsto_tool_sessions_status ON vsto_tool_sessions(status);
CREATE INDEX IF NOT EXISTS ix_vsto_tool_sessions_expires_at ON vsto_tool_sessions(expires_at);
CREATE INDEX IF NOT EXISTS ix_vsto_tool_sessions_snapshot_id ON vsto_tool_sessions(snapshot_id);

CREATE TABLE IF NOT EXISTS blind_check_tasks (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool_session_id VARCHAR(36) NOT NULL REFERENCES vsto_tool_sessions(id) ON DELETE RESTRICT,
    requirement_text TEXT NOT NULL,
    document_name VARCHAR(500),
    document_key VARCHAR(500),
    document_revision VARCHAR(255),
    snapshot_id VARCHAR(36),
    scope JSONB,
    status VARCHAR(40) NOT NULL DEFAULT 'created',
    celery_task_id VARCHAR(255),
    summary JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_blind_check_tasks_user_id ON blind_check_tasks(user_id);
CREATE INDEX IF NOT EXISTS ix_blind_check_tasks_status ON blind_check_tasks(status);
CREATE INDEX IF NOT EXISTS ix_blind_check_tasks_tool_session_id ON blind_check_tasks(tool_session_id);
CREATE INDEX IF NOT EXISTS ix_blind_check_tasks_snapshot_id ON blind_check_tasks(snapshot_id);

CREATE TABLE IF NOT EXISTS vsto_tool_calls (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES blind_check_tasks(id) ON DELETE CASCADE,
    session_id VARCHAR(36) NOT NULL REFERENCES vsto_tool_sessions(id) ON DELETE CASCADE,
    call_id VARCHAR(36) NOT NULL UNIQUE,
    tool_name VARCHAR(100) NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    result JSONB,
    error_message TEXT,
    requested_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    answered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_vsto_tool_calls_task_id ON vsto_tool_calls(task_id);
CREATE INDEX IF NOT EXISTS ix_vsto_tool_calls_session_id ON vsto_tool_calls(session_id);
CREATE INDEX IF NOT EXISTS ix_vsto_tool_calls_status ON vsto_tool_calls(status);
CREATE INDEX IF NOT EXISTS ix_vsto_tool_calls_expires_at ON vsto_tool_calls(expires_at);

CREATE TABLE IF NOT EXISTS blind_check_findings (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES blind_check_tasks(id) ON DELETE CASCADE,
    category VARCHAR(40) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    verdict VARCHAR(20) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    evidence_text TEXT,
    page_number INTEGER,
    paragraph_index INTEGER,
    location JSONB,
    rule_reference TEXT,
    confidence DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_blind_check_findings_task_id ON blind_check_findings(task_id);
CREATE INDEX IF NOT EXISTS ix_blind_check_findings_category ON blind_check_findings(category);
CREATE INDEX IF NOT EXISTS ix_blind_check_findings_verdict ON blind_check_findings(verdict);
