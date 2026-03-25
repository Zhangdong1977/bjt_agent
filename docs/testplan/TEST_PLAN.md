# 标书审查智能体系统 - 完整测试计划

## 1. 概述

### 1.1 测试目标
本测试计划旨在为"标书审查智能体系统"提供全面的测试覆盖，确保系统满足需求文档中定义的所有功能和质量要求。

### 1.2 测试范围
- **后端 API**: FastAPI + Celery 异步任务
- **前端应用**: Vue3 单页应用
- **Agent 智能体**: Mini-Agent 扩展的标书审查 Agent
- **外部服务集成**: rag_memory_service, MiniMax LLM API

### 1.3 测试环境
```
PostgreSQL: 183.66.37.186:7004 (bjt_agent)
Redis: 183.66.37.186:7005
Backend API: http://localhost:8000
Frontend: http://localhost:3000
RAG Service: http://localhost:8001
Mini-Agent API: https://api.minimaxi.com
```

---

## 2. 测试类型

### 2.1 单元测试 (Unit Tests)

#### 2.1.1 后端单元测试

| 模块 | 测试类/函数 | 测试内容 |
|------|-----------|---------|
| **Models** | `test_user_model` | 用户创建、密码哈希、验证 |
| | `test_project_model` | 项目创建、状态流转 |
| | `test_document_model` | 文档创建、状态标记 |
| | `test_review_task_model` | 任务创建、状态枚举 |
| | `test_review_result_model` | 结果数据结构 |
| | `test_agent_step_model` | 步骤记录 |
| **Services** | `test_auth_service` | Token 生成与验证 |
| | `test_sse_service` | SSE 连接管理、Redis 订阅 |
| **Agent Tools** | `test_doc_search_tool` | 文档搜索、关键词过滤 |
| | `test_rag_search_tool` | RAG 服务调用、错误处理 |
| | `test_comparator_tool` | LLM 比对、JSON 解析 |
| **Tasks** | `test_document_parser` | PDF/DOCX 解析、图像提取 |
| | `test_review_tasks` | 任务状态流转、错误处理 |
| **API Deps** | `test_deps` | 依赖注入、认证中间件 |

#### 2.1.2 前端单元测试

| 模块 | 测试内容 |
|------|---------|
| **Stores** | Pinia stores 状态管理 |
| **Components** | 组件渲染、props 传递 |
| **Utils** | 工具函数 |

### 2.2 集成测试 (Integration Tests)

#### 2.2.1 API 集成测试

| 端点 | 测试场景 |
|------|---------|
| `POST /api/auth/register` | 用户注册成功、用户名重复、邮箱重复、密码验证 |
| `POST /api/auth/login` | 登录成功、用户不存在、密码错误 |
| `POST /api/auth/refresh` | Token 刷新成功、过期 refresh token |
| `GET /api/auth/me` | 获取当前用户信息 |
| `GET /api/projects` | 列出用户项目、权限验证 |
| `POST /api/projects` | 创建项目、参数验证 |
| `GET /api/projects/{id}` | 获取项目详情 |
| `PUT /api/projects/{id}` | 更新项目 |
| `DELETE /api/projects/{id}` | 删除项目 |
| `GET /api/projects/{id}/documents` | 列出项目文档 |
| `POST /api/projects/{id}/documents` | 上传文档 (PDF/DOCX)、参数验证 |
| `GET /api/documents/{id}` | 获取文档详情 |
| `GET /api/documents/{id}/content` | 获取解析内容 |
| `DELETE /api/documents/{id}` | 删除文档 |
| `POST /api/projects/{id}/review` | 启动审查、重复启动拦截 |
| `GET /api/projects/{id}/review` | 获取审查结果 |
| `GET /api/projects/{id}/review/tasks/{task_id}` | 获取任务状态 |
| `POST /api/projects/{id}/review/tasks/{task_id}/cancel` | 取消任务 |
| `GET /api/projects/{id}/review/tasks/{task_id}/steps` | 获取执行步骤 |
| `GET /api/projects/{id}/review/tasks/{task_id}/results` | 获取不符合项清单 |

#### 2.2.2 SSE 事件流测试

| 事件类型 | 测试验证点 |
|---------|-----------|
| `step_started` | 事件格式、step_number 递增 |
| `step_progress` | 进度消息正确 |
| `step_completed` | 完成状态标记 |
| `finding` | 发现项事件推送 |
| `task_completed` | 任务完成汇总 |
| `error` | 错误事件处理 |

#### 2.2.3 Celery 任务集成测试

| 任务 | 测试场景 |
|------|---------|
| `parse_document` | PDF 解析、DOCX 解析、图像提取、LLM 图像理解 |
| `run_review` | Agent 执行、结果存储、错误处理 |

