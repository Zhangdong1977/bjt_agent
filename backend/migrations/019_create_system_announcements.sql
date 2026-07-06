-- Migration 019: system announcements (系统公告) + per-user read state.
-- New databases are covered by init_db() create_all. Existing databases should
-- run this file manually via psql.

CREATE TABLE IF NOT EXISTS system_announcements (
    id VARCHAR(36) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'info',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    published_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    created_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_system_announcements_title        ON system_announcements(title);
CREATE INDEX IF NOT EXISTS ix_system_announcements_severity     ON system_announcements(severity);
CREATE INDEX IF NOT EXISTS ix_system_announcements_is_active    ON system_announcements(is_active);
CREATE INDEX IF NOT EXISTS ix_system_announcements_published_at ON system_announcements(published_at);
CREATE INDEX IF NOT EXISTS ix_system_announcements_expires_at   ON system_announcements(expires_at);
CREATE INDEX IF NOT EXISTS ix_system_announcements_created_by   ON system_announcements(created_by);

CREATE TABLE IF NOT EXISTS system_announcement_reads (
    id VARCHAR(36) PRIMARY KEY,
    announcement_id VARCHAR(36) NOT NULL REFERENCES system_announcements(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_announcement_read_user UNIQUE (announcement_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_system_announcement_reads_announcement_id ON system_announcement_reads(announcement_id);
CREATE INDEX IF NOT EXISTS ix_system_announcement_reads_user_id         ON system_announcement_reads(user_id);
