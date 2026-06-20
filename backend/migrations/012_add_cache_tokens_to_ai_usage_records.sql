-- Migration 012: 给 ai_usage_records 增加 DeepSeek 上下文缓存拆分列
-- 背景：deepseek-v4-flash 命中缓存输入单价是未命中的 1/50（0.02 vs 1.0 元/百万 token），
--       需要分开记录 prompt_cache_hit_tokens / prompt_cache_miss_tokens 才能按官方费率精确计费。
-- 全新库由 init_db() create_all 自动建列；已存在的库需手动跑本文件。
-- 幂等：ADD COLUMN IF NOT EXISTS（PostgreSQL 9.6+）。

ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS prompt_cache_hit_tokens  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_records
    ADD COLUMN IF NOT EXISTS prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0;

-- 便于按缓存命中率排查的复合索引（可选；命中率高时命中率分析很有用）
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_cache_tokens
    ON ai_usage_records(prompt_cache_hit_tokens, prompt_cache_miss_tokens);
