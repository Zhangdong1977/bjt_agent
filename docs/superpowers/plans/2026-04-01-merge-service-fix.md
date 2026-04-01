# MergeService 重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构合并逻辑，使每个新结果与所有现有记录对比，keep 生成新 key，replace 更新指定记录，discard 丢弃。

**Architecture:** 修改 `merge_project_results` 方法的核心循环逻辑，LLM 对比时传入全部现有记录，保持顺序处理以便后续新结果能参考前面保留的记录。

**Tech Stack:** Python async/await, SQLAlchemy async, pytest

---

## Task 1: 修改 LLM 对比范围 - 传入所有现有记录

**Files:**
- Modify: `backend/services/merge_service.py:111-151`

- [ ] **Step 1: 阅读 merge_decision_parser.py 确认决策解析逻辑**

```python
# 查看 parse_merge_decision 函数，理解 keep_both 的含义
# 预期：keep_both 应该和 keep 类似，但可能用于解析失败时的保底策略
```

- [ ] **Step 2: 修改 LLM 调用，传入所有现有记录（不只是匹配 key 的那条）**

当前代码（第118-121行）：
```python
decision = await self._get_llm_merge_decision(
    new_result,
    [existing]  # ← 只传1条！需要改成所有现有记录
)
```

修改为：
```python
# 构建所有现有记录的列表（用于 LLM 对比）
all_existing = list(existing_merged)  # 复制，避免 mutation

decision = await self._get_llm_merge_decision(
    new_result,
    all_existing
)
```

- [ ] **Step 3: 运行现有测试确认未破坏**

```bash
cd /home/openclaw/bjt_agent && python -m pytest backend/tests/test_merge_service.py -v
```

---

## Task 2: 修改 "keep" 动作 - 生成新 key 并添加新记录

**Files:**
- Modify: `backend/services/merge_service.py:124-131`

- [ ] **Step 1: 分析 key 生成逻辑**

现有 key 格式为 `req_XXX`（如 req_001）。需要：
1. 从所有现有记录中找到最大编号
2. 生成新 key = `req_{max+1:03d}`

- [ ] **Step 2: 添加辅助函数 `_generate_new_key`**

在 `merge_project_results` 方法前添加：

```python
def _generate_new_requirement_key(self, existing_records: list[dict]) -> str:
    """Generate a new requirement key that doesn't exist in existing records.

    Args:
        existing_records: List of existing ProjectReviewResult records

    Returns:
        New requirement key in format 'req_XXX' where XXX is next sequential number
    """
    max_num = 0
    for rec in existing_records:
        key = rec.get("requirement_key", "")
        if key.startswith("req_"):
            try:
                num = int(key[4:])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"req_{max_num + 1:03d}"
```

- [ ] **Step 3: 修改 keep 分支逻辑**

当前（第124-131行）：
```python
if decision["action"] == "keep":
    # keep = 保留新旧两条记录
    new_record_copy = {**new_result, "merged_from_count": 1}
    existing_record_copy = {**existing, "merged_from_count": existing.get("merged_from_count", 1)}
    new_merged_records.append(existing_record_copy)
    new_merged_records.append(new_record_copy)
    matched_keys.add(req_key)
    merge_count += 1
```

修改为：
```python
if decision["action"] == "keep":
    # keep = 新发现是全新的，添加为新记录（生成新 key）
    new_key = self._generate_new_requirement_key(new_merged_records)
    merged_record = {
        **new_result,
        "requirement_key": new_key,
        "merged_from_count": 1,
    }
    new_merged_records.append(merged_record)
    merge_count += 1
```

- [ ] **Step 4: 确认 matched_keys 不再需要（删除相关代码）**

因为 keep 不再是"匹配到同 key"，而是"生成新 key"，所以：
- `matched_keys.add(req_key)` 应该移除
- keep 分支不再需要追踪 matched_keys

---

## Task 3: 修改 "replace" 动作 - 正确更新目标记录

**Files:**
- Modify: `backend/services/merge_service.py:132-137`

