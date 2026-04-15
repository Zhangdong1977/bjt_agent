# 批量合并与重试机制实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现批量合并与重试机制，减少LLM调用次数并提高稳定性

**Architecture:** 批量合并的核心思路是将多个findings一次性发送给LLM，解析批量决策后统一应用。重试机制在LLM调用层统一处理。

**Tech Stack:** Python asyncio, LLMClient, 正则表达式解析

---

## 文件清单

| 文件 | 变更类型 | 职责 |
|------|---------|------|
| `backend/services/merge_decision_parser.py` | 修改 | 添加批量解析函数 |
| `backend/agent/tools/merge_decider.py` | 修改 | 添加重试机制 |
| `backend/services/task_merge_service.py` | 修改 | 批量合并子代理结果 |
| `backend/services/merge_service.py` | 修改 | 批量合历史结果 |

---

## Task 1: 批量解析器

**Files:**
- Modify: `backend/services/merge_decision_parser.py`
- Test: `backend/tests/test_merge_decision_parser.py`

- [ ] **Step 1: 创建批量解析函数**

在 `merge_decision_parser.py` 末尾添加：

```python
def parse_batch_merge_decisions(text: str, new_findings_keys: list[str]) -> list[dict]:
    """Parse batch natural language decisions from LLM.

    Args:
        text: LLM output with multiple decisions in natural language format
        new_findings_keys: List of requirement_keys for the new findings in order

    Returns:
        List of decision dicts, one per new finding:
        [{
            "action": "keep" | "replace" | "discard" | "keep_both",
            "reason": str,
            "replace_key": str | None,
            "parse_failed": bool
        }, ...]

    If a finding cannot be parsed, returns keep_both for that finding.
    """
    logger.info(f"[parse_batch_merge_decisions] Input text length: {len(text)}, new_findings_keys count: {len(new_findings_keys)}")

    decisions = []
    lines = text.split('\n')

    # Split text into blocks for each finding
    # Pattern: "1. 新发现[序号]" or "新发现[序号]" or numbered list "1.", "2.", etc.
    blocks = []
    current_block = []

    for line in lines:
        # Detect new finding block (numbered pattern)
        if re.match(r'^\d+[\.、]\s*新发现', line) or re.match(r'^新发现\[?\d+\]?', line):
            if current_block:
                blocks.append('\n'.join(current_block))
            current_block = [line]
        elif re.match(r'^\d+[\.、]\s*', line) and current_block and ('决策' in line or '决策' in '\n'.join(current_block[-3:])):
            # Continuation of numbered decision
            if current_block:
                blocks.append('\n'.join(current_block))
            current_block = [line]
        else:
            current_block.append(line)

    if current_block:
        blocks.append('\n'.join(current_block))

    logger.info(f"[parse_batch_merge_decisions] Split into {len(blocks)} blocks")

    # Parse each block
    for i, block in enumerate(blocks):
        key = new_findings_keys[i] if i < len(new_findings_keys) else f"unknown_{i}"
        try:
            decision = parse_merge_decision(block)
            decisions.append(decision)
            logger.info(f"[parse_batch_merge_decisions] Block {i+1} (key={key}): action={decision['action']}")
        except Exception as e:
            logger.warning(f"[parse_batch_merge_decisions] Block {i+1} parse failed: {e}, using keep_both")
            decisions.append({
                "action": "keep_both",
                "reason": f"parse failed: {str(e)}",
                "replace_key": None,
                "parse_failed": True,
            })

    # If we didn't get enough decisions, fill with keep_both
    while len(decisions) < len(new_findings_keys):
        decisions.append({
            "action": "keep_both",
            "reason": "not enough decisions parsed",
            "replace_key": None,
            "parse_failed": True,
        })

    return decisions
```

- [ ] **Step 2: 运行测试确认解析器正常**

Run: `python -c "from backend.services.merge_decision_parser import parse_batch_merge_decisions, parse_merge_decision; print('Import OK')"`
Expected: `Import OK`

- [ ] **Step 3: 提交**

```bash
git add backend/services/merge_decision_parser.py
git commit -m "feat: add batch merge decision parser"
```

---

## Task 2: 重试机制

**Files:**
- Modify: `backend/agent/tools/merge_decider.py`

