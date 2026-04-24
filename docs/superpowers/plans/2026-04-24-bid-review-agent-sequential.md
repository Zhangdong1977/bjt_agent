# BidReviewAgent 串行执行改造实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `BidReviewAgent` 子代理从并行执行（最多5个并发）改为串行执行

**Architecture:** 移除 `asyncio.Semaphore` + `asyncio.gather()` 并发逻辑，改为 `for` 循环串行调用 `_run_single_sub_agent()`

**Tech Stack:** Python async/await, asyncio

---

## 文件变更清单

- **修改**: `backend/agent/master/master_agent.py`

---

## 任务 1: 移除 `max_parallel` 参数

**文件:** `backend/agent/master/master_agent.py:19-37`

- [ ] **Step 1: 读取当前 `__init__` 签名并修改**

将 `__init__` 方法中的 `max_parallel: int = 5,` 参数和 `self.max_parallel = max_parallel` 赋值删除。

修改后的 `__init__` 应该是：

```python
def __init__(
    self,
    project_id: str,
    rule_library_path: str,
    tender_doc_path: str,
    bid_doc_path: str,
    user_id: str,
    event_callback: Optional[Callable] = None,
    max_retries: int = 3,
):
    self.project_id = project_id
    self.rule_library_path = rule_library_path
    self.tender_doc_path = tender_doc_path
    self.bid_doc_path = bid_doc_path
    self.user_id = user_id
    self.event_callback = event_callback
    self.max_retries = max_retries

    self.scanner = RuleLibraryScannerTool()
    self._todo_items = []
    self._session_id: Optional[str] = None
```

- [ ] **Step 2: 验证语法正确**

运行: `cd /home/openclaw/bjt_agent && python -m py_compile backend/agent/master/master_agent.py`
预期: 无输出（语法正确）

- [ ] **Step 3: 提交变更**

```bash
git add backend/agent/master/master_agent.py
git commit -m "refactor: remove max_parallel parameter from MasterAgent"
```

---

## 任务 2: 将 `_run_sub_agents_parallel` 改为串行执行

**文件:** `backend/agent/master/master_agent.py:118-133`

- [ ] **Step 1: 替换 `_run_sub_agents_parallel` 方法实现**

将整个 `_run_sub_agents_parallel` 方法替换为 `_run_sub_agents` 串行实现：

```python
async def _run_sub_agents(self, todo_service) -> None:
    """串行执行所有子代理."""
    logger.info(f"[_run_sub_agents] Starting sequential execution with {len(self._todo_items)} todos")
    for i, todo in enumerate(self._todo_items):
        try:
            result = await self._run_single_sub_agent(todo, todo_service, self._session_factory)
            logger.info(f"[_run_sub_agents] Task {i+1}/{len(self._todo_items)} completed, success={result.get('success')}")
        except Exception as e:
            logger.error(f"[_run_sub_agents] Task {i+1} raised exception: {e}")
    logger.info(f"[_run_sub_agents] All tasks completed")
```

- [ ] **Step 2: 更新 `run()` 方法中的调用**

将 `run()` 方法第 104 行的 `await self._run_sub_agents_parallel(todo_service)` 改为 `await self._run_sub_agents(todo_service)`

- [ ] **Step 3: 验证语法正确**

运行: `cd /home/openclaw/bjt_agent && python -m py_compile backend/agent/master/master_agent.py`
预期: 无输出（语法正确）

- [ ] **Step 4: 提交变更**

```bash
git add backend/agent/master/master_agent.py
git commit -m "refactor: change sub-agent execution from parallel to sequential"
```

---

## 任务 3: 验证测试

**文件:** `backend/agent/master/` 目录下是否有相关测试

- [ ] **Step 1: 检查是否有测试文件**

运行: `find backend/agent -name "*test*.py" -o -name "test_*.py" 2>/dev/null | head -20`
预期: 如有测试文件，运行测试验证改动未破坏功能

- [ ] **Step 2: 如有测试，运行测试**

运行: `cd /home/openclaw/bjt_agent && python -m pytest backend/agent/master/ -v 2>/dev/null || echo "No tests found or pytest not available"`
预期: 测试通过或 "No tests found"

---

## 自检清单

- [ ] `max_parallel` 参数已从 `__init__` 移除
- [ ] `self.max_parallel` 赋值已删除
- [ ] `_run_sub_agents_parallel` 已重命名为 `_run_sub_agents`
- [ ] `asyncio.Semaphore` 和 `asyncio.gather` 已移除
- [ ] `for` 循环串行调用 `_run_single_sub_agent` 已实现
- [ ] `run()` 方法中调用已更新
- [ ] `python -m py_compile` 通过
- [ ] 所有变更已提交
