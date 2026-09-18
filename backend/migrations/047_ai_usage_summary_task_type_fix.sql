-- 047: 矫正 ai_usage_task_summary.task_type 误标（幂等，可重复执行）
-- 背景（2026-09-18 生产实测）：usage_summary._MERGE_STATUS_SQL 曾把 task_type
-- 硬编码 'review'，而 review_tasks 同时承载 review（标书检查）与 duplicate
-- （标书查重）两类任务 → 生产 5/5 个查重任务的用量汇总全部被标成 review；
-- 043 的历史回填语句同源同病（凡存在于 review_tasks 即标 'review'）。
-- 矫正：按 review_tasks 真实 task_type 绝对值覆盖（IS DISTINCT FROM 幂等）。
UPDATE ai_usage_task_summary s
SET task_type = r.task_type, updated_at = now()
FROM review_tasks r
WHERE r.id = s.id
  AND s.task_type IS DISTINCT FROM r.task_type;

-- 校验：查重任务的汇总不应再有非 duplicate 标记（应返回 0 行）
-- SELECT s.id FROM ai_usage_task_summary s JOIN review_tasks r ON r.id = s.id
-- WHERE r.task_type = 'duplicate' AND s.task_type <> 'duplicate';
