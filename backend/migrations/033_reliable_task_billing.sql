-- Migration 033: reliable task dispatch and settlement lifecycle.
-- PostgreSQL 10 compatible. Existing tasks are deliberately marked "legacy"
-- so deploying this migration never retroactively charges historical work.

ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS billing_status VARCHAR(20) NOT NULL DEFAULT 'legacy';
ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS billing_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS billing_error TEXT;
ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS usage_finalized_at TIMESTAMPTZ;
ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS billing_settled_at TIMESTAMPTZ;
ALTER TABLE review_tasks ALTER COLUMN billing_status SET DEFAULT 'pending';
CREATE INDEX IF NOT EXISTS ix_review_tasks_billing_status ON review_tasks(billing_status);

ALTER TABLE blind_check_tasks
    ADD COLUMN IF NOT EXISTS billing_multiplier NUMERIC(10, 4);
ALTER TABLE blind_check_tasks
    ADD COLUMN IF NOT EXISTS billing_status VARCHAR(20) NOT NULL DEFAULT 'legacy';
ALTER TABLE blind_check_tasks
    ADD COLUMN IF NOT EXISTS billing_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blind_check_tasks
    ADD COLUMN IF NOT EXISTS billing_error TEXT;
ALTER TABLE blind_check_tasks
    ADD COLUMN IF NOT EXISTS usage_finalized_at TIMESTAMPTZ;
ALTER TABLE blind_check_tasks
    ADD COLUMN IF NOT EXISTS billing_settled_at TIMESTAMPTZ;
ALTER TABLE blind_check_tasks ALTER COLUMN billing_status SET DEFAULT 'pending';
CREATE INDEX IF NOT EXISTS ix_blind_check_tasks_billing_status ON blind_check_tasks(billing_status);

ALTER TABLE consumption_records ALTER COLUMN project_id DROP NOT NULL;
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS task_type VARCHAR(20) NOT NULL DEFAULT 'review';
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS task_status VARCHAR(50);
CREATE INDEX IF NOT EXISTS ix_consumption_records_task_type ON consumption_records(task_type);
CREATE INDEX IF NOT EXISTS ix_consumption_records_task_status ON consumption_records(task_status);

UPDATE consumption_records c
SET task_type = r.task_type, task_status = r.status
FROM review_tasks r
WHERE r.id = c.task_id;

CREATE TABLE IF NOT EXISTS task_dispatch_outbox (
    id VARCHAR(36) PRIMARY KEY,
    task_kind VARCHAR(20) NOT NULL,
    task_id VARCHAR(36) NOT NULL,
    celery_task_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ,
    dispatched_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_task_dispatch_kind_task UNIQUE (task_kind, task_id),
    CONSTRAINT uq_task_dispatch_celery_id UNIQUE (celery_task_id),
    CONSTRAINT ck_task_dispatch_status CHECK (status IN ('pending', 'retry', 'dispatched', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_task_dispatch_outbox_task_kind ON task_dispatch_outbox(task_kind);
CREATE INDEX IF NOT EXISTS ix_task_dispatch_outbox_task_id ON task_dispatch_outbox(task_id);
CREATE INDEX IF NOT EXISTS ix_task_dispatch_outbox_status ON task_dispatch_outbox(status);
CREATE INDEX IF NOT EXISTS ix_task_dispatch_outbox_next_attempt ON task_dispatch_outbox(next_attempt_at);

-- Also fail safe if an operator accidentally started the new code before this
-- migration: only new-code tasks have a transactional outbox row. Everything
-- else remains historical and must never be retroactively charged.
UPDATE review_tasks r
SET billing_status = 'legacy'
WHERE r.billing_status = 'pending'
  AND NOT EXISTS (
      SELECT 1 FROM task_dispatch_outbox o
      WHERE o.task_id = r.id AND o.task_kind IN ('review', 'duplicate')
  );
UPDATE blind_check_tasks b
SET billing_status = 'legacy'
WHERE b.billing_status = 'pending'
  AND NOT EXISTS (
      SELECT 1 FROM task_dispatch_outbox o
      WHERE o.task_id = b.id AND o.task_kind = 'blind_check'
  );

-- Preserve the already-created consumption rows as settled audit history.
UPDATE review_tasks r
SET billing_status = 'settled', billing_settled_at = COALESCE(r.completed_at, now())
WHERE EXISTS (SELECT 1 FROM consumption_records c WHERE c.task_id = r.id);
