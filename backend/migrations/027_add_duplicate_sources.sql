-- S2-1: immutable tender/public-reference source snapshots.
-- Existing uploads remain readable; new source documents persist a hash and
-- version during parsing so every tender/public conclusion can cite a stable
-- document snapshot instead of model memory or a live web page.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_version VARCHAR(120);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_snapshot_hash VARCHAR(64);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_snapshot_path VARCHAR(500);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_uri VARCHAR(500);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_published_at TIMESTAMPTZ;
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_metadata JSONB;

CREATE INDEX IF NOT EXISTS ix_documents_source_snapshot_hash
    ON documents(source_snapshot_hash);
CREATE INDEX IF NOT EXISTS ix_documents_project_source_type
    ON documents(project_id, doc_type, created_at DESC)
    WHERE doc_type IN ('duplicate_tender', 'duplicate_public_reference');

-- Uploaded source files are already immutable workspace snapshots.  Backfill
-- the path for legacy S2-1 rows; the application computes a streaming SHA-256
-- on the next parse/reparse rather than doing expensive hashing in SQL.
UPDATE documents
   SET source_snapshot_path = file_path
 WHERE doc_type IN ('duplicate_tender', 'duplicate_public_reference')
   AND source_snapshot_path IS NULL;