### 2.3 E2E 测试 (End-to-End Tests)

#### 2.3.1 关键用户流程

```
Flow 1: 用户注册与登录
1. 用户注册 → 2. 用户登录 → 3. 获取当前用户信息 → 4. Token 刷新

Flow 2: 完整审查流程
1. 创建项目 → 2. 上传招标书 → 3. 上传应标书 → 4. 等待解析完成 →
5. 启动审查 → 6. SSE 实时跟踪 → 7. 获取审查结果 → 8. 查看不符合项

Flow 3: 文档生命周期
1. 上传文档 → 2. 检查解析状态 → 3. 查看解析内容 → 4. 删除文档

Flow 4: 任务管理
1. 启动审查 → 2. 检查任务状态 → 3. 取消任务 → 4. 重新启动审查
```

---

## 3. 功能测试用例

### 3.1 认证模块

| TC ID | 用例名称 | 前置条件 | 测试步骤 | 预期结果 |
|-------|---------|---------|---------|---------|
| AUTH-001 | 用户注册成功 | 无 | 提交有效的用户名、邮箱、密码 | 返回用户信息，状态码 201 |
| AUTH-002 | 用户名重复 | 用户已存在 | 使用已存在的用户名注册 | 返回 400，错误信息"Username already registered" |
| AUTH-003 | 邮箱重复 | 用户已存在 | 使用已存在的邮箱注册 | 返回 400，错误信息"Email already registered" |
| AUTH-004 | 密码过短 | 无 | 提交少于 8 位的密码 | 返回 422，密码验证失败 |
| AUTH-005 | 登录成功 | 用户已注册 | 提交正确的用户名和密码 | 返回 access_token 和 refresh_token |
| AUTH-006 | 登录密码错误 | 用户已注册 | 提交错误的密码 | 返回 401，错误信息"Incorrect email or password" |
| AUTH-007 | Token 刷新 | 持有有效 refresh_token | 提交 refresh_token | 返回新的 access_token 和 refresh_token |
| AUTH-008 | 获取当前用户 | 持有有效 access_token | 请求 /api/auth/me | 返回当前用户信息 |

### 3.2 项目管理模块

| TC ID | 用例名称 | 测试步骤 | 预期结果 |
|-------|---------|---------|---------|
| PROJ-001 | 创建项目 | POST /api/projects with name, description | 返回项目信息，状态码 201 |
| PROJ-002 | 列出项目 | GET /api/projects | 返回当前用户的所有项目列表 |
| PROJ-003 | 获取项目详情 | GET /api/projects/{id} | 返回项目详情，包括文档列表 |
| PROJ-004 | 更新项目 | PUT /api/projects/{id} | 返回更新后的项目信息 |
| PROJ-005 | 删除项目 | DELETE /api/projects/{id} | 返回 204，项目及其文档被删除 |
| PROJ-006 | 跨用户访问拒绝 | 用户 B 访问用户 A 的项目 | 返回 404 |

### 3.3 文档管理模块

| TC ID | 用例名称 | 测试步骤 | 预期结果 |
|-------|---------|---------|---------|
| DOC-001 | 上传 PDF 招标书 | POST 上传 .pdf 文件 | 返回文档信息，状态 "pending" |
| DOC-002 | 上传 DOCX 应标书 | POST 上传 .docx 文件 | 返回文档信息，状态 "pending" |
| DOC-003 | 上传不支持格式 | POST 上传 .txt 文件 | 返回 400，"Unsupported file type" |
| DOC-004 | 文档解析完成 | 等待 Celery 任务完成 | 文档状态变为 "parsed" |
| DOC-005 | 获取文档内容 | GET /api/documents/{id}/content | 返回 md_content 和 images 列表 |
| DOC-006 | 内容未解析时获取 | 文档状态不是 "parsed" | 返回 400，"Document is not parsed yet" |
| DOC-007 | 删除文档 | DELETE /api/documents/{id} | 返回 204，物理文件被删除 |

### 3.4 审查模块

| TC ID | 用例名称 | 测试步骤 | 预期结果 |
|-------|---------|---------|---------|
| REV-001 | 启动审查 | 两个文档都解析完成后 POST | 返回任务信息，状态 "pending" |
| REV-002 | 重复启动拦截 | 已有运行中任务时启动 | 返回 400，"A review task is already running" |
| REV-003 | 缺少文档启动 | 未上传任一文档时启动 | 提示文档未准备好 |
| REV-004 | 获取审查结果 | GET /api/projects/{id}/review | 返回汇总和 findings 列表 |
| REV-005 | 取消任务 | POST /api/review-tasks/{id}/cancel | 任务状态变为 "cancelled" |
| REV-006 | 获取任务状态 | GET /api/review-tasks/{id} | 返回任务详情 |
| REV-007 | 获取执行步骤 | GET /api/review-tasks/{id}/steps | 返回步骤列表 |
| REV-008 | 获取不符合项 | GET /api/review-tasks/{id}/results | 返回 findings 列表，按严重程度排序 |

