# bjt-agent 开放 API 契约（v0.1 · 已按 B 线实现校准）

> 对应 bjt-agent 新增的 `/api/v1/open` 端点组（`backend/api/open.py`，**已实现**，默认关闭）。
> `scripts/bjt.py` 已封装全部端点；本文供直连调试、排查与对照。

## 鉴权与环境

- **Base URL（生产）**：`https://check.aibjt.com:30002/api/v1/open`（复用现有 nginx-LB 入口，无新增暴露面）
- 鉴权头：

| Header | 值 | 说明 |
|---|---|---|
| `X-Api-Key` | API Key | 必填，形如 `bjt_live_xxxxx`；服务端只存 sha256（`api_keys` 表，迁移 039） |
| `Idempotency-Key` | UUID（可选） | 仅提交类接口；Redis 缓存 24h，重放返回同一 task_id（尽力而为，Redis 不可用时退化为非幂等） |

- **总开关**：`.env` `OPEN_API_ENABLED`（默认 `false`；关闭时**整层 404** `not_found`）。
- 认证主体：key 绑定 bjt-agent 用户，任务/计费/审计归属该用户；任务 `client_channel='api'`（**豁免前端心跳超时取消**，保留 API 取消与总超时兜底）。
- **错误体（house style）**：`{"detail": {"code": "...", "message": "..."}}`（与 Web 端一致；`bjt.py` 已兼容 `{"error":{...}}` 回退）。

## 核心约定

- **document_id**：上传+解析完成的文档句柄。doc_type：`tender`/`bid`（审查）、`duplicate_left`/`duplicate_right`（查重）。文档 `source='api'`，**不占 Web 草稿配额**、不进 Web 草稿列表；每用户未使用上限 50（`OPEN_API_MAX_UNATTACHED_DOCUMENTS`）。
- **task_id**：异步任务句柄。提交立即返回 `{"task_id": "...", "service": "review|duplicate"}`。
- **任务状态**：`pending → running → completed / failed / cancelled`；`billing_status`：`pending/processing/retry/settled`。
- **上传**：**仅 multipart**（`file` + `doc_type` 表单字段）——远程 URL 由客户端先下载再传（`bjt.py` 已实现），服务端不做 URL 抓取（无 SSRF 面）。单份 ≤ 1GiB；pdf/docx/doc/xlsx。
- **隐式项目**：提交时自动创建 `source='api'` 项目承载任务（Web「我的项目」列表默认隐藏 API 项目）。
- **限流**（slowapi，按 key 哈希维度）：上传 60 req/min（`OPEN_API_RATE_PER_MINUTE`）、提交类 20 req/min；超限 429。
- **并发闸**：每 key `max_active_tasks`（`api_keys` 列，默认 1）个未结算任务；超限 409 `ACTIVE_BILLING_TASK_EXISTS`。
- **计费**：与 Web 同口径——发起时余额>0 闸（402），终态后按实际用量结算（**失败/取消也可能计费**）。

## 端点详情

### `GET /me`
```json
{"balance_points": 100.0, "recharge_points": 100.0, "gift_points": 0.0,
 "limits": {"rate_per_min": 60, "max_active_tasks": 1, "running_tasks": 0}}
```

### `POST /documents` — 上传并解析（multipart）
- 表单字段：`doc_type` + `file`。返回 201 `{"document_id": "...", "doc_type": "...", "status": "pending"}`，解析异步开始。

### `GET /documents/{id}`
```json
{"document_id": "...", "doc_type": "tender", "status": "parsed",
 "pages": 156, "word_count": 82013, "error": null}
```
- `status`：`pending / parsing / parsed / failed`（failed 多为加密/损坏文件）。

### `POST /review`
```json
{"tender_document_ids": ["d1"], "bid_document_ids": ["d2"]}
```
- 校验：文档归属当前用户、未关联任务、`status='parsed'`、类型正确；tender/bid 各 ≤ 20。
- 返回 201 `{"task_id": "...", "service": "review"}`。

### `POST /duplicate-check`
```json
{"left_document_id": "d1", "right_document_id": "d2"}
```
- 校验：两份不同、类型分别为 duplicate_left/right、已解析；服务端做相同内容预检（命中 → 400 `identical_documents`）。
- 返回 201 `{"task_id": "...", "service": "duplicate"}`。

