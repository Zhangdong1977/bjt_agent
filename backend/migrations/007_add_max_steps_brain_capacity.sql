-- Add max_steps and brain_capacity columns to todo_items table
ALTER TABLE todo_items ADD COLUMN IF NOT EXISTS max_steps INTEGER NOT NULL DEFAULT 100;
ALTER TABLE todo_items ADD COLUMN IF NOT EXISTS brain_capacity FLOAT NOT NULL DEFAULT 0.0;
