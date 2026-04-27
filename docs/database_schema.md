# 数据库表结构文档

## 概述

- 数据库: PostgreSQL
- ORM: SQLAlchemy (async)
- 基础类: 所有表继承 `Base` 类，包含公共字段:
  - `id`: UUID 主键
  - `created_at`: 创建时间
  - `updated_at`: 更新时间

---

## 表清单

| 表名 | 说明 |
|------|------|
| [users](#users) | 用户账户 |
| [projects](#projects) | 招标项目 |
| [documents](#documents) | 招标/投标文档 |
| [review_tasks](#review_tasks) | 审查任务 |
| [review_results](#review_results) | 单次审查结果 |
| [project_review_results](#project_review_results) | 项目级合并审查结果 |
| [agent_steps](#agent_steps) | Agent执行步骤 |
| [todo_items](#todo_items) | 待办任务 |
| [review_sessions](#review_sessions) | 审查会话 |

---

## 详细表结构

### users

用户账户表。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| username | VARCHAR(100) | UNIQUE, NOT NULL, INDEX | 用户名 |
| email | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | 邮箱 |
| password_hash | VARCHAR(255) | NOT NULL | 密码哈希 |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

**关系:**
- 1:N → `projects`

---

### projects

招标项目表。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| user_id | VARCHAR(36) | FK → users.id, NOT NULL, INDEX | 所属用户ID |
| name | VARCHAR(255) | NOT NULL | 项目名称 |
| description | TEXT | NULLABLE | 项目描述 |
| status | VARCHAR(50) | DEFAULT 'draft', INDEX | 状态 (draft/...) |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

**关系:**
- N:1 → `users`
- 1:N → `documents`
- 1:N → `review_tasks`
- 1:N → `project_review_results`

---

### documents

招标/投标文档表。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| project_id | VARCHAR(36) | FK → projects.id, NOT NULL, INDEX | 所属项目ID |
| doc_type | VARCHAR(50) | NOT NULL | 文档类型 (tender/bid) |
| original_filename | VARCHAR(255) | NOT NULL | 原始文件名 |
| file_path | VARCHAR(500) | NOT NULL | 文件存储路径 |
| parsed_html_path | VARCHAR(500) | NULLABLE | 解析后HTML路径 |
| parsed_markdown_path | VARCHAR(500) | NULLABLE | 解析后Markdown路径 |
| parsed_images_dir | VARCHAR(500) | NULLABLE | 解析后图片目录 |
| page_count | INTEGER | NULLABLE | 页数 |
| word_count | INTEGER | NULLABLE | 字数 |
| status | VARCHAR(50) | DEFAULT 'pending', INDEX | 解析状态 (pending/parsing/parsed/failed) |
| parse_error | TEXT | NULLABLE | 解析错误信息 |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

**关系:**
- N:1 → `projects`

---

### review_tasks

审查任务表 (Celery异步任务)。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| project_id | VARCHAR(36) | FK → projects.id, NOT NULL, INDEX | 所属项目ID |
| status | VARCHAR(50) | DEFAULT 'pending', INDEX | 任务状态 (pending/running/completed/failed/cancelled) |
| celery_task_id | VARCHAR(255) | NULLABLE, INDEX | Celery任务ID |
| started_at | DATETIME | NULLABLE | 开始时间 |
| completed_at | DATETIME | NULLABLE | 完成时间 |
| error_message | TEXT | NULLABLE | 错误信息 |
| last_heartbeat | DATETIME | NULLABLE, INDEX | 前端心跳时间 (20秒无心跳则取消) |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

**关系:**
- N:1 → `projects`
- 1:N → `review_results`
- 1:N → `agent_steps`

---

### review_results

单次审查结果表 (每次审查任务的发现项)。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| task_id | VARCHAR(36) | FK → review_tasks.id, NOT NULL, INDEX | 所属任务ID |
| requirement_key | VARCHAR(255) | NOT NULL | 需求标识 |
| requirement_content | TEXT | NOT NULL | 需求内容 |
| bid_content | TEXT | NULLABLE | 投标文件对应内容 |
| is_compliant | BOOLEAN | DEFAULT False | 是否合规 |
| severity | VARCHAR(50) | NOT NULL | 严重程度 (critical/major/minor) |
| location_page | INTEGER | NULLABLE | 所在页码 |
| location_line | INTEGER | NULLABLE | 所在行号 |
| suggestion | TEXT | NULLABLE | 修改建议 |
| explanation | TEXT | NULLABLE | 解释说明 |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

**关系:**
- N:1 → `review_tasks`

---

### project_review_results

项目级合并审查结果表 (跨任务去重合并后的结果)。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| project_id | VARCHAR(36) | FK → projects.id, NOT NULL, INDEX | 所属项目ID |
| requirement_key | VARCHAR(255) | NOT NULL | 需求标识 |
| requirement_content | TEXT | NOT NULL | 需求内容 |
| bid_content | TEXT | NULLABLE | 投标文件对应内容 |
| is_compliant | BOOLEAN | DEFAULT False | 是否合规 |
| severity | VARCHAR(50) | NOT NULL | 严重程度 (critical/major/minor) |
| location_page | INTEGER | NULLABLE | 所在页码 |
| location_line | INTEGER | NULLABLE | 所在行号 |
| suggestion | TEXT | NULLABLE | 修改建议 |
| explanation | TEXT | NULLABLE | 解释说明 |
| source_task_id | VARCHAR(36) | FK → review_tasks.id, NOT NULL | 来源任务ID |
| merged_from_count | INTEGER | DEFAULT 1 | 合并自多少条记录 |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

**关系:**
- N:1 → `projects`

---

### agent_steps

Agent执行步骤表 (用于时间线展示)。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| task_id | VARCHAR(36) | FK → review_tasks.id, NULLABLE, INDEX | 所属任务ID |
| todo_id | VARCHAR(36) | FK → todo_items.id, NULLABLE, INDEX | 关联待办ID |
| step_number | INTEGER | NOT NULL | 步骤编号 |
| step_type | VARCHAR(50) | NOT NULL | 步骤类型 (thought/tool_call/tool_result/final) |
| content | TEXT | NOT NULL | 步骤内容 |
| tool_name | VARCHAR(100) | NULLABLE | 工具名称 |
| tool_args | JSONB | NULLABLE | 工具参数 |
| tool_result | JSONB | NULLABLE | 工具执行结果 |
| duration_ms | INTEGER | NULLABLE | 执行耗时(毫秒) |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

**关系:**
- N:1 → `review_tasks`

---

### todo_items

待办任务表 (对应规则文档的检查任务)。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| project_id | VARCHAR(36) | INDEX | 项目ID |
| session_id | VARCHAR(36) | INDEX | 审查会话ID |
| rule_doc_path | VARCHAR(500) | NOT NULL | 规则文档路径 |
| rule_doc_name | VARCHAR(255) | NOT NULL | 规则文档名称 |
| check_items | JSON | NULLABLE | 检查项列表 |
| status | VARCHAR(20) | DEFAULT 'pending', INDEX | 状态 (pending/running/completed/failed) |
| result | JSON | NULLABLE | 执行结果 |
| error_message | TEXT | NULLABLE | 错误信息 |
| retry_count | INTEGER | DEFAULT 0 | 重试次数 |
| max_retries | INTEGER | DEFAULT 3 | 最大重试次数 |
| started_at | DATETIME | NULLABLE | 开始时间 |
| completed_at | DATETIME | NULLABLE | 完成时间 |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

---

### review_sessions

审查会话表 (关联项目与规则库的检查任务批次)。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID主键 |
| project_id | VARCHAR(36) | INDEX | 项目ID |
| rule_library_path | VARCHAR(500) | NOT NULL | 规则库路径 |
| tender_doc_path | VARCHAR(500) | NOT NULL | 招标书路径 |
| bid_doc_path | VARCHAR(500) | NOT NULL | 投标书路径 |
| status | VARCHAR(20) | DEFAULT 'pending', INDEX | 会话状态 |
| merged_result | JSON | NULLABLE | 合并后的审查结果 |
| total_todos | INTEGER | DEFAULT 0 | 总待办数 |
| completed_todos | INTEGER | DEFAULT 0 | 已完成待办数 |
| started_at | DATETIME | NULLABLE | 开始时间 |
| completed_at | DATETIME | NULLABLE | 完成时间 |
| created_at | DATETIME | NOT NULL | 创建时间 |
| updated_at | DATETIME | NOT NULL | 更新时间 |

---

## ER关系图

```
users (1) ──────< projects (N)
                    │
                    ├────< documents (N)
                    ├────< review_tasks (N)
                    │         │
                    │         ├────< review_results (N)
                    │         └────< agent_steps (N)
                    │
                    └────< project_review_results (N)

review_sessions (1) ──────< todo_items (N)
```

---

## 索引汇总

| 表名 | 索引字段 |
|------|----------|
| users | username, email |
| projects | user_id, status |
| documents | project_id, status |
| review_tasks | project_id, status, celery_task_id, last_heartbeat |
| review_results | task_id |
| project_review_results | project_id |
| agent_steps | task_id, todo_id |
| todo_items | project_id, session_id, status |
| review_sessions | project_id, status |