- [ ] **Step 1: 添加带重试的LLM调用方法**

在 `MergeDeciderTool` 类中添加：

```python
async def _call_llm_with_retry(
    self,
    messages: list,
    max_retries: int = 3,
) -> Any:
    """Call LLM with retry mechanism.

    Args:
        messages: LLM messages
        max_retries: Maximum retry attempts (default 3)

    Returns:
        LLM response

    Raises:
        Exception: If all retries fail
    """
    import asyncio
    import logging
    logger = logging.getLogger(__name__)

    last_error = None
    for attempt in range(max_retries):
        try:
            logger.info(f"[_call_llm_with_retry] Attempt {attempt + 1}/{max_retries}")
            response = await self._llm_client.generate(messages=messages)
            logger.info(f"[_call_llm_with_retry] Success on attempt {attempt + 1}")
            return response
        except Exception as e:
            last_error = e
            logger.warning(f"[_call_llm_with_retry] Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)  # Simple 1 second delay between retries

    logger.error(f"[_call_llm_with_retry] All {max_retries} attempts failed")
    raise last_error
```

- [ ] **Step 2: 修改 `execute` 方法使用重试**

将 `execute` 方法中的：
```python
response = await self._llm_client.generate(messages=messages)
```

替换为：
```python
response = await self._call_llm_with_retry(messages=messages)
```

- [ ] **Step 3: 提交**

```bash
git add backend/agent/tools/merge_decider.py
git commit -m "feat: add retry mechanism to MergeDeciderTool"
```

---

## Task 3: TaskMergeService批量合并

**Files:**
- Modify: `backend/services/task_merge_service.py`

- [ ] **Step 1: 添加批量合并Prompt**

在 `TaskMergeService` 类中添加：

```python
BATCH_MERGE_PROMPT = """你是专业的标书审查结果合并决策专家，负责将多个审查发现与现有发现进行批量合并。

## 决策原则

每次审查都应该被保留，除非新发现与某历史发现**完全重复**（实质内容相同）。

- keep - 保留新发现作为独立条目（新发现描述的是不同的招标要求）
- replace - 用新发现替换某个现有发现（描述的是同一个招标要求，但新发现内容更完整或评估更准确）
- discard - 丢弃新发现（只有当新发现与某现有发现实质内容完全相同时）

## 新发现列表：
{new_findings}

## 现有发现列表：
{existing_findings}

## 输出要求

请对**每个新发现**给出决策，格式如下：

1. 新发现 (requirement_key: xxx)
   决策：keep | replace | discard
   理由：[详细解释为什么做出这个决策，30-100字]
   替换key：[如果决策是replace，填入被替换的requirement_key，否则填"无"]
"""

async def _batch_get_llm_merge_decisions(
    self,
    new_findings: list[dict],
    existing_findings: list[dict],
) -> list[dict]:
    """Call LLM to get batch merge decisions.

    Args:
        new_findings: New findings to merge
        existing_findings: Existing findings already merged

    Returns:
        List of parsed decision dicts
    """
    from backend.services.merge_decision_parser import parse_batch_merge_decisions

    logger.info(f"[_batch_get_llm_merge_decisions] new_findings count={len(new_findings)}, existing count={len(existing_findings)}")

    if not self.agent:
        logger.warning("[_batch_get_llm_merge_decisions] No agent, using keep_both for all")
        return [
            {"action": "keep_both", "reason": "No agent available", "replace_key": None, "parse_failed": True}
            for _ in new_findings
        ]

    # Build prompt
    prompt = BATCH_MERGE_PROMPT.format(
        new_findings=json.dumps(new_findings, ensure_ascii=False, indent=2),
        existing_findings=json.dumps(existing_findings, ensure_ascii=False, indent=2),
    )

    messages = [Message(role="user", content=prompt)]
    new_findings_keys = [f.get("requirement_key", f"unknown_{i}") for i, f in enumerate(new_findings)]

    try:
        logger.info(f"[_batch_get_llm_merge_decisions] Calling LLM...")
        response = await self.agent._call_llm_with_retry(messages=messages)
        logger.info(f"[_batch_get_llm_merge_decisions] LLM response length: {len(response.content)}")

        decisions = parse_batch_merge_decisions(response.content, new_findings_keys)
        logger.info(f"[_batch_get_llm_merge_decisions] Parsed {len(decisions)} decisions")
        return decisions

    except Exception as e:
        logger.warning(f"[_batch_get_llm_merge_decisions] LLM failed: {e}, using keep_both for all")
        return [
            {"action": "keep_both", "reason": f"LLM调用失败: {str(e)}", "replace_key": None, "parse_failed": True}
            for _ in new_findings
        ]
```