### 3.5 SSE 事件模块

| TC ID | 用例名称 | 测试步骤 | 预期结果 |
|-------|---------|---------|---------|
| SSE-001 | 连接事件流 | GET /api/events/tasks/{id}/stream | 建立 SSE 连接 |
| SSE-002 | 接收步骤事件 | 启动审查后 | 收到 step 事件，包含 step_number |
| SSE-003 | 接收完成事件 | 审查完成后 | 收到 complete 事件 |
| SSE-004 | 接收错误事件 | 审查失败时 | 收到 error 事件 |
| SSE-005 | 连接断开 | SSE 连接超时或手动关闭 | 连接正常关闭 |

### 3.6 Agent 工具模块

| TC ID | 用例名称 | 测试输入 | 预期输出 |
|-------|---------|---------|---------|
| TOOL-001 | DocSearchTool 招标书搜索 | doc_type="tender", query="资质" | 返回包含"资质"的招标书内容 |
| TOOL-002 | DocSearchTool 应标书搜索 | doc_type="bid", query="证书" | 返回包含"证书"的应标书内容 |
| TOOL-003 | DocSearchTool 文档不存在 | doc_type="tender" (路径错误) | 返回错误: "Document not found" |
| TOOL-004 | RAGSearchTool 知识库查询 | query="质量管理体系" | 返回知识库相关内容 |
| TOOL-005 | RAGSearchTool 服务不可用 | RAG 服务宕机 | 返回错误: "Could not connect to RAG memory service" |
| TOOL-006 | ComparatorTool 合规比对 | requirement, bid_content 完整 | 返回 is_compliant=true 及分析 |
| TOOL-007 | ComparatorTool 不合规 | requirement 存在, bid_content="N/A" | 返回 is_compliant=false, severity="critical" |
| TOOL-008 | ComparatorTool LLM 失败 | LLM API 错误 | 返回降级结果和错误信息 |

### 3.7 Celery 任务模块

| TC ID | 用例名称 | 测试场景 | 预期结果 |
|-------|---------|---------|---------|
| TASK-001 | PDF 解析 | 上传 10 页 PDF | 生成 _parsed.md，提取文本和图像 |
| TASK-002 | DOCX 解析 | 上传 DOCX 文件 | 生成 _parsed.md，提取文本和图像 |
| TASK-003 | 图像 LLM 理解 | PDF 包含 3 张图片 | 调用 MiniMax API，生成图像描述 |
| TASK-004 | 审查任务执行 | 两个文档都解析完成 | Agent 执行，产生 findings |
| TASK-005 | 任务失败处理 | 文档路径不存在 | 任务状态 "failed"，错误信息记录 |
| TASK-006 | 并发控制 | 同时启动 5 个审查 | 只有 4 个并发执行 |

---

## 4. 非功能测试

### 4.1 性能测试

| 指标 | 目标值 | 测试方法 |
|------|-------|---------|
| API 响应时间 (P95) | < 500ms | 使用 k6 进行负载测试 |
| 并发用户数 | 支持 50 并发 | 使用 k6 模拟 50 用户 |
| 文件上传 (10MB PDF) | < 30s | 计时上传和解析 |
| SSE 延迟 | < 1s | 事件发送到前端显示的时间 |

### 4.2 安全测试

| 测试项 | 测试方法 | 预期结果 |
|-------|---------|---------|
| SQL 注入 | 在所有输入字段尝试 SQL 注入 | 请求被拒绝或转义 |
| XSS 攻击 | 在文档名和内容中注入脚本 | 脚本不被执行 |
| 未授权访问 | 使用他人的 token 访问 | 返回 401/403 |
| 文件上传安全 | 上传恶意文件 | 被安全检查拦截 |
| Token 安全 | 检查 JWT 签名验证 | 伪造 token 被拒绝 |

### 4.3 兼容性测试

| 环境 | 浏览器/客户端 |
|------|-------------|
| Windows 10 | Chrome, Firefox, Edge |
| Windows 11 | Chrome, Firefox, Edge |
| Linux | Chrome, Firefox |

---

## 5. 测试数据

### 5.1 测试用户

| 用户名 | 邮箱 | 密码 | 角色 |
|-------|------|------|-----|
| test_user1 | test1@example.com | Test123! | 普通用户 |
| test_user2 | test2@example.com | Test123! | 普通用户 |

