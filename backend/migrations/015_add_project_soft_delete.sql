-- Migration: Add project soft-delete fields
-- Regular users no longer physically delete project data. Deleted projects are
-- hidden from owner-facing lists/access, while internal users can still inspect
-- the retained documents and review results.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS deleted_by_user_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_projects_is_deleted ON projects(is_deleted);