**注意**: 需要在文件顶部添加 `import json` 和 `from mini_agent.schema import Message`

- [ ] **Step 2: 修改 `merge_sub_agent_results` 使用批量合并**

找到当前的 `merge_sub_agent_results` 方法中这段逻辑：

```python
# Use LLM to decide merge strategy
all_existing = list(merged_findings)
decision = await self._get_llm_merge_decision(finding, all_existing)
```

替换为批量逻辑。首先收集所有需要LLM决策的findings，然后一次性调用：

```python
# Collect all findings that need LLM decision
findings_to_decide = []
for finding in findings:
    req_key = finding.get("requirement_key", "")

    # Check for same key duplicates first (fast path)
    if req_key and req_key in by_key and len(by_key[req_key]) > 1:
        existing_with_key = [f for f in merged_findings if f.get("requirement_key") == req_key]
        if existing_with_key:
            is_dup = False
            for existing in existing_with_key:
                if self._is_duplicate_content(finding, existing):
                    logger.info(f"[TaskMergeService] Found duplicate content for key={req_key}, discarding")
                    is_dup = True
                    break
            if is_dup:
                continue

    # Check content-based deduplication
    content_key = self._content_hash(finding)
    if content_key in seen_content_keys:
        logger.info(f"[TaskMergeService] Found duplicate content hash, discarding finding with key={req_key}")
        continue

    findings_to_decide.append(finding)

# Batch call LLM for all decisions
if findings_to_decide:
    logger.info(f"[TaskMergeService] Batch calling LLM for {len(findings_to_decide)} findings")
    decisions = await self._batch_get_llm_merge_decisions(findings_to_decide, merged_findings)

    # Apply decisions
    for i, finding in enumerate(findings_to_decide):
        if i >= len(decisions):
            decision = {"action": "keep_both", "reason": "no decision", "replace_key": None}
        else:
            decision = decisions[i]

        req_key = finding.get("requirement_key", "")
        logger.info(f"[TaskMergeService] LLM decision for key={req_key}: action={decision['action']}")

        if decision["action"] == "keep":
            new_key = self._generate_new_requirement_key(merged_findings)
            merged = {**finding, "requirement_key": new_key}
            merged_findings.append(merged)
            seen_content_keys.add(self._content_hash(merged))
            logger.info(f"[TaskMergeService] ACTION=keep: new key={new_key}")
        elif decision["action"] == "replace":
            replace_key = decision.get("replace_key")
            target = None
            if replace_key:
                for rec in merged_findings:
                    if rec.get("requirement_key") == replace_key:
                        target = rec
                        break
            if target:
                idx = merged_findings.index(target)
                new_target = {**target, **{k: v for k, v in finding.items() if k != 'requirement_key'}, "requirement_key": replace_key}
                merged_findings[idx] = new_target
                seen_content_keys.discard(self._content_hash(target))
                seen_content_keys.add(self._content_hash(new_target))
                logger.info(f"[TaskMergeService] ACTION=replace: updated key={replace_key}")
            else:
                new_key = self._generate_new_requirement_key(merged_findings)
                merged = {**finding, "requirement_key": new_key}
                merged_findings.append(merged)
                seen_content_keys.add(self._content_hash(merged))
        elif decision["action"] == "keep_both":
            new_key = self._generate_new_requirement_key(merged_findings)
            merged = {**finding, "requirement_key": new_key}
            merged_findings.append(merged)
            seen_content_keys.add(self._content_hash(merged))
        elif decision["action"] == "discard":
            logger.info(f"[TaskMergeService] ACTION=discard: discarded finding key={req_key}")
```

- [ ] **Step 3: 提交**

```bash
git add backend/services/task_merge_service.py
git commit -m "feat: implement batch merge for TaskMergeService"
```

