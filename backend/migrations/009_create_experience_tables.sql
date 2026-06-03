-- Migration: Create experience_cases, experience_skills, experience_cluster_memberships tables

CREATE TABLE IF NOT EXISTS experience_cases (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id),
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id),
    rule_doc_name VARCHAR(255) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(100) NOT NULL,
    task_intent TEXT NOT NULL,
    approach TEXT NOT NULL,
    key_insight TEXT,
    quality_score_llm REAL NOT NULL,
    quality_score_eval REAL NOT NULL,
    quality_score REAL NOT NULL,
    finding_count INTEGER NOT NULL,
    finding_ids JSONB NOT NULL,
    raw_step_count INTEGER NOT NULL,
    compressed_step_count INTEGER NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_experience_cases_task_id ON experience_cases(task_id);
CREATE INDEX IF NOT EXISTS ix_experience_cases_project_id ON experience_cases(project_id);
CREATE INDEX IF NOT EXISTS ix_experience_cases_rule_doc_name ON experience_cases(rule_doc_name);
CREATE INDEX IF NOT EXISTS ix_experience_cases_group_id ON experience_cases(group_id);
CREATE INDEX IF NOT EXISTS ix_experience_cases_user_id ON experience_cases(user_id);
CREATE INDEX IF NOT EXISTS ix_experience_cases_quality_score ON experience_cases(quality_score);
CREATE INDEX IF NOT EXISTS ix_experience_cases_group_quality ON experience_cases(group_id, quality_score DESC);
CREATE INDEX IF NOT EXISTS ix_experience_cases_user_created ON experience_cases(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS experience_skills (
    id VARCHAR(36) PRIMARY KEY,
    cluster_id VARCHAR(255) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    name VARCHAR(200) NOT NULL,
    description VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    skill_form VARCHAR(20) NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    maturity_score REAL NOT NULL DEFAULT 0.0,
    maturity_detail JSONB,
    source_case_ids JSONB NOT NULL DEFAULT '[]',
    rag_doc_id VARCHAR(255),
    last_promoted_at TIMESTAMPTZ,
    retired_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT ck_experience_skills_skill_form CHECK (skill_form IN ('verified', 'hypothesis'))
);

CREATE INDEX IF NOT EXISTS ix_experience_skills_cluster_id ON experience_skills(cluster_id);
CREATE INDEX IF NOT EXISTS ix_experience_skills_group_id ON experience_skills(group_id);
CREATE INDEX IF NOT EXISTS ix_experience_skills_skill_form ON experience_skills(skill_form);
CREATE INDEX IF NOT EXISTS ix_experience_skills_retired_at ON experience_skills(retired_at);
CREATE INDEX IF NOT EXISTS ix_experience_skills_group_retired_maturity ON experience_skills(group_id, retired_at, maturity_score DESC, confidence DESC);

CREATE TABLE IF NOT EXISTS experience_cluster_memberships (
    case_id VARCHAR(36) NOT NULL REFERENCES experience_cases(id),
    cluster_id VARCHAR(255) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    assigned_by VARCHAR(20) NOT NULL,
    similarity_score REAL,
    assigned_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_experience_cluster_memberships PRIMARY KEY (case_id, cluster_id),
    CONSTRAINT ck_experience_cluster_memberships_assigned_by CHECK (assigned_by IN ('embedding', 'llm'))
);

CREATE INDEX IF NOT EXISTS ix_experience_cluster_memberships_group_id ON experience_cluster_memberships(group_id);
