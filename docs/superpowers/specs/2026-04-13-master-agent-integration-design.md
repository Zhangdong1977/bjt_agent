# MasterAgent Integration Design

## Context

The frontend displays a single "BidReviewAgent 执行" block in the timeline. The user expects to see the multi-agent execution flow (MasterAgent → SubAgentExecutor → Merge) with visible sub-agent components.

Root cause: `backend/tasks/review_tasks.py` directly calls `BidReviewAgent.run_review()` (single-agent mode) instead of using `MasterAgent` (multi-agent mode).

## Problem

- Frontend has two rendering modes based on `todoList` presence
- No `todo_created` events are emitted → frontend shows `BidReviewAgentMode`
- `MasterAgent` exists with full sub-agent coordination but is never invoked

## Solution

Modify `run_review` Celery task to use `MasterAgent` instead of `BidReviewAgent` in `_run_agent_review()`.

## Architecture

```
Frontend (ReviewExecutionView)
  │ SSE 订阅
  ▼
FastAPI (run_review Celery 任务)
  │
  ├─ 创建 TodoService
  ├─ 创建 MasterAgent (rule_library_path="/home/openclaw/bjt_agent/docs/rules")
  │
  ▼
MasterAgent.run()
  ├─ 扫描规则库 (RuleLibraryScannerTool)
  ├─ 解析规则文档 (RuleParserTool)
  ├─ 创建 TodoItem (通过 TodoService)
  ├─ 发送 SSE: todo_created
  │
  ├─ 并行执行子代理 (SubAgentExecutor)
  │   ├─ 发送 SSE: sub_agent_started
  │   └─ 发送 SSE: sub_agent_completed
  │
  └─ 汇总结果 (merge)
      └─ 发送 SSE: merging_completed
```

## Changes

### File: `backend/tasks/review_tasks.py`

Modify `_run_agent_review()` function:

1. Create `TodoService(db)` instance for database operations
2. Replace `BidReviewAgent` instantiation with `MasterAgent`
3. Use fixed `rule_library_path="/home/openclaw/bjt_agent/docs/rules"`
4. Pass `event_callback` for SSE event publishing
5. Call `master.run(todo_service, session_id=task_id)` instead of `agent.run_review()`

### SSE Event Sequence

```
master_started           → 前端显示 "开始解析规则库"
master_scan_completed    → 显示扫描到的规则文档数量
todo_created             → 前端开始显示 SubAgentExecutorBlock
todo_list_completed      → 所有规则解析完成
sub_agent_started        → 子代理开始执行
sub_agent_completed      → 子代理完成
merging_started          → 开始合并结果
merging_completed        → 合并完成，审查结束
```

## Error Handling

- MasterAgent execution error → logs to file (backend issues log to file per requirements)
- Database operation failure → logs via Python logger to file
- Sub-agent retry logic preserved (inherited from existing implementation)

## No Changes Required

- Frontend stays unchanged (SSE event handling already correct)
- `ReviewSession` API remains unused but intact
- Existing `BidReviewAgent` class preserved for other use cases

## Implementation Notes

- Rule library path is fixed: `/home/openclaw/bjt_agent/docs/rules`
- Session ID used as `session_id` parameter for TodoService operations
- All SSE events forwarded via `event_callback` to Redis Streams