---

## Task 4: MergeService批量合并

**Files:**
- Modify: `backend/services/merge_service.py`

- [ ] **Step 1: 添加批量合并Prompt和辅助方法**

在 `MergeService` 类中添加：

```python
BATCH_MERGE_PROMPT = """你是专业的标书审查结果合并决策专家，负责将多个审查发现与现有发现进行批量合并。

## 决策原则

每次审查都应该被保留，除非新发现与某历史发现**完全重复**（实质内容相同）。

- keep - 保留新发现作为独立条目（新发现描述的是不同的招标要求）
- replace - 用新发现替换某个现有发现（描述的是同一个招标要求，但新发现内容更完整或评估更准确）
- discard - 丢弃新发现（只有当新发现与某现有发现实质内容完全相同时）

## 新发现列表：
{new_findings}

## 现有发现列表：
{existing_findings}

## 输出要求

请对**每个新发现**给出决策，格式如下：

1. 新发现 (requirement_key: xxx)
   决策：keep | replace | discard
   理由：[详细解释为什么做出这个决策，30-100字]
   替换key：[如果决策是replace，填入被替换的requirement_key，否则填"无"]
"""

async def _batch_get_llm_merge_decisions(
    self,
    new_findings: list[dict],
    existing_findings: list[dict],
) -> list[dict]:
    """Call LLM to get batch merge decisions.

    Args:
        new_findings: New findings to merge
        existing_findings: Existing findings already merged

    Returns:
        List of parsed decision dicts
    """
    from backend.services.merge_decision_parser import parse_batch_merge_decisions

    logger.info(f"[_batch_get_llm_merge_decisions] new_findings count={len(new_findings)}, existing count={len(existing_findings)}")

    if not self.agent:
        logger.warning("[_batch_get_llm_merge_decisions] No agent, using keep_both for all")
        return [
            {"action": "keep_both", "reason": "No agent available", "replace_key": None, "parse_failed": True}
            for _ in new_findings
        ]

    # Build prompt
    prompt = BATCH_MERGE_PROMPT.format(
        new_findings=json.dumps(new_findings, ensure_ascii=False, indent=2),
        existing_findings=json.dumps(existing_findings, ensure_ascii=False, indent=2),
    )

    messages = [Message(role="user", content=prompt)]
    new_findings_keys = [f.get("requirement_key", f"unknown_{i}") for i, f in enumerate(new_findings)]

    try:
        logger.info(f"[_batch_get_llm_merge_decisions] Calling LLM...")
        response = await self.agent._call_llm_with_retry(messages=messages)
        logger.info(f"[_batch_get_llm_merge_decisions] LLM response length: {len(response.content)}")

        decisions = parse_batch_merge_decisions(response.content, new_findings_keys)
        logger.info(f"[_batch_get_llm_merge_decisions] Parsed {len(decisions)} decisions")
        return decisions

    except Exception as e:
        logger.warning(f"[_batch_get_llm_merge_decisions] LLM failed: {e}, using keep_both for all")
        return [
            {"action": "keep_both", "reason": f"LLM调用失败: {str(e)}", "replace_key": None, "parse_failed": True}
            for _ in new_findings
        ]
```

**注意**: 需要在文件顶部添加 `import json` 和 `from mini_agent.schema import Message`

- [ ] **Step 2: 修改 `merge_project_results` 使用批量合并**

在 `merge_project_results` 方法中，找到这个循环：

```python
for new_result in latest_results:
    ...
    decision = await self._get_llm_merge_decision(new_result, all_existing)
    ...
```

将其替换为批量调用逻辑。在处理最新结果之前，先做快速重复检查，然后批量调用LLM。

实际上，这个方法的逻辑比较复杂，需要仔细重构。核心思路是：
1. 先收集所有需要LLM决策的新结果（排除快速检查已确定discard的）
2. 一次性调用 `_batch_get_llm_merge_decisions`
3. 根据返回的批量决策列表统一应用结果

**关键变更代码段**：

