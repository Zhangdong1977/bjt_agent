-- Migration: Add external user fields to users table (for ai_usage_records attribution)
-- 把 aiCheckLogin 返回的 sys_user 维度冗余到本地 users 表，避免用量记录每次都带 token 反查。
-- 生产用 psql 手动执行；dev/测试环境 init_db() 的 create_all 会补列（全新库），
-- 已存在的库需手动跑本文件。
ALTER TABLE users ADD COLUMN IF NOT EXISTS external_user_id BIGINT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS enterprise_name VARCHAR(200);
ALTER TABLE users ADD COLUMN IF NOT EXISTS interior_user BOOLEAN;

CREATE INDEX IF NOT EXISTS ix_users_external_user_id ON users(external_user_id);
CREATE INDEX IF NOT EXISTS ix_users_enterprise_name ON users(enterprise_name);
