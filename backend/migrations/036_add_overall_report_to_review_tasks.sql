-- 总体报告：检查完成后由报告 Agent 汇总各子 agent 输出生成
-- （结构定义见 backend/agent/report_agent.py::assemble_report）
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS overall_report JSONB;