```python
# 先做快速检查，收集需要LLM决策的结果
findings_to_decide = []
findings_to_discard = []

for new_result in latest_results:
    req_key = new_result.get("requirement_key", "")

    if req_key in existing_by_key:
        existing = existing_by_key[req_key]
        if self._is_duplicate_content(new_result, existing):
            logger.info(f"[merge] key={req_key}: 发现实质内容重复，自动 discard")
            findings_to_discard.append(new_result)
            continue

    findings_to_decide.append(new_result)

# 批量调用 LLM
all_existing = list(new_merged_records)
if findings_to_decide:
    decisions = await self._batch_get_llm_merge_decisions(findings_to_decide, all_existing)

    # 应用决策
    for i, new_result in enumerate(findings_to_decide):
        if i >= len(decisions):
            decision = {"action": "keep_both", "reason": "no decision", "replace_key": None}
        else:
            decision = decisions[i]

        req_key = new_result.get("requirement_key", "")
        logger.info(f"[merge] LLM decision for key={req_key}: action={decision['action']}")

        # ... 应用决策的逻辑（与原来相同）...
```

- [ ] **Step 3: 提交**

```bash
git add backend/services/merge_service.py
git commit -m "feat: implement batch merge for MergeService"
```

---

## Task 5: 验证测试

**Files:**
- Test: `backend/tests/test_merge_decision_parser.py`

- [ ] **Step 1: 添加批量解析测试**

```python
def test_parse_batch_merge_decisions_success():
    """Test parsing batch merge decisions with valid input."""
    text = """
1. 新发现 (requirement_key: req_001)
   决策：keep
   理由：新发现与现有发现描述的是不同的招标要求，应该作为独立条目保留。
   替换key：无

2. 新发现 (requirement_key: req_002)
   决策：replace
   理由：新发现与req_003描述同一招标要求，但新发现的内容更完整。
   替换key：req_003

3. 新发现 (requirement_key: req_004)
   决策：discard
   理由：新发现与req_005实质内容完全相同，应丢弃。
   替换key：无
"""
    new_findings_keys = ["req_001", "req_002", "req_004"]
    decisions = parse_batch_merge_decisions(text, new_findings_keys)

    assert len(decisions) == 3
    assert decisions[0]["action"] == "keep"
    assert decisions[0]["parse_failed"] == False
    assert decisions[1]["action"] == "replace"
    assert decisions[1]["replace_key"] == "req_003"
    assert decisions[2]["action"] == "discard"
    assert decisions[2]["parse_failed"] == False


def test_parse_batch_merge_decisions_partial_failure():
    """Test parsing when some blocks fail to parse."""
    text = """
1. 新发现 (requirement_key: req_001)
   决策：keep
   理由：这是一个有效的决策块。
   替换key：无

2. 新发现 (requirement_key: req_002)
   这个块格式不正确，没有决策字段
"""
    new_findings_keys = ["req_001", "req_002"]
    decisions = parse_batch_merge_decisions(text, new_findings_keys)

    assert len(decisions) == 2
    assert decisions[0]["action"] == "keep"
    assert decisions[0]["parse_failed"] == False
    assert decisions[1]["action"] == "keep_both"  # Fallback
    assert decisions[1]["parse_failed"] == True


def test_parse_batch_merge_decisions_less_findings_than_keys():
    """Test when there are fewer findings than keys."""
    text = """
1. 新发现 (requirement_key: req_001)
   决策：keep
   理由：只有一条有效决策。
   替换key：无
"""
    new_findings_keys = ["req_001", "req_002", "req_003"]
    decisions = parse_batch_merge_decisions(text, new_findings_keys)

    assert len(decisions) == 3
    assert decisions[0]["action"] == "keep"
    assert decisions[1]["action"] == "keep_both"  # Filled with keep_both
    assert decisions[2]["action"] == "keep_both"  # Filled with keep_both
```

- [ ] **Step 2: 运行测试**

Run: `cd /home/openclaw/bjt_agent && python -m pytest backend/tests/test_merge_decision_parser.py -v`
Expected: All tests pass

- [ ] **Step 3: 提交**

```bash
git add backend/tests/test_merge_decision_parser.py
git commit -m "test: add batch merge decision parser tests"
```

---

## 自检清单

- [ ] Task 1-5 的代码变更符合设计文档
- [ ] 没有 placeholder 或 TODO
- [ ] 类型一致性检查：方法签名、返回类型一致
- [ ] 提交信息遵循 conventional commits 格式