### 5.2 测试文档

| 文档类型 | 文件名 | 大小 | 页数 |
|---------|-------|------|-----|
| 招标书 | tender_sample.pdf | 2MB | 10 页 |
| 应标书 | bid_sample.pdf | 1.5MB | 8 页 |
| DOCX 招标书 | tender_sample.docx | 500KB | - |

### 5.3 测试项目

| 项目名 | 描述 | 包含文档 |
|-------|------|---------|
| 测试项目-完整 | 用于完整流程测试 | 招标书 + 应标书 |
| 测试项目-部分 | 仅招标书 | 招标书 |
| 测试项目-空 | 无文档 | 无 |

---

## 6. 测试工具与环境

### 6.1 测试工具

| 工具 | 用途 |
|------|-----|
| pytest | Python 单元测试和集成测试 |
| pytest-asyncio | 异步测试支持 |
| pytest-cov | 代码覆盖率 |
| httpx | 异步 HTTP 客户端 (测试 API) |
| k6 | 性能/负载测试 |
| Playwright | 前端 E2E 测试 |
| Selenium | 备选前端测试 |
| Locust | 备选负载测试 |

### 6.2 测试环境配置

```bash
# 环境变量
export DATABASE_URL=postgresql://ssirs_user:y6+YufO6njlzxXiaNj6rA4xZaT3ofwT6@183.66.37.186:7004/bjt_agent
export REDIS_URL=redis://183.66.37.186:7005/0
export SECRET_KEY=your-secret-key-here
export MINI_AGENT_API_KEY=your-api-key
export MINI_AGENT_API_BASE=https://api.minimaxi.com
export RAG_MEMORY_SERVICE_URL=http://localhost:8001
```

---

## 7. 测试执行计划

### 7.1 阶段划分

| 阶段 | 内容 | 持续时间 |
|------|------|---------|
| Phase 1 | 单元测试 - Models & Services | 2 天 |
| Phase 2 | 单元测试 - Agent Tools & Tasks | 2 天 |
| Phase 3 | API 集成测试 | 3 天 |
| Phase 4 | SSE & Celery 集成测试 | 2 天 |
| Phase 5 | 前端组件测试 | 2 天 |
| Phase 6 | E2E 测试 | 2 天 |
| Phase 7 | 性能与安全测试 | 2 天 |
| Phase 8 | 回归测试 | 1 天 |

### 7.2 每日测试报告

- 测试执行数量
- 通过/失败数量
- 覆盖率统计
- 发现的 Bug 列表
- 阻塞问题

---

## 8. 风险与 Mitigation

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 外部 API 不可用 (MiniMax, RAG) | Agent 功能测试无法执行 | Mock 外部服务 |
| 数据库连接问题 | 所有测试失败 | 独立的测试数据库 |
| 前端依赖后端 | 无法独立测试前端 | Mock API 服务 |
| 并发测试资源 | 需要足够并发能力 | 使用专门的压测环境 |

---

## 9. 交付物

1. **测试计划本文档** - TEST_PLAN.md
2. **测试用例库** - docs/testplan/test_cases/
3. **API 测试集合** - docs/testplan/api_tests/
4. **E2E 测试脚本** - docs/testplan/e2e_tests/
5. **测试报告** - docs/testplan/reports/
6. **测试覆盖率报告** - docs/testplan/coverage/

---

## 10. 附录

### 10.1 关键 API 响应格式

#### 审查结果响应
```json
{
  "summary": {
    "total_requirements": 25,
    "compliant": 18,
    "non_compliant": 7,
    "critical": 2,
    "major": 3,
    "minor": 2
  },
  "findings": [
    {
      "id": "uuid",
      "requirement": "投标人需具备ISO9001认证",
      "bid_content": "未在资质证书部分找到ISO9001证书",
      "is_compliant": false,
      "severity": "critical",
      "location": {"page": 5, "line": 23},
      "suggestion": "补充提供ISO9001质量管理体系认证证书",
      "explanation": "招标文件明确要求提供ISO9001认证"
    }
  ]
}
```

#### SSE 事件格式
```
event: step
data: {"step_number": 1, "step_type": "thought", "content": "Reading tender document..."}

event: complete
data: {"status": "completed", "findings_count": 12}

event: error
data: {"error": "Document not found"}
```

### 10.2 数据库表结构索引

- `users.id` - 主键
- `projects.user_id` - 外键到 users
- `documents.project_id` - 外键到 projects
- `review_tasks.project_id` - 外键到 projects
- `review_results.task_id` - 外键到 review_tasks
- `agent_steps.task_id` - 外键到 review_tasks
