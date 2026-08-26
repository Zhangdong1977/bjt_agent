-- Migration 038: dedicated project_type for bid-draft generation projects.
-- 标书生成上线（037）时项目复用 project_type='review' 落库，导致历史标书列表把
-- 生成项目当"标书检查"展示。038 起 projects.project_type 新增 'bid_draft'：
-- 生成页只建/只选 bid_draft 项目；网站历史/我的项目列表不再展示生成项目。
-- 本迁移把已有生成任务引用的存量项目回填为 'bid_draft'，并同步放宽 022 引入的
-- ck_projects_project_type 约束。幂等，可重复执行。PostgreSQL 10 compatible。

ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_project_type;

UPDATE projects
SET project_type = 'bid_draft'
WHERE project_type = 'review'
  AND id IN (SELECT DISTINCT project_id FROM bid_draft_tasks);

DO $$ BEGIN
    ALTER TABLE projects ADD CONSTRAINT ck_projects_project_type
        CHECK (project_type IN ('review', 'duplicate', 'bid_draft'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
