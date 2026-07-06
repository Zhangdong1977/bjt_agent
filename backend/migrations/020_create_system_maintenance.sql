-- Migration 020: 系统维护状态（全局单行）。
-- New databases are covered by init_db() create_all. Existing databases should
-- run this file manually via psql. 应用启动时 lifespan 会 ensure 单行种子存在。

CREATE TABLE IF NOT EXISTS system_maintenance (
    id VARCHAR(36) PRIMARY KEY,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    reason TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ,
    updated_by VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_system_maintenance_is_enabled ON system_maintenance(is_enabled);
CREATE INDEX IF NOT EXISTS ix_system_maintenance_updated_by   ON system_maintenance(updated_by);

-- 单行种子：id 恒为 'maintenance'，默认关闭。
-- 用 ON CONFLICT 保证幂等（重复执行 / 与 lifespan ensure 共存都不冲突）。
INSERT INTO system_maintenance (id, is_enabled, reason, created_at, updated_at)
VALUES ('maintenance', FALSE, '', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
