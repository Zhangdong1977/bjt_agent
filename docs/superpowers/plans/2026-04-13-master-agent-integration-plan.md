# MasterAgent Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace BidReviewAgent with MasterAgent in `run_review` Celery task so frontend displays multi-agent execution flow with visible sub-agent components.

**Architecture:** Modify `_run_agent_review()` in `backend/tasks/review_tasks.py` to create `MasterAgent` instead of `BidReviewAgent`, use fixed rule library path, and properly handle the different return structure.

**Tech Stack:** Python 3.11, Celery, SQLAlchemy async, MasterAgent, TodoService

---

## File Map

- **Modify:** `backend/tasks/review_tasks.py:364-428` — Replace BidReviewAgent with MasterAgent in `_run_agent_review()`

---

## Task 1: Modify `_run_agent_review` to Use MasterAgent

**Files:**
- Modify: `backend/tasks/review_tasks.py:364-428`

- [ ] **Step 1: Read current implementation**

Verify the exact lines to replace by re-reading the function:
```bash
sed -n '364,428p' backend/tasks/review_tasks.py
```

- [ ] **Step 2: Replace BidReviewAgent with MasterAgent**

Replace the entire `_run_agent_review` function with this implementation:

```python
async def _run_agent_review(
    task_id: str,
    tender_doc,
    bid_doc,
    db,
) -> list[dict]:
    """Run the agent review process and return findings.

    Uses MasterAgent with SubAgentExecutor for parallel multi-agent review.
    """
    from backend.agent.master.master_agent import MasterAgent
    from backend.services.todo_service import TodoService

    # Get document paths - prefer markdown, fallback to html
    tender_path = tender_doc.parsed_markdown_path or tender_doc.parsed_html_path or ""
    bid_path = bid_doc.parsed_markdown_path or bid_doc.parsed_html_path or ""

    if not tender_path or not Path(tender_path).exists():
        raise FileNotFoundError("Tender document not parsed")

    if not bid_path or not Path(bid_path).exists():
        raise FileNotFoundError("Bid document not parsed")

    # Fixed rule library path
    rule_library_path = "/home/openclaw/bjt_agent/docs/rules"

    # Create event callback for SSE
    def event_cb(event_type: str, data: dict):
        _publish_event(task_id, event_type, data)

    # Get user_id
    user_id = ""
    if hasattr(tender_doc.project, 'user_id'):
        user_id = str(tender_doc.project.user_id)

    # Create TodoService for MasterAgent to use
    todo_service = TodoService(db)

    # Create MasterAgent
    master = MasterAgent(
        project_id=str(tender_doc.project_id),
        rule_library_path=rule_library_path,
        tender_doc_path=tender_path,
        bid_doc_path=bid_path,
        user_id=user_id,
        event_callback=event_cb,
    )

    # Run the agent
    try:
        result = await master.run(todo_service, session_id=task_id)

        if result.get("success"):
            merged_result = result.get("merged_result", {})
            findings = merged_result.get("findings", [])
            return findings
        else:
            error_msg = result.get("error", "Unknown error")
            event_cb("error", {"message": f"MasterAgent error: {error_msg}"})
            return _create_error_finding(error_msg)

    except Exception as e:
        logger.exception(f"MasterAgent execution failed for task {task_id}: {e}")
        event_cb("error", {"message": f"Agent error: {str(e)}"})
        return _create_error_finding(str(e))
```

- [ ] **Step 3: Verify the edit**

Confirm the change was applied correctly:
```bash
sed -n '364,440p' backend/tasks/review_tasks.py
```

Expected: Should show MasterAgent import and new function body.

- [ ] **Step 4: Test syntax**

```bash
cd /home/openclaw/bjt_agent && python -m py_compile backend/tasks/review_tasks.py
```

Expected: No output (success).

- [ ] **Step 5: Commit**

```bash
git add backend/tasks/review_tasks.py && git commit -m "feat(review): use MasterAgent for multi-agent review execution"
```

---

## Verification Steps

After implementation:

1. **Start services:**
   ```bash
   ./scripts/bjt.sh restart
   ```

2. **Trigger a review** from frontend and check:
   - SSE events should include `master_started`, `todo_created`, `sub_agent_started`, etc.
   - Frontend should show SubAgentExecutor timeline (multiple sub-agent blocks)
   - Check backend logs: `grep "MasterAgent" backend.log` or Celery worker logs

3. **Check SSE events:**
   - Connect to SSE endpoint and verify event sequence

---

## Rollback Plan

If issues occur, revert with:
```bash
git revert HEAD
```

The previous `BidReviewAgent` implementation will be restored immediately.
