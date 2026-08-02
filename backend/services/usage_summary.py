"""任务级用量汇总刷新 — 把一个 ReviewTask 的全部 LLM/OCR 流水聚合成一行 ai_usage_task_summary。

触发点：
  1. usage_recorder._write_one 落库一条流水后 fire-and-forget 调用（覆盖运行中增量）。
  2. tasks/review_tasks.py 任务终态（completed/failed/cancelled）commit 后 await 调用（覆盖终态 status/时长）。

设计：fire-and-forget 风格，任何异常吞掉只 warning，绝不影响审查主流程。
并发安全：用 INSERT ... ON CONFLICT DO UPDATE（绝对值覆盖，非增量累加），重复刷新幂等。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from backend.models import async_session_factory

logger = logging.getLogger(__name__)

# 聚合 ai_usage_records 的 upsert：一行 SQL 重算该 task 的全部用量指标。
# 用 PG 的 COUNT(*) FILTER / SUM(...) FILTER 按 usage_type 拆分，COALESCE 防 NULL。
# ON CONFLICT (id) DO UPDATE —— 主键 id = task_id，冲突即覆盖（绝对值，幂等）。
# 注意：
#   - id 列值用 MIN(task_id) 而非 :task_id：:task_id 只在 WHERE 出现一次，
#     否则 asyncpg 对同一参数在 INSERT 值与 WHERE 两种上下文推断类型冲突
#     （AmbiguousParameterError: inconsistent types deduced）。
#   - created_at 在 ON CONFLICT 时保留原值（首次创建时间），不覆盖。
_UPSERT_SQL = text("""
INSERT INTO ai_usage_task_summary (
    id, llm_calls, ocr_calls, embedding_calls, vision_calls, failed_calls,
    prompt_tokens, completion_tokens, total_tokens,
    prompt_cache_hit_tokens, prompt_cache_miss_tokens,
    ocr_images, ocr_words_result_num,
    embedding_inputs, embedding_input_chars, embedding_input_tokens,
    embedding_cache_hits, vision_images,
    cost_cny,
    external_user_id, local_user_id, user_name, enterprise_name,
    interior_user, project_id,
    first_usage_at, last_usage_at,
    created_at, updated_at
)
SELECT
    MIN(task_id),
    COUNT(*) FILTER (WHERE usage_type = 'llm'),
    COUNT(*) FILTER (WHERE usage_type = 'ocr'),
    COALESCE(SUM(embedding_calls) FILTER (WHERE usage_type = 'embedding'), 0),
    COALESCE(SUM(vision_calls) FILTER (WHERE usage_type = 'vision'), 0),
    COUNT(*) FILTER (WHERE status <> 'success'),
    COALESCE(SUM(prompt_tokens)  FILTER (WHERE usage_type = 'llm'), 0),
    COALESCE(SUM(completion_tokens) FILTER (WHERE usage_type = 'llm'), 0),
    COALESCE(SUM(total_tokens)   FILTER (WHERE usage_type = 'llm'),  0),
    COALESCE(SUM(prompt_cache_hit_tokens),  0),
    COALESCE(SUM(prompt_cache_miss_tokens), 0),
    COALESCE(SUM(ocr_images)         FILTER (WHERE usage_type = 'ocr'), 0),
    COALESCE(SUM(ocr_words_result_num) FILTER (WHERE usage_type = 'ocr'), 0),
    COALESCE(SUM(embedding_inputs) FILTER (WHERE usage_type = 'embedding'), 0),
    COALESCE(SUM(embedding_input_chars) FILTER (WHERE usage_type = 'embedding'), 0),
    COALESCE(SUM(embedding_input_tokens) FILTER (WHERE usage_type = 'embedding'), 0),
    COALESCE(SUM(embedding_cache_hits) FILTER (WHERE usage_type = 'embedding'), 0),
    COALESCE(SUM(vision_images) FILTER (WHERE usage_type = 'vision'), 0),
    COALESCE(SUM(cost_cny), 0),
    -- 归属维度：一个 task 的所有流水归属应一致，取任意非空值
    MAX(external_user_id),
    MAX(local_user_id),
    MAX(user_name),
    MAX(enterprise_name),
    -- PG 无 max(boolean) 聚合，用 bool_or（任一为 true 则 true）
    bool_or(interior_user),
    MAX(project_id),
    MIN(created_at),
    MAX(created_at),
    now(),
    now()
FROM ai_usage_records
WHERE task_id = :task_id
HAVING COUNT(*) > 0
ON CONFLICT (id) DO UPDATE SET
    llm_calls              = EXCLUDED.llm_calls,
    ocr_calls              = EXCLUDED.ocr_calls,
    embedding_calls        = EXCLUDED.embedding_calls,
    vision_calls           = EXCLUDED.vision_calls,
    failed_calls           = EXCLUDED.failed_calls,
    prompt_tokens          = EXCLUDED.prompt_tokens,
    completion_tokens      = EXCLUDED.completion_tokens,
    total_tokens           = EXCLUDED.total_tokens,
    prompt_cache_hit_tokens   = EXCLUDED.prompt_cache_hit_tokens,
    prompt_cache_miss_tokens  = EXCLUDED.prompt_cache_miss_tokens,
    ocr_images             = EXCLUDED.ocr_images,
    ocr_words_result_num   = EXCLUDED.ocr_words_result_num,
    embedding_inputs       = EXCLUDED.embedding_inputs,
    embedding_input_chars  = EXCLUDED.embedding_input_chars,
    embedding_input_tokens = EXCLUDED.embedding_input_tokens,
    embedding_cache_hits   = EXCLUDED.embedding_cache_hits,
    vision_images          = EXCLUDED.vision_images,
    cost_cny               = EXCLUDED.cost_cny,
    external_user_id       = EXCLUDED.external_user_id,
    local_user_id          = EXCLUDED.local_user_id,
    user_name              = EXCLUDED.user_name,
    enterprise_name        = EXCLUDED.enterprise_name,
    interior_user          = EXCLUDED.interior_user,
    project_id             = EXCLUDED.project_id,
    first_usage_at         = EXCLUDED.first_usage_at,
    last_usage_at          = EXCLUDED.last_usage_at,
    updated_at             = now()
