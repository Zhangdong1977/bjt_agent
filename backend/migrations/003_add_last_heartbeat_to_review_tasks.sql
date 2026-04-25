-- Migration: Add last_heartbeat to review_tasks table
-- Date: 2026-04-25
-- Purpose: Track frontend heartbeat for automatic task cancellation

ALTER TABLE review_tasks ADD COLUMN last_heartbeat TIMESTAMP;
CREATE INDEX idx_review_tasks_last_heartbeat ON review_tasks(last_heartbeat);