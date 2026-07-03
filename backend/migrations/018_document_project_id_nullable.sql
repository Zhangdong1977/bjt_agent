-- Migration: Allow documents to exist without a project (draft / standalone documents)
--
-- 用户在标书检查页面选择文件时立即上传并解析，此时项目尚未创建。
-- 让 documents.project_id 可空，并新增 owner_user_id 标记独立文档归属。
-- 点「开始检查」创建项目后，再通过 attach 接口把这些草稿文档关联到项目。
-- 解析产物用绝对路径存库，关联时不移动文件，仅更新 project_id。

ALTER TABLE documents ALTER COLUMN project_id DROP NOT NULL;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_documents_owner_user_id ON documents(owner_user_id);

-- 部分索引：加速「当前用户的草稿文档」查询（project_id IS NULL）
CREATE INDEX IF NOT EXISTS ix_documents_drafts_per_user
  ON documents(owner_user_id)
  WHERE project_id IS NULL;