- [ ] **Step 1: 修改 replace 分支**

当前（第132-137行）：
```python
elif decision["action"] == "replace":
    merged_record = {**existing, **new_result}
    merged_record["merged_from_count"] = existing.get("merged_from_count", 1) + 1
    new_merged_records.append(merged_record)
    matched_keys.add(req_key)
    merge_count += 1
```

问题：`existing` 是 `existing_by_key[req_key]` 找到的那条记录，但 LLM 可能说 "replace req_002" 而 `req_key` 可能是 `new_001`。

修改为：
```python
elif decision["action"] == "replace":
    replace_key = decision.get("replace_key")
    if not replace_key:
        # 如果没有指定 replace_key，尝试从 decision 文本中解析
        # parse_merge_decision 应该已经解析了 replace_key
        logger.warning(f"[merge] replace action but no replace_key specified, using keep")
        new_key = self._generate_new_requirement_key(new_merged_records)
        merged_record = {**new_result, "requirement_key": new_key, "merged_from_count": 1}
        new_merged_records.append(merged_record)
    else:
        # 找到目标记录并更新
        target_record = None
        for rec in new_merged_records:
            if rec.get("requirement_key") == replace_key:
                target_record = rec
                break
        if target_record:
            # 用新结果更新目标记录
            target_record.update({
                **new_result,
                "requirement_key": replace_key,  # 保持原有 key
                "merged_from_count": target_record.get("merged_from_count", 1) + 1,
            })
        else:
            logger.warning(f"[merge] replace target {replace_key} not found, treating as keep")
            new_key = self._generate_new_requirement_key(new_merged_records)
            merged_record = {**new_result, "requirement_key": new_key, "merged_from_count": 1}
            new_merged_records.append(merged_record)
    merge_count += 1
```

---

## Task 4: 修改 "discard" 动作

**Files:**
- Modify: `backend/services/merge_service.py:138-140`

- [ ] **Step 1: 修改 discard 分支**

当前：
```python
elif decision["action"] == "discard":
    new_merged_records.append(existing)
    matched_keys.add(req_key)
```

discard = 丢弃新发现，保留现有记录不变。但现有逻辑是追加 `existing`，这是错的——`existing` 只是匹配到的那条记录。

discard 应该：
- 不添加新记录
- 不修改任何现有记录
- 保持 new_merged_records 不变

修改为：
```python
elif decision["action"] == "discard":
    # discard = 丢弃新发现，什么都不做
    # 现有记录已经在 new_merged_records 中，不需要额外处理
    pass
```

---

## Task 5: 处理非匹配 key（新 key）

**Files:**
- Modify: `backend/services/merge_service.py:147-151`

- [ ] **Step 1: 分析当前逻辑**

当前：
```python
else:
    # 新 key，直接添加
    merged_record = {**new_result, "merged_from_count": 1}
    new_merged_records.append(merged_record)
    matched_keys.add(req_key)
```

问题：新 key 的结果也应该经过 LLM 对比（而不是直接添加），以判断是否应该 keep/discard。

修改为：
```python
else:
    # 新 key，但需要与所有现有记录对比
    all_existing = list(existing_merged)  # 原始 existing_merged
    decision = await self._get_llm_merge_decision(new_result, all_existing)
    logger.info(f"[merge] LLM decision for new key {req_key}: action={decision['action']}")

    if decision["action"] == "keep":
        # 新 key 直接添加
        merged_record = {**new_result, "merged_from_count": 1}
        new_merged_records.append(merged_record)
    elif decision["action"] == "replace":
        # replace 在新 key 场景下应该降级为 keep
        new_key = self._generate_new_requirement_key(new_merged_records)
        merged_record = {**new_result, "requirement_key": new_key, "merged_from_count": 1}
        new_merged_records.append(merged_record)
    elif decision["action"] == "keep_both":
        # keep_both 也降级为 keep
        merged_record = {**new_result, "merged_from_count": 1}
        new_merged_records.append(merged_record)
    # discard: 什么都不做
```

---

## Task 6: 修改 matched_keys 处理逻辑

