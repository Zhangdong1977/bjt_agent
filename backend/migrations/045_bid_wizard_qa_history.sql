-- 045：bid_wizards 补 qa_history（幂等，可重复执行）
-- 背景（2026-09-14 第三轮 grilling 决策 43）：AI编标需求确认阶段「额外的需求」卡改聊天式布局，
-- 用户自由提问与 AI 回答的问答历史此前仅前端内存态（刷新即丢），改为落库：
--   list[{question, answer, created_at, adopted}]，时序正序、上限 50 条丢最旧，
--   「采纳并入需求」时同步置 adopted=true，GET /wizards/{id} 回填。
-- 列类型沿用 040 同表其余 JSON 列（analysis/questionnaire/requirements/spec）的 JSON。
-- 同族教训（023/040/041/043/044）：新代码引用的每一列必须有对应迁移，
-- 预发布 create_all 建出的列不代表生产 schema 就绪。
ALTER TABLE bid_wizards ADD COLUMN IF NOT EXISTS qa_history JSON NULL;
