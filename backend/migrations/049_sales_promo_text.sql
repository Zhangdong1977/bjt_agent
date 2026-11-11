-- Migration 049: configurable recharge promo banner text.
-- PostgreSQL 10 compatible.
--
-- The recharge modal's promo banner moves from a frontend hardcoded string to
-- an operate-two configurable field delivered by the sales snapshot.  Empty
-- string/NULL hides the banner (campaign taken down).  Seed the current
-- production copy so behaviour is unchanged until an operator edits it.
-- Re-running is safe: only NULL rows (never saved by operate-two, which always
-- sends "" when cleared) are seeded.

ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS promo_text VARCHAR(500);

UPDATE sales_configs
SET promo_text = '为庆祝标捷通智能标书检查服务上线，推出重磅优惠活动，充值满50元，赠送5元；满100元，赠送20元；满300元，赠送100元；满1000元，赠送500元！充值越多，赠送越多！活动随时下架，欢迎尽快充值！'
WHERE promo_text IS NULL;

COMMENT ON COLUMN sales_configs.promo_text IS '充值弹窗活动横幅文案（空=不显示），由 operate-two 销售配置快照下发';
