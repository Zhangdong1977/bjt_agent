-- Migration 017: real BoCom payment bridge columns on billing_orders.
--   external_order_no  : operate-two 的 payMerTranNo，用于轮询交行网关取支付结果
--   external_qr_payload: 交行二维码文本(displayCodeText)，缓存以便重复渲染同一订单而不重复下单
-- New databases are covered by init_db() create_all. Existing databases run this manually via psql.

ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS external_order_no VARCHAR(64);
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS external_qr_payload TEXT;

CREATE INDEX IF NOT EXISTS ix_billing_orders_external_order_no ON billing_orders(external_order_no);

