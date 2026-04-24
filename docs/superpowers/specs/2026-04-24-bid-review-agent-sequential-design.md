# BidReviewAgent 串行执行改造设计

## 目标

将 BidReviewAgent 从并行执行（最多5个并发）改为串行执行（一个接一个）。

## 背景

当前 `MasterAgent` 使用 `asyncio.Semaphore(max_parallel=5)` + `asyncio.gather()` 并行执行多个 `BidReviewAgent` 子代理。用户要求简化为串行执行。

## 改动范围

### 1. `backend/agent/master/master_agent.py`

#### `__init__()` 方法 (第 27-36 行)
- **删除** `max_parallel: int = 5` 参数
- **删除** `self.max_parallel = max_parallel` 赋值

#### `run()` 方法 (第 104 行)
- **修改** `await self._run_sub_agents_parallel(todo_service)`
- **改为** `await self._run_sub_agents(todo_service)`

#### `_run_sub_agents_parallel()` 方法 (第 118-133 行)
- **重命名** 为 `_run_sub_agents()`
- **删除** `semaphore = asyncio.Semaphore(self.max_parallel)`
- **删除** `asyncio.gather()` 逻辑
- **改为** 简单的 `for` 循环串行调用：

```python
async def _run_sub_agents(self, todo_service) -> None:
    """串行执行所有子代理."""
    logger.info(f"[_run_sub_agents] Starting sequential execution with {len(self._todo_items)} todos")
    for i, todo in enumerate(self._todo_items):
        try:
            result = await self._run_single_sub_agent(todo, todo_service, self._session_factory)
            logger.info(f"[_run_sub_agents] Task {i+1}/{len(self._todo_items)} completed")
        except Exception as e:
            logger.error(f"[_run_sub_agents] Task {i+1} raised exception: {e}")
    logger.info(f"[_run_sub_agents] All tasks completed")
```

## 不需要改动的部分

- `BidReviewAgent` 本身 (`backend/agent/bid_review_agent.py`)
- SSE 事件推送逻辑 (`backend/services/sse_service.py`, `backend/tasks/review_tasks.py`)
- 前端页面 (`frontend/src/views/ReviewExecutionView.vue`)
- API 接口 (`backend/api/review.py`)

## 测试计划

1. 启动后端服务，创建一个审查任务
2. 验证日志中子代理是串行执行的（一个完成后才开始下一个）
3. 验证 SSE 事件仍然正确推送到前端
4. 验证前端页面显示正常

## 风险评估

- **低风险**：仅删除并发逻辑，不改变业务逻辑
- 串行执行预计会显著增加总执行时间，但不改变审查结果正确性
