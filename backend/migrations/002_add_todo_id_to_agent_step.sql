-- Migration: Add todo_id to agent_steps table
-- Date: 2026-04-25

-- 添加 todo_id 列（允许 NULL）
ALTER TABLE agent_steps ADD COLUMN todo_id VARCHAR(36);

-- 添加外键约束
ALTER TABLE agent_steps
ADD CONSTRAINT fk_agent_steps_todo_id
FOREIGN KEY (todo_id) REFERENCES todo_items(id) ON DELETE CASCADE;

-- 添加索引
CREATE INDEX IF NOT EXISTS idx_agent_steps_todo_id ON agent_steps(todo_id);

-- 注意：task_id 和 todo_id 不能同时为空
-- 这个约束由业务逻辑保证，不在数据库层面强制