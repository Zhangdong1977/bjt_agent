-- 041_bid_wizard_admin.sql
-- AI编标 M2 ⑤（决策 36，2026-09-09 第二轮 grilling）：功能开关 DB 化。
-- env BID_WIZARD_ACCESS_MODE 降为安装初始值/回退，运营台经 internal API 推送到本表，
-- 读取顺序 DB 行优先；白名单沿用 040 的 bid_wizard_whitelist（不新建表）。

CREATE TABLE IF NOT EXISTS bid_wizard_settings (
    id          VARCHAR(20) PRIMARY KEY,
    mode        VARCHAR(20) NOT NULL DEFAULT 'disabled'
                CHECK (mode IN ('enabled', 'whitelist', 'disabled')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO bid_wizard_settings (id, mode)
VALUES ('default', 'disabled')
ON CONFLICT (id) DO NOTHING;
