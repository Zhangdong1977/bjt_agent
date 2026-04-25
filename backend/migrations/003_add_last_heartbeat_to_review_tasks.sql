-- Migration: Add last_heartbeat to review_tasks table
-- Date: 2026-04-25
-- Purpose: Track frontend heartbeat for heartbeat cancellation feature

ALTER TABLE review_tasks ADD COLUMN last_heartbeat TIMESTAMP;