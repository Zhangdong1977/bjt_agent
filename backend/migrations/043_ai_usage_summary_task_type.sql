-- 043: ai_usage_task_summary 补 task_type 维度列（幂等，可重复执行）
-- 背景（2026-09-11 20:07 生产实锤）：6d22113 复盘修复给 usage_summary 的
-- _MERGE_STATUS_SQL / _MERGE_BLIND_STATUS_SQL / _EXTRA_TASK_MERGES 统一赋值
-- task_type，但该列只在预发布存在（预发布表由应用 create_all 建出带列），
-- 生产表由 014 迁移建出、从未有加列迁移 → 生产 M2 发布后首个结算任务即
-- SUMMARY_REFRESH_FAILED (UndefinedColumnError) 卡 billing retry、reconcile
-- 每 120s 崩——「先起服务后迁移 vs 先迁移后起服务建出不同 schema」第四例
-- （同族：023 vsto_tool_calls / 040 白名单表 / 041 开关表）。
ALTER TABLE ai_usage_task_summary ADD COLUMN IF NOT EXISTS task_type VARCHAR(40);

-- 历史行回填（幂等）：task_type 仍为 NULL 的行按各任务源表反查
UPDATE ai_usage_task_summary s SET task_type = 'review'
WHERE task_type IS NULL AND EXISTS (SELECT 1 FROM review_tasks t WHERE t.id = s.id);
UPDATE ai_usage_task_summary s SET task_type = 'blind_check'
WHERE task_type IS NULL AND EXISTS (SELECT 1 FROM blind_check_tasks t WHERE t.id = s.id);
UPDATE ai_usage_task_summary s SET task_type = 'bid_draft'
WHERE task_type IS NULL AND EXISTS (SELECT 1 FROM bid_draft_tasks t WHERE t.id = s.id);
UPDATE ai_usage_task_summary s SET task_type = 'polish'
WHERE task_type IS NULL AND EXISTS (SELECT 1 FROM polish_tasks t WHERE t.id = s.id);
UPDATE ai_usage_task_summary s SET task_type = 'bid_wizard_qa'
WHERE task_type IS NULL AND EXISTS (SELECT 1 FROM bid_wizard_qa_tasks t WHERE t.id = s.id);
UPDATE ai_usage_task_summary s SET task_type = 'bid_wizard_index'
WHERE task_type IS NULL AND EXISTS (SELECT 1 FROM bid_wizard_index_tasks t WHERE t.id = s.id);
UPDATE ai_usage_task_summary s SET task_type = 'bid_wizard_write'
WHERE task_type IS NULL AND EXISTS (SELECT 1 FROM bid_writing_tasks t WHERE t.id = s.id);