""")

# 合并 review_tasks 的状态/时长维度。LEFT JOIN 保证 task 不存在时不清空（r.id IS NULL 时跳过）。
# 这样即使 ai_usage_records 有 task_id 但 review_tasks 已被级联删除，也不会误覆盖为 NULL。
_MERGE_STATUS_SQL = text("""
UPDATE ai_usage_task_summary s
SET
    task_status      = r.status,
    started_at       = r.started_at,
    completed_at     = r.completed_at,
    duration_seconds = r.duration_seconds,
    error_message    = r.error_message,
    updated_at       = now()
FROM review_tasks r
WHERE r.id = s.id AND s.id = :task_id
""")

_MERGE_BLIND_STATUS_SQL = text("""
UPDATE ai_usage_task_summary s
SET
    task_status      = b.status,
    started_at       = b.started_at,
    completed_at     = b.completed_at,
    duration_seconds = CASE
        WHEN b.started_at IS NOT NULL AND b.completed_at IS NOT NULL
        THEN EXTRACT(EPOCH FROM (b.completed_at - b.started_at))::INTEGER
        ELSE NULL
    END,
    error_message    = b.error_message,
    updated_at       = now()
FROM blind_check_tasks b
WHERE b.id = s.id AND s.id = :task_id
""")

_INSERT_EMPTY_REVIEW_SQL = text("""
INSERT INTO ai_usage_task_summary (
    id, task_status, started_at, completed_at, duration_seconds, error_message,
    external_user_id, local_user_id, user_name, enterprise_name, interior_user,
    project_id,
    llm_calls, ocr_calls, embedding_calls, vision_calls, failed_calls,
    prompt_tokens, completion_tokens, total_tokens,
    prompt_cache_hit_tokens, prompt_cache_miss_tokens,
    ocr_images, ocr_words_result_num,
    embedding_inputs, embedding_input_chars, embedding_input_tokens,
    embedding_cache_hits, vision_images,
    cost_cny, created_at, updated_at
)
SELECT
    r.id, r.status, r.started_at, r.completed_at, r.duration_seconds, r.error_message,
    u.external_user_id, u.id, u.username, u.enterprise_name, u.interior_user,
    p.id,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0,
    0, now(), now()
FROM review_tasks r
JOIN projects p ON p.id = r.project_id
LEFT JOIN users u ON u.id = p.user_id
WHERE r.id = :task_id
ON CONFLICT (id) DO NOTHING
""")

_INSERT_EMPTY_BLIND_SQL = text("""
INSERT INTO ai_usage_task_summary (
    id, task_status, started_at, completed_at, duration_seconds, error_message,
    external_user_id, local_user_id, user_name, enterprise_name, interior_user,
    project_id,
    llm_calls, ocr_calls, embedding_calls, vision_calls, failed_calls,
    prompt_tokens, completion_tokens, total_tokens,
    prompt_cache_hit_tokens, prompt_cache_miss_tokens,
    ocr_images, ocr_words_result_num,
    embedding_inputs, embedding_input_chars, embedding_input_tokens,
    embedding_cache_hits, vision_images,
    cost_cny, created_at, updated_at
)
SELECT
    b.id, b.status, b.started_at, b.completed_at,
    CASE WHEN b.started_at IS NOT NULL AND b.completed_at IS NOT NULL
         THEN EXTRACT(EPOCH FROM (b.completed_at - b.started_at))::INTEGER ELSE NULL END,
    b.error_message, u.external_user_id, u.id, u.username, u.enterprise_name,
    u.interior_user, NULL,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0,
    0, now(), now()
FROM blind_check_tasks b
LEFT JOIN users u ON u.id = b.user_id
WHERE b.id = :task_id
ON CONFLICT (id) DO NOTHING
""")


async def refresh_task_summary(task_id: str, *, strict: bool = False) -> None:
    """重算并 upsert 指定 task 的用量汇总行。

    fire-and-forget 风格：异常吞掉只 warning。调用方可在 fire-and-forget（_spawn）
    或终态 commit 后 await 调用。
    """
    if not task_id:
        return
    try:
        async with async_session_factory() as db:
            await db.execute(_UPSERT_SQL, {"task_id": task_id})
            # Aggregate SELECT has no usable id when a task made zero provider
            # calls. Materialize an explicit zero-cost audit row from either
            # supported task table instead.
            await db.execute(_INSERT_EMPTY_REVIEW_SQL, {"task_id": task_id})
            await db.execute(_INSERT_EMPTY_BLIND_SQL, {"task_id": task_id})
            await db.execute(_MERGE_STATUS_SQL, {"task_id": task_id})
            await db.execute(_MERGE_BLIND_STATUS_SQL, {"task_id": task_id})
            await db.commit()
    except Exception as e:
        logger.warning(f"[usage-summary] refresh failed for task {task_id}: {e}")
        if strict:
            raise