**Files:**
- Modify: `backend/services/merge_service.py:151-158`

- [ ] **Step 1: 删除或简化 matched_keys 逻辑**

由于 keep/discard/replace 的语义改变，`matched_keys` 的用途需要重新审视：

- keep: 添加新记录，不涉及 matched_keys
- replace: 更新目标记录，需要知道哪个 key 被替换了
- discard: 什么都不做

matched_keys 原本用于"追踪哪些 existing 记录已被处理"，但现在 discard 不需要追加任何东西，replace 直接通过 replace_key 定位。

可以简化：
1. keep: 不需要追踪
2. replace: 通过 replace_key 定位，不需要 matched_keys
3. discard: 不需要追踪

删除 `matched_keys.add(req_key)` 相关代码。

---

## Task 7: 清理历史记录处理逻辑

**Files:**
- Modify: `backend/services/merge_service.py:153-159`

- [ ] **Step 1: 确认历史记录处理逻辑**

当前（第153-159行）：
```python
# Handle historical records not in latest task
for rec in existing_merged:
    req_key = rec.get("requirement_key", "")
    if req_key not in matched_keys:
        new_merged_records.append(rec)
```

这个逻辑的意图是：如果某个 existing record 没有被任何新结果"匹配"到，仍然保留。

但在新的语义下：
- 如果 existing record 被 replace 更新了，它已经在 new_merged_records 中（以更新后的形式）
- 如果 existing record 没被任何新结果处理，它应该保留

问题：replace 更新的是 new_merged_records 中的引用，但 original `existing_merged` 中的记录不会被修改。

需要重新设计：
1. new_merged_records 一开始就包含所有 existing_merged 的副本
2. replace 操作修改 new_merged_records 中的对应记录
3. discard/keep 不修改

修改初始化逻辑（第108行附近）：
```python
new_merged_records: list[dict] = []
# 先复制所有现有记录作为基础
for rec in existing_merged:
    new_merged_records.append({**rec})
```

然后简化历史记录处理为：
```python
# 不再需要处理未匹配的记录，因为所有记录已经在 new_merged_records 中
```

---

## Task 8: 编写新测试用例

**Files:**
- Modify: `backend/tests/test_merge_service.py`

- [ ] **Step 1: 添加测试 - keep 生成新 key**

```python
@pytest.mark.asyncio
async def test_keep_generates_new_key(self, mock_agent, mock_db):
    """When LLM says keep, should generate new key like req_004."""
    mock_agent.decide_merge.return_value = "决策：keep\n理由：新发现是全新的\n替换key：无"

    service = MergeService(mock_db, mock_agent)

    new_finding = {
        "requirement_key": "new_001",
        "requirement_content": "新要求内容",
    }
    existing_findings = [
        {"requirement_key": "req_001", "requirement_content": "要求1"},
        {"requirement_key": "req_002", "requirement_content": "要求2"},
        {"requirement_key": "req_003", "requirement_content": "要求3"},
    ]

    decision = await service._get_llm_merge_decision(new_finding, existing_findings)

    assert decision["action"] == "keep"

    # Test _generate_new_requirement_key
    new_key = service._generate_new_requirement_key(existing_findings)
    assert new_key == "req_004"
```

- [ ] **Step 2: 添加测试 - replace 更新目标记录**

```python
@pytest.mark.asyncio
async def test_replace_updates_target_record(self, mock_agent, mock_db):
    """When LLM says replace req_002, should update req_002 content."""
    mock_agent.decide_merge.return_value = "决策：replace\n理由：新发现更完整\n替换key：req_002"

    service = MergeService(mock_db, mock_agent)

    new_finding = {
        "requirement_key": "new_001",
        "requirement_content": "新的更完整的内容",
    }

    decision = await service._get_llm_merge_decision(new_finding, [])

    assert decision["action"] == "replace"
    assert decision["replace_key"] == "req_002"
```

- [ ] **Step 3: 添加测试 - discard 丢弃**