### `GET /tasks/{task_id}`
```json
{"task_id": "...", "service": "review", "status": "running",
 "billing_status": "pending",
 "progress": {"percent": 47, "stage": "check", "stage_label": "AI 检查执行中",
              "current_step": 8, "total_steps": 17, "message": "已完成 8/17 个检查项"},
 "error": null, "created_at": "...", "updated_at": "..."}
```
- `progress.percent` 可能为 `null`（查重任务无数值进度；审查按检查项大类完成数估算）；`failed/cancelled` 时 `error` 非空（`task_failed`/`canceled`）。

### `GET /tasks/{task_id}/result`
- 非终态/未成功 → 409 `invalid_job_state`。
- **review**：
```json
{"service": "review", "task_id": "...",
 "report_url": "https://check.aibjt.com:30002/shared/<token>",
 "result": {"summary": {"conclusion": "...", "high_count": 3, "review_count": 39,
                        "tip_count": 25, "finding_count": 67},
            "issues": [{"id": 1, "risk_level": "high|review|tip",
                        "title": "...", "description": "...",
                        "tender_evidence": "...", "bid_evidence": "...",
                        "suggestion": "...", "location_page": 47,
                        "rule_doc_name": "...", "is_compliant": false}]}}
```
- **duplicate**：
```json
{"service": "duplicate", "task_id": "...", "report_url": null,
 "result": {"verdict": "reasonable|suspicious|unknown", "similarity_score": 0.18,
            "confidence": 0.86, "channel_scores": {"lexical": 0.2, "...": 0.0},
            "explanation": "...", "suggestion": "...",
            "evidences": [{"rule_doc_name": "...", "check_item_name": "...",
                           "verdict": "...", "similarity_score": 0.9,
                           "left_location": {...}, "left_excerpt": "...",
                           "right_location": {...}, "right_excerpt": "..."}]}}
```
- `report_url`：**仅审查任务**有（复用 share 令牌，7 天有效期，登录后可看，不含 API Key）；查重 v0 返回 `null`。
- 风险等级映射：`critical→high`、`major→review`、`minor→tip`（只对不合规项计数）。

### `POST /tasks/{task_id}/cancel`
- 尽力而为；已发生的 AI 成本仍会进入结算。返回 `{"task_id", "status": "cancelled", "message": "已请求取消（注意：取消的任务也可能产生费用）"}`。

### `GET /tasks?page=1`
```json
{"tasks": [{"task_id": "...", "service": "review", "status": "completed",
            "title": "API · 某招标文件.pdf", "created_at": "..."}]}
```
- 每页 20 条，按创建时间倒序（断点续查用）。

## 错误码速查（detail.code）

| HTTP | code | 含义与处理 |
|---|---|---|
| 401 | `missing_credentials` / `invalid_credentials` | 缺 X-Api-Key / Key 无效 |
| 403 | `key_revoked` | Key 已吊销 |
| 402 | `INSUFFICIENT_BALANCE` | 余额不足（发起闸），不建任务 |
| 404 | `not_found` | 开放层总开关关闭（整层 404） |
| 404 | `document_not_found` / `task_not_found` | 句柄不存在、非本人或已被任务使用 |
| 409 | `ACTIVE_BILLING_TASK_EXISTS` / `invalid_job_state` | 并发闸 / 未成功就取结果、状态不可取消 |
| 400 | `identical_documents` | 查重两份内容完全相同 |
| 413 | `file_too_large` | 单份 > 1GiB |
| 422 | `validation_error` | doc_type/类型不匹配/未解析完成/超上限等（message 有具体原因） |
| 429 | （slowapi） | 限流 → 退避重试 |

## 实现锚点（代码位置）

- 路由：`backend/api/open.py`；schema：`backend/schemas/open.py`
- 迁移：`backend/migrations/039_open_api.sql`（api_keys 表 + review_tasks.client_channel + projects/documents.source）
- 心跳豁免：`backend/agent/bid_review_agent.py` `_check_heartbeat_async`、`backend/tasks/duplicate_tasks.py` `_cancellation_monitor`
- 并发闸：`backend/services/task_lifecycle.py` `authorize_billable_task_start(max_active_tasks=...)` / `count_unsettled_tasks`
- Web 通道隔离：`backend/api/documents.py`（草稿配额/列表/attach）、`backend/api/projects.py`（列表隐藏 API 项目）
- 配置：`backend/config.py` `open_api_enabled / open_api_max_unattached_documents / public_base_url / open_api_rate_per_minute`
- 测试：`backend/tests/test_open_api.py`（12 用例全过）
