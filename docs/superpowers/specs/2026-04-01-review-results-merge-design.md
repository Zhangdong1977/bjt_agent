# 项目级不合格项合并设计

## 背景

当前 `get_review_results` API 只返回**最新任务**的 `ReviewResult`，而非项目所有历史任务的合并结果。每次新审查会覆盖前一次的结果，导致历史不合格项丢失。

**需求**：新任务完成后，将新发现的不合格项与项目当前的不合格项通过 AI 语义相似度比较进行去重合并。

## 架构流程

```
新任务完成
    ↓
Celery: run_review task 成功
    ↓
Celery: merge_review_results task 启动
    ↓
SSE: 发送 {type: 'merging', message: '正在合并历史结果...'}
    ↓
AI 去重合并操作
    ↓
SSE: 发送 {type: 'merged', merged_count: N, total_count: M}
    ↓
前端刷新审查结果
```

## 数据模型

### 新增 `ProjectReviewResult` 模型

项目级合并后的不合格项，替代原 `ReviewResult` 作为前端展示的数据源。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| project_id | UUID | 项目ID，外键 |
| requirement_key | VARCHAR(255) | 招标要求标识（如 req_001） |
| requirement_content | TEXT | 招标要求内容 |
| bid_content | TEXT | 应标内容 |
| is_compliant | BOOLEAN | 是否合格（默认 false） |
| severity | VARCHAR(50) | 严重程度：critical / major / minor |
| location_page | INT | 页码（可选） |
| location_line | INT | 行号（可选） |
| suggestion | TEXT | 建议（可选） |
| explanation | TEXT | AI分析说明（可选） |
| source_task_id | UUID | 首次发现不合格项的来源任务 |
| merged_from_count | INT | 本条记录由几条记录合并而来 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### 模型关系

```
Project (1) ──< ProjectReviewResult (N)
ReviewTask (1) ──< ReviewResult (N)  ← 保持不变，作为原始数据
```

## 后端改动

### 1. 新增 Celery Task

**任务名**: `backend.tasks.review_tasks.merge_review_results`

**输入**: `project_id: str, latest_task_id: str`

**处理流程**:
1. 查询项目所有历史 `ReviewResult` 记录
2. 查询项目当前所有 `ProjectReviewResult` 记录（已合并的）
3. 将新任务的 ReviewResult 与现有的 ProjectReviewResult 做 AI 语义去重
4. 去重后写入/更新 `project_review_results` 表
5. 通过 SSE 发送 `merging` 和 `merged` 事件

**AI 去重策略**:
- 比较字段拼接: `requirement_content` + `bid_content` + `explanation` + `suggestion`
- 使用 Mini-Max API 计算语义相似度
- 相似度阈值: 85%（待标定）
- 保留规则: 相同内容保留 severity 更高（critical > major > minor）的记录

### 2. 修改 `run_review` Task

在 `run_review` 任务完成（`task.status = 'completed'`）后，触发 `merge_review_results` 异步任务：

```python
# run_review task 结束时
_publish_event(task_id, "complete", {...})
# 触发合并任务
from backend.tasks.review_tasks import merge_review_results
merge_review_results.delay(project_id=task.project_id, latest_task_id=task_id)
```

### 3. SSE 事件扩展

```python
# 合并开始
_publish_event(task_id, "merging", {"message": "正在合并历史结果..."})

# 合并完成
_publish_event(task_id, "merged", {
    "merged_count": 4,    # 新发现的不合格项数量
    "total_count": 6      # 合并后总数量
})
```

### 4. API 改动

**`GET /projects/{project_id}/review`** (review.py:99)

修改为查询 `project_review_results` 表：

```python
# 原：查询最新 ReviewTask 的 ReviewResult
# 改：查询 ProjectReviewResult 表
result = await db.execute(
    select(ProjectReviewResult)
    .where(ProjectReviewResult.project_id == project_id)
    .order_by(ProjectReviewResult.severity.asc())
)
```

**新增 Schema**: `ProjectReviewResultResponse`（与 `ReviewResultResponse` 结构相同）

## 前端改动

### 1. SSE 事件处理扩展

```typescript
case 'merging':
    console.log('[SSE] 正在合并历史结果...')
    // 可选：显示全局 loading 状态
    break

case 'merged':
    console.log('[SSE] 合并完成, merged_count:', event.merged_count, 'total_count:', event.total_count)
    fetchReviewResults()  // 刷新合并后的结果
    break
```

### 2. fetchReviewResults 保持不变

`fetchReviewResults()` 调用 `reviewApi.getResults()`，API 返回的数据结构已统一为 `ProjectReviewResult`。

### 3. 状态指示（可选）

- 在 SSE 收到 `merging` 事件时，可在 UI 显示"正在合并历史结果..."
- 收到 `merged` 事件后隐藏该提示并刷新列表

## 文件改动清单

### 新增文件

- `backend/models/project_review_result.py` — ProjectReviewResult 模型
- `backend/tasks/merge_task.py` — 合并逻辑（可选独立文件）

### 修改文件

- `backend/models/__init__.py` — 导出 ProjectReviewResult
- `backend/tasks/review_tasks.py` — 新增 merge_review_results task，修改 run_review 触发合并
- `backend/api/review.py` — get_review_results 改为查询 ProjectReviewResult 表
- `backend/schemas/review.py` — 新增 ProjectReviewResultResponse schema
- `frontend/src/stores/project.ts` — SSE 事件处理增加 merging/merged case

## 测试要点

1. 新任务完成后，数据库 `project_review_results` 表有新记录
2. 新任务发现的不合格项与历史有重复时，只保留 severity 更高的
3. 前端 SSE 收到 `merged` 事件后刷新显示正确数量
4. `GET /projects/{project_id}/review` 返回合并后的完整列表
