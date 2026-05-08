-- Add max_concurrency column to review_tasks table
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS max_concurrency INTEGER NOT NULL DEFAULT 2;
