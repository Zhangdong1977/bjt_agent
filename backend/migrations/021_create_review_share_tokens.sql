-- Migration 021: 审查结果分享令牌表。
-- New databases are covered by init_db() create_all. Existing databases should
-- run this file manually via psql.
-- 用于把某次审查任务(rev的任务)的结果分享给其他登录用户：持令牌者无需是项目所有者。

CREATE TABLE IF NOT EXISTS review_share_tokens (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    token VARCHAR(64) NOT NULL,
    created_by_user_id VARCHAR(36) NOT NULL,
    expires_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_review_share_tokens_token      ON review_share_tokens(token);
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_share_tokens_active_task_creator
    ON review_share_tokens(task_id, created_by_user_id)
    WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_review_share_tokens_project_id        ON review_share_tokens(project_id);
CREATE INDEX IF NOT EXISTS ix_review_share_tokens_task_id           ON review_share_tokens(task_id);
CREATE INDEX IF NOT EXISTS ix_review_share_tokens_created_by_user_id ON review_share_tokens(created_by_user_id);
