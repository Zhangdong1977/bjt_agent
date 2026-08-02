-- Migration 034: repair duplicate-check runtime permissions and usage-summary defaults.
--
-- 029 may be executed by a deployment owner different from the application
-- role.  Grant only the CRUD privileges required by the runtime to the known
-- application roles that exist in the target database.  The block is
-- idempotent and supports both the current ssirs_user name and the legacy
-- bjt_user production name.

DO $$
DECLARE
    app_role RECORD;
    table_name TEXT;
BEGIN
    FOR app_role IN
        SELECT rolname
        FROM pg_roles
        WHERE rolname IN ('ssirs_user', 'bjt_user')
    LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', app_role.rolname);
        FOREACH table_name IN ARRAY ARRAY[
            'duplicate_document_members',
            'duplicate_evidence_clusters',
            'duplicate_occurrences',
            'duplicate_pair_summaries'
        ]
        LOOP
            IF to_regclass('public.' || table_name) IS NOT NULL THEN
                EXECUTE format(
                    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO %I',
                    table_name,
                    app_role.rolname
                );
            END IF;
        END LOOP;

        -- Future tables created by the same migration owner inherit the same
        -- least-privilege CRUD grant instead of repeating the 029 failure.
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
            app_role.rolname
        );
    END LOOP;
END $$;

-- Older databases can have these NOT NULL columns without server defaults.
-- Set every counter default explicitly so status-only/zero-provider-call rows
-- are valid regardless of which insert path materializes the summary.
ALTER TABLE ai_usage_task_summary ALTER COLUMN llm_calls SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN ocr_calls SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN embedding_calls SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN vision_calls SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN failed_calls SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN prompt_tokens SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN completion_tokens SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN total_tokens SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN prompt_cache_hit_tokens SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN prompt_cache_miss_tokens SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN ocr_images SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN ocr_words_result_num SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN embedding_inputs SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN embedding_input_chars SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN embedding_input_tokens SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN embedding_cache_hits SET DEFAULT 0;
ALTER TABLE ai_usage_task_summary ALTER COLUMN vision_images SET DEFAULT 0;
