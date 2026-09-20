-- Migration 039: 开放 API 通道（/api/v1/open，供 WorkBuddy skill 等第三方客户端调用）。
-- 三件事：① api_keys 表（X-Api-Key 鉴权，服务端只存 sha256 哈希）；
-- ② review_tasks.client_channel（'web'|'api'——API 通道任务豁免前端心跳超时取消）；
-- ③ projects/documents.source（'web'|'api'——API 隐式项目/文档与 Web 草稿配额互相隔离，
--    Web 项目历史列表默认隐藏 source='api'）。
-- 幂等，可重复执行。PostgreSQL 10 compatible。

CREATE TABLE IF NOT EXISTS api_keys (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL DEFAULT 'default',
    key_prefix VARCHAR(16) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    max_active_tasks INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys(user_id);

ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS client_channel VARCHAR(20) NOT NULL DEFAULT 'web';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'web';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'web';
