-- Migration 013: 把所有时间列从 timestamp(无时区) 转为 timestamptz(带时区)
-- 背景：后端历史用 datetime.utcnow() 写入 naive UTC，PG 列是 timestamp without time zone。
--       API 返回的时间字符串无时区后缀，前端 new Date() 当本地时区解析，导致 UTC+8 用户
--       看到的时间比真实本地时间晚 8 小时。现已把后端改为写入 aware UTC（now(timezone.utc)），
--       列也改为 timestamptz。本迁移负责把已存在的库对齐到 timestamptz。
--
-- 历史数据处理：USING <col> AT TIME ZONE 'UTC' —— 告诉 PG 现有 naive 值本来就是 UTC，
--       转换时按 UTC 打标记，不产生任何时间偏移。历史数据因此也保持正确。
--
-- 幂等：每个列用 DO 块判断，只有当列当前是 'timestamp without time zone' 时才转换；
--       已是 timestamptz 或列不存在则跳过，可安全重复执行。
--
-- 执行方式：docker exec bjt-postgres psql -U bjt_user -d bjt_db -f /path/to/013_*.sql
--           （或 -c 带入文件内容）。全新空库由 init_db() create_all 直接建 timestamptz，无需跑本文件。

-- 通用转换过程：入参为 表名、列名，仅当该列当前是 'timestamp without time zone' 时
-- 才 ALTER 成 timestamptz，USING AT TIME ZONE 'UTC' 保持数值不变。
CREATE OR REPLACE FUNCTION __mig013_to_timestamptz(_tbl text, _col text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    _dt text;
BEGIN
    SELECT data_type INTO _dt
      FROM information_schema.columns
     WHERE table_name = _tbl AND column_name = _col;
    IF _dt = 'timestamp without time zone' THEN
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN %I TYPE timestamptz USING %I AT TIME ZONE ''UTC''',
            _tbl, _col, _col
        );
    END IF;
END;
$$;

-- 1) 所有继承 Base 的表都有 created_at / updated_at（project_review_results 显式定义，其余同）
--    逐一调用；列不存在的表，information_schema 查不到 _dt，函数体不执行 ALTER，安全跳过。
SELECT __mig013_to_timestamptz('projects',                   'created_at');
SELECT __mig013_to_timestamptz('projects',                   'updated_at');
SELECT __mig013_to_timestamptz('documents',                  'created_at');
SELECT __mig013_to_timestamptz('documents',                  'updated_at');
SELECT __mig013_to_timestamptz('users',                      'created_at');
SELECT __mig013_to_timestamptz('users',                      'updated_at');
SELECT __mig013_to_timestamptz('review_tasks',               'created_at');
SELECT __mig013_to_timestamptz('review_tasks',               'updated_at');
SELECT __mig013_to_timestamptz('review_tasks',               'started_at');
SELECT __mig013_to_timestamptz('review_tasks',               'completed_at');
SELECT __mig013_to_timestamptz('review_tasks',               'last_heartbeat');
SELECT __mig013_to_timestamptz('review_results',             'created_at');
SELECT __mig013_to_timestamptz('review_results',             'updated_at');
SELECT __mig013_to_timestamptz('agent_steps',                'created_at');
SELECT __mig013_to_timestamptz('agent_steps',                'updated_at');
SELECT __mig013_to_timestamptz('todo_items',                 'created_at');
SELECT __mig013_to_timestamptz('todo_items',                 'updated_at');
SELECT __mig013_to_timestamptz('todo_items',                 'started_at');
SELECT __mig013_to_timestamptz('todo_items',                 'completed_at');
SELECT __mig013_to_timestamptz('review_sessions',            'created_at');
SELECT __mig013_to_timestamptz('review_sessions',            'updated_at');
SELECT __mig013_to_timestamptz('review_sessions',            'started_at');
SELECT __mig013_to_timestamptz('review_sessions',            'completed_at');
SELECT __mig013_to_timestamptz('project_review_results',     'created_at');
SELECT __mig013_to_timestamptz('project_review_results',     'updated_at');

-- 2) experience 相关表
SELECT __mig013_to_timestamptz('experience_feedback',              'created_at');
SELECT __mig013_to_timestamptz('experience_feedback',              'updated_at');
SELECT __mig013_to_timestamptz('experience_feedback',              'reviewed_at');
SELECT __mig013_to_timestamptz('experience_cases',                 'created_at');
SELECT __mig013_to_timestamptz('experience_cases',                 'updated_at');
SELECT __mig013_to_timestamptz('experience_skills',                'created_at');
SELECT __mig013_to_timestamptz('experience_skills',                'updated_at');
SELECT __mig013_to_timestamptz('experience_skills',                'last_promoted_at');  -- 已是 timestamptz，函数会自动跳过
SELECT __mig013_to_timestamptz('experience_skills',                'retired_at');
SELECT __mig013_to_timestamptz('experience_cluster_memberships',   'created_at');
SELECT __mig013_to_timestamptz('experience_cluster_memberships',   'updated_at');
SELECT __mig013_to_timestamptz('experience_cluster_memberships',   'assigned_at');

-- 3) ai_usage_records 只有 usage_date(Date 类型，无时区问题) + created_at/updated_at(继承 Base)
SELECT __mig013_to_timestamptz('ai_usage_records',           'created_at');
SELECT __mig013_to_timestamptz('ai_usage_records',           'updated_at');

-- 清理临时函数
DROP FUNCTION __mig013_to_timestamptz(text, text);