```python
@pytest.mark.asyncio
async def test_discard_ignores_new_finding(self, mock_agent, mock_db):
    """When LLM says discard, should not add new record."""
    mock_agent.decide_merge.return_value = "决策：discard\n理由：重复内容\n替换key：无"

    service = MergeService(mock_db, mock_agent)

    decision = await service._get_llm_merge_decision({}, [])

    assert decision["action"] == "discard"
```

---

## Task 9: 端到端集成测试

**Files:**
- Modify: `backend/tests/test_merge_service.py`

- [ ] **Step 1: 添加端到端测试 - 完整合并流程**

```python
@pytest.mark.asyncio
async def test_full_merge_flow_with_sequential_new_results(self, mock_agent, mock_db):
    """Test: existing [req_001, req_002, req_003], new task [new_001, new_002]

    Flow:
    1. new_001 vs [req_001, req_002, req_003] → keep → adds as req_004
    2. new_002 vs [req_001, req_002, req_003, req_004] → replace req_002
    """
    # 模拟 LLM 决策
    async def mock_decide(new_finding, existing):
        key = new_finding.get("requirement_key", "")
        if key == "new_001":
            return "决策：keep\n理由：新发现全新\n替换key：无"
        elif key == "new_002":
            return "决策：replace\n理由：更新req_002\n替换key：req_002"
        return "决策：keep\n理由：默认\n替换key：无"

    mock_agent.decide_merge = AsyncMock(side_effect=mock_decide)

    service = MergeService(mock_db, mock_agent)

    # Mock existing merged records
    existing_merged = [
        {"requirement_key": "req_001", "requirement_content": "内容1", "task_id": "t1",
         "bid_content": "bid1", "is_compliant": False, "severity": "major",
         "location_page": 1, "location_line": 10, "suggestion": "s1", "explanation": "e1"},
        {"requirement_key": "req_002", "requirement_content": "内容2", "task_id": "t1",
         "bid_content": "bid2", "is_compliant": False, "severity": "minor",
         "location_page": 2, "location_line": 20, "suggestion": "s2", "explanation": "e2"},
        {"requirement_key": "req_003", "requirement_content": "内容3", "task_id": "t1",
         "bid_content": "bid3", "is_compliant": True, "severity": None,
         "location_page": 3, "location_line": 30, "suggestion": None, "explanation": "e3"},
    ]

    # Mock _get_existing_merged
    async def mock_get_existing():
        return existing_merged
    service._get_existing_merged = mock_get_existing

    # Mock new results
    latest_results = [
        {"requirement_key": "new_001", "requirement_content": "新内容1", "task_id": "t2",
         "bid_content": "new_bid1", "is_compliant": False, "severity": "critical",
         "location_page": 5, "location_line": 50, "suggestion": "ns1", "explanation": "ne1"},
        {"requirement_key": "new_002", "requirement_content": "新内容2-更新", "task_id": "t2",
         "bid_content": "new_bid2", "is_compliant": False, "severity": "major",
         "location_page": 6, "location_line": 60, "suggestion": "ns2", "explanation": "ne2"},
    ]

    # Mock _get_historical_results
    async def mock_get_historical():
        return existing_merged + latest_results
    service._get_historical_results = mock_get_historical

    # 执行合并
    merge_count, total_count = await service.merge_project_results(
        project_id="p1",
        latest_task_id="t2"
    )

    # 验证
    assert merge_count == 2  # new_001 keep, new_002 replace
    # 最终应该有: req_001, req_002(被更新), req_003, req_004(new_001 keep)
    assert total_count == 4
```

---

## 执行顺序

1. Task 1: 修改 LLM 对比范围
2. Task 2: 修改 keep 动作
3. Task 3: 修改 replace 动作
4. Task 4: 修改 discard 动作
5. Task 5: 处理非匹配 key
6. Task 6: 清理 matched_keys
7. Task 7: 清理历史记录处理
8. Task 8: 编写单元测试
9. Task 9: 编写集成测试

**建议使用 Subagent-Driven 方式执行，每个 Task 由独立 subagent 完成，完成后审查再继续下一个。**
