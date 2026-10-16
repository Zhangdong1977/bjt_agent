-- 048: ai_usage_task_summary 印章调用量拆列（幂等，可重复执行）
-- 背景（2026-10-15）：印章检测（provider=baidu_seal）与文字 OCR 同记
-- usage_type='ocr'，usage_summary 的 _UPSERT_SQL 按 usage_type 计数把两类
-- 流水混入 ocr_calls/ocr_images/ocr_words_result_num —— 运营台自 2026-10-14
-- 印章检测上线起这三项指标虚高一倍。
-- 拆分：ocr_* 三项只计 provider <> 'baidu_seal' 的流水，新增 seal_calls
-- 单独计印章调用。历史行在下一次 refresh_task_summary 触碰该任务时按
-- 绝对值覆盖自动矫正，无需回填脚本。
-- 同族教训（023/040/041/044）：新代码引用的每一列必须有对应迁移，预发布
-- create_all 建出的列不代表生产 schema 就绪。
ALTER TABLE ai_usage_task_summary
    ADD COLUMN IF NOT EXISTS seal_calls INTEGER NOT NULL DEFAULT 0;

-- 校验：部署后跑一个含印章调用的任务，其汇总行应满足
-- ocr_calls = 真实文字 OCR 次数、seal_calls = 印章调用次数
-- SELECT id, ocr_calls, seal_calls FROM ai_usage_task_summary ORDER BY updated_at DESC LIMIT 5;
