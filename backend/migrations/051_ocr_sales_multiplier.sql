-- Migration 051: OCR-specific sales multiplier.
-- PostgreSQL 10 compatible.
--
-- OCR spend (preannotation sweep + on-demand understand_image) has become the
-- dominant per-task cost (task 607de39c: 3075 calls / 86.10 CNY of a 90.97 CNY
-- total) but is currently priced together with LLM usage under the single
-- per-kind billing_multiplier.  This adds an independent, global OCR sales
-- multiplier: NULL keeps the legacy single-multiplier settlement unchanged;
-- a configured value prices the OCR portion of a task separately.
--
-- - sales_configs.ocr_multiplier: runtime copy delivered by the operate-two
--   sales snapshot (global, not per task-kind; NULL = do not split).
-- - *_tasks.ocr_billing_multiplier: per-task snapshot taken at task start,
--   same principle as billing_multiplier (migration 031).
-- - consumption_records.ocr_*: audit columns recording the split actually
--   applied at settlement time.
-- Re-running is safe (ADD COLUMN IF NOT EXISTS).

ALTER TABLE sales_configs
    ADD COLUMN IF NOT EXISTS ocr_multiplier NUMERIC(10, 4);

COMMENT ON COLUMN sales_configs.ocr_multiplier IS 'OCR 用量独立销售倍率（全局；NULL=不拆分，OCR 成本跟随任务功能倍率）';

ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS ocr_billing_multiplier NUMERIC(10, 4);
ALTER TABLE blind_check_tasks
    ADD COLUMN IF NOT EXISTS ocr_billing_multiplier NUMERIC(10, 4);
ALTER TABLE bid_draft_tasks
    ADD COLUMN IF NOT EXISTS ocr_billing_multiplier NUMERIC(10, 4);
ALTER TABLE polish_tasks
    ADD COLUMN IF NOT EXISTS ocr_billing_multiplier NUMERIC(10, 4);
ALTER TABLE bid_wizard_qa_tasks
    ADD COLUMN IF NOT EXISTS ocr_billing_multiplier NUMERIC(10, 4);
ALTER TABLE bid_wizard_index_tasks
    ADD COLUMN IF NOT EXISTS ocr_billing_multiplier NUMERIC(10, 4);
ALTER TABLE bid_writing_tasks
    ADD COLUMN IF NOT EXISTS ocr_billing_multiplier NUMERIC(10, 4);

COMMENT ON COLUMN review_tasks.ocr_billing_multiplier IS '任务发起时固化的 OCR 销售倍率快照（NULL=结算不拆分，与 051 配套）';

ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS ocr_cost_cny NUMERIC(12, 6);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS ocr_multiplier NUMERIC(10, 4);

COMMENT ON COLUMN consumption_records.ocr_cost_cny IS '结算时任务 OCR 用量成本拆分（usage_type=ocr）；NULL=未拆分（旧口径）';
COMMENT ON COLUMN consumption_records.ocr_multiplier IS '结算时生效的 OCR 销售倍率；NULL=未拆分（旧口径）';
