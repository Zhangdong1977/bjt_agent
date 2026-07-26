-- S2-0 deterministic parser artifacts and coverage facts.
-- New databases receive these columns through SQLAlchemy create_all(); this
-- migration is for existing installations and is intentionally idempotent.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS docling_json_path VARCHAR(500);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS artifact_manifest_path VARCHAR(500);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS evidence_blocks_path VARCHAR(500);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS parser_name VARCHAR(100);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS parser_version VARCHAR(100);
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS coverage_summary JSONB;

ALTER TABLE IF EXISTS duplicate_results
    ADD COLUMN IF NOT EXISTS confidence NUMERIC(5,4);
ALTER TABLE IF EXISTS duplicate_results
    ADD COLUMN IF NOT EXISTS coverage_status VARCHAR(20) NOT NULL DEFAULT 'insufficient';
ALTER TABLE IF EXISTS duplicate_results
    ADD COLUMN IF NOT EXISTS channel_scores JSONB;

-- Rows created before S2-0 had no parser coverage.  If their linked
-- duplicate documents still have no coverage summary, do not let the new
-- column's historical default present them as complete.  The predicate is
-- safe to rerun and leaves newly generated, fully-artifacted results alone.
UPDATE duplicate_results AS result
   SET coverage_status = 'insufficient'
 WHERE result.coverage_status = 'complete'
   AND NOT EXISTS (
       SELECT 1
         FROM review_tasks AS task
         JOIN documents AS left_doc
           ON left_doc.project_id = task.project_id
          AND left_doc.doc_type = 'duplicate_left'
         JOIN documents AS right_doc
           ON right_doc.project_id = task.project_id
          AND right_doc.doc_type = 'duplicate_right'
        WHERE task.id = result.task_id
          AND left_doc.coverage_summary IS NOT NULL
          AND right_doc.coverage_summary IS NOT NULL
   );

ALTER TABLE IF EXISTS duplicate_results
    ALTER COLUMN coverage_status SET DEFAULT 'insufficient';

DO $$ BEGIN
    ALTER TABLE IF EXISTS duplicate_results ADD CONSTRAINT ck_duplicate_results_confidence
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE IF EXISTS duplicate_results ADD CONSTRAINT ck_duplicate_results_coverage_status
        CHECK (coverage_status IN ('complete', 'partial', 'insufficient'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    IF to_regclass('duplicate_results') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS ix_duplicate_results_coverage_status
            ON duplicate_results(coverage_status);
    END IF;
END $$;
