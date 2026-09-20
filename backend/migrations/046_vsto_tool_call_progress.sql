-- 046: vsto_tool_calls 补进度心跳两列（幂等，可重复执行）
-- 背景（2026-09-17，ADR-0003「工具调用从固定超时改为分块 + 活性判定」）：VSTO 扫描大
-- 文档期间按批上报进度，页面经 POST /api/vsto-tools/progress 落到这两列并写 Redis，
-- broker 以“无进展超过空闲窗口”而非固定墙钟判超时；游标同时用于排障（卡在第几段）。
-- 同族教训（023/040/041/043/044）：新代码引用的每一列必须有对应迁移，预发布
-- create_all 建出的列不代表生产 schema 就绪。
ALTER TABLE vsto_tool_calls ADD COLUMN IF NOT EXISTS last_progress_at TIMESTAMPTZ NULL;
ALTER TABLE vsto_tool_calls ADD COLUMN IF NOT EXISTS progress_cursor INTEGER NULL;
