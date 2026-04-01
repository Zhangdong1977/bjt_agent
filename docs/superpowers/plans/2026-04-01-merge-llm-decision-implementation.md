# LLM 合并决策实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将合并逻辑从 embedding 相似度判断改为 LLM 决策判断，复用 BidReviewAgent 的 LLM client。

**Architecture:** 新增 MergeDeciderTool 和决策解析器，修改 MergeService 使用 LLM 决策替代 embedding 相似度。

**Tech Stack:** Python, SQLAlchemy, Mini-Max API, Celery

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/services/merge_decision_parser.py` | 新增 | LLM 自然语言决策解析器 |
| `backend/agent/tools/merge_decider.py` | 新增 | MergeDeciderTool，调用 LLM 进行合并决策 |
| `backend/agent/tools/__init__.py` | 修改 | 导出 MergeDeciderTool |
| `backend/agent/prompt.py` | 修改 | 增加 MergeDecider 提示词 |
| `backend/agent/bid_review_agent.py` | 修改 | 注册 MergeDeciderTool，新增 decide_merge 方法 |
| `backend/services/merge_service.py` | 修改 | 使用 LLM 决策替代 embedding |
| `backend/tests/test_merge_decision_parser.py` | 新增 | 解析器单元测试 |
| `backend/tests/test_merge_service.py` | 新增 | 合并服务单元测试 |

---

## Task 1: 实现 merge_decision_parser.py

**Files:**
- Create: `backend/services/merge_decision_parser.py`
- Test: `backend/tests/test_merge_decision_parser.py`

- [ ] **Step 1: 编写解析器测试**

```python
# backend/tests/test_merge_decision_parser.py
import pytest
from backend.services.merge_decision_parser import parse_merge_decision

class TestParseMergeDecision:
    def test_parse_keep(self):
        text = """决策：keep
理由：这是一个全新的发现，与现有所有发现都不重复。
替换key：无"""
        result = parse_merge_decision(text)
        assert result["action"] == "keep"
        assert result["parse_failed"] is False

    def test_parse_replace(self):
        text = """决策：replace
理由：新发现的位置信息更精确，severity更高。
替换key：req_001"""
        result = parse_merge_decision(text)
        assert result["action"] == "replace"
        assert result["replace_key"] == "req_001"

    def test_parse_discard(self):
        text = """决策：discard
理由：新发现与现有发现完全重复，没有提供任何新信息。
替换key：无"""
        result = parse_merge_decision(text)
        assert result["action"] == "discard"

    def test_parse_failure_fallback(self):
        text = "这是一段无法解析的文本"
        result = parse_merge_decision(text)
        assert result["action"] == "keep_both"
        assert result["parse_failed"] is True

    def test_parse_case_insensitive(self):
        text = """决策：KEEP
理由：测试
替换key：无"""
        result = parse_merge_decision(text)
        assert result["action"] == "keep"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/openclaw/bjt_agent && python -m pytest backend/tests/test_merge_decision_parser.py -v`
Expected: FAIL - module not found

- [ ] **Step 3: 编写解析器实现**

```python
# backend/services/merge_decision_parser.py
"""Parser for LLM merge decision output."""
import re
import logging

logger = logging.getLogger(__name__)


def parse_merge_decision(text: str) -> dict:
    """Parse natural language decision from LLM.

    Args:
        text: LLM output in natural language format

    Returns:
        {
            "action": "keep" | "replace" | "discard" | "keep_both",
            "reason": str,
            "replace_key": str | None,
            "parse_failed": bool
        }

    On parse failure, returns keep_both strategy:
        {
            "action": "keep_both",
            "reason": "parse failed",
            "replace_key": None,
            "parse_failed": True
        }
    """
    try:
        # Extract decision (case-insensitive)
        decision_match = re.search(r'决策\s*[:：]\s*(\w+)', text, re.IGNORECASE)
        if not decision_match:
            raise ValueError("Cannot find decision field")

        decision = decision_match.group(1).lower().strip()

        # Validate decision
        valid_decisions = {"keep", "replace", "discard"}
        if decision not in valid_decisions:
            raise ValueError(f"Invalid decision: {decision}")

        # Extract reason
        reason_match = re.search(r'理由\s*[:：]\s*(.+?)(?=\n替换key|替换key|$)', text, re.DOTALL)
        reason = reason_match.group(1).strip() if reason_match else ""

        # Extract replace key
        replace_key = None
        if decision == "replace":
            key_match = re.search(r'替换key\s*[:：]\s*(\S+)', text)
            replace_key = key_match.group(1) if key_match else None

        return {
            "action": decision,
            "reason": reason,
            "replace_key": replace_key,
            "parse_failed": False,
        }

    except Exception as e:
        logger.warning(f"Failed to parse merge decision: {e}, text: {text[:200]}")
        return {
            "action": "keep_both",
            "reason": f"解析失败: {str(e)}",
            "replace_key": None,
            "parse_failed": True,
        }
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/openclaw/bjt_agent && python -m pytest backend/tests/test_merge_decision_parser.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/merge_decision_parser.py backend/tests/test_merge_decision_parser.py
git commit -m "feat: add merge decision parser for LLM output

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: 实现 MergeDeciderTool

**Files:**
- Create: `backend/agent/tools/merge_decider.py`
- Modify: `backend/agent/tools/__init__.py`
- Test: 集成测试，无独立单元测试

- [ ] **Step 1: 创建 merge_decider.py**

```python
# backend/agent/tools/merge_decider.py
"""Merge decider tool for LLM-powered merge decisions."""

import json
from typing import Any

from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message
from mini_agent.tools.base import Tool as BaseTool, ToolResult

from backend.config import get_settings

settings = get_settings()

# Prompt for merge decision
MERGE_DECISION_PROMPT = """你是专业的标书审查结果合并决策专家。

给定一个新发现和一系列已存在的发现，请判断：
1. keep - 保留新发现（与现有所有发现都不重复或新发现信息更丰富）
2. replace - 用新发现替换某个现有发现（新发现更完整/severity更高/位置更精确）
3. discard - 丢弃新发现（与某现有发现重复且没有提供任何新信息）

## 新发现：
{new_finding}

## 现有发现列表：
{existing_findings}

## 输出格式（自然语言，必须包含）：
决策：keep | replace | discard
理由：[详细解释为什么做出这个决策，50-200字]
替换key：[如果决策是replace，填入被替换的 requirement_key，否则填"无"]
"""


class MergeDeciderTool(BaseTool):
    """Tool for LLM to decide merge strategy between findings."""

    def __init__(self):
        """Initialize the merge decider tool."""
        super().__init__()
        self._llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )

    @property
    def name(self) -> str:
        return "decide_merge"

    @property
    def description(self) -> str:
        return """决定新发现与历史发现的合并策略。

输入 JSON:
- new_finding: 新发现的完整信息
- existing_findings: 现有发现列表

输出自然语言决策，包含：决策、理由、替换key"""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "new_finding": {
                    "type": "object",
                    "description": "新发现的完整信息",
                },
                "existing_findings": {
                    "type": "array",
                    "description": "现有发现列表",
                },
            },
            "required": ["new_finding", "existing_findings"],
        }

    async def execute(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> ToolResult:
        """Execute merge decision via LLM.

        Args:
            new_finding: 新发现
            existing_findings: 现有发现列表

        Returns:
            ToolResult with natural language decision
        """
        try:
            # Build prompt
            prompt = MERGE_DECISION_PROMPT.format(
                new_finding=json.dumps(new_finding, ensure_ascii=False, indent=2),
                existing_findings=json.dumps(existing_findings, ensure_ascii=False, indent=2),
            )

            messages = [
                Message(role="user", content=prompt),
            ]

            response = await self._llm_client.generate(messages=messages)

            return ToolResult(
                success=True,
                content=response.content,
                data={"decision": response.content},
            )

        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))
```

- [ ] **Step 2: 更新 __init__.py**

```python
# backend/agent/tools/__init__.py
from .base import ToolResult
from .doc_search import DocSearchTool
from .rag_search import RAGSearchTool
from .comparator import ComparatorTool
from .merge_decider import MergeDeciderTool

__all__ = ["ToolResult", "DocSearchTool", "RAGSearchTool", "ComparatorTool", "MergeDeciderTool"]
```

- [ ] **Step 3: 验证导入**

Run: `cd /home/openclaw/bjt_agent && python -c "from backend.agent.tools import MergeDeciderTool; print('OK')"`
Expected: OK

- [ ] **Step 4: 提交**

```bash
git add backend/agent/tools/merge_decider.py backend/agent/tools/__init__.py
git commit -m "feat: add MergeDeciderTool for LLM-powered merge decisions

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: 修改 prompt.py

**Files:**
- Modify: `backend/agent/prompt.py`

- [ ] **Step 1: 查看当前 prompt.py 结尾**

Run: `tail -20 /home/openclaw/bjt_agent/backend/agent/prompt.py`

- [ ] **Step 2: 追加 MergeDecider 提示词**

在 `prompt.py` 文件末尾添加：

```python
# Merge decision prompt (used by MergeDeciderTool)
MERGE_DECISION_PROMPT = """你是专业的标书审查结果合并决策专家。

给定一个新发现和一系列已存在的发现，请判断：
1. keep - 保留新发现（与现有所有发现都不重复或新发现信息更丰富）
2. replace - 用新发现替换某个现有发现（新发现更完整/severity更高/位置更精确）
3. discard - 丢弃新发现（与某现有发现重复且没有提供任何新信息）

## 新发现：
{new_finding}

## 现有发现列表：
{existing_findings}

## 输出格式（自然语言，必须包含）：
决策：keep | replace | discard
理由：[详细解释为什么做出这个决策，50-200字]
替换key：[如果决策是replace，填入被替换的 requirement_key，否则填"无"]
"""
```

注意：`merge_decider.py` 中已包含提示词定义，此步骤可选（保持提示词在工具类内部）。

- [ ] **Step 3: 提交**

```bash
git add backend/agent/prompt.py
git commit -m "docs: add merge decision prompt to agent prompts

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: 修改 BidReviewAgent

**Files:**
- Modify: `backend/agent/bid_review_agent.py:70-88`
- Modify: 新增 decide_merge 方法

- [ ] **Step 1: 查看 tools 注册位置**

Run: `sed -n '70,88p' /home/openclaw/bjt_agent/backend/agent/bid_review_agent.py`

- [ ] **Step 2: 添加 MergeDeciderTool 到 tools 列表**

在 `tools = [` 列表中添加 `MergeDeciderTool(),`

- [ ] **Step 3: 在文件末尾添加 decide_merge 方法**

```python
    async def decide_merge(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> str:
        """调用 LLM 进行合并决策。

        Args:
            new_finding: 新发现的完整信息
            existing_findings: 现有发现列表

        Returns:
            LLM 自然语言决策结果
        """
        from backend.agent.tools import MergeDeciderTool

        tool = MergeDeciderTool()
        result = await tool.execute(new_finding, existing_findings)

        if not result.success:
            raise RuntimeError(f"Merge decider failed: {result.error}")

        return result.content
```

- [ ] **Step 4: 验证语法**

Run: `cd /home/openclaw/bjt_agent && python -c "from backend.agent.bid_review_agent import BidReviewAgent; print('OK')"`
Expected: OK

- [ ] **Step 5: 提交**

```bash
git add backend/agent/bid_review_agent.py
git commit -m "feat: register MergeDeciderTool and add decide_merge method

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: 修改 MergeService

**Files:**
- Modify: `backend/services/merge_service.py`
- Test: `backend/tests/test_merge_service.py`

- [ ] **Step 1: 编写 MergeService 测试**

```python
# backend/tests/test_merge_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.services.merge_service import MergeService


class TestMergeServiceLLMDecision:
    """Test MergeService with LLM-based merge decisions."""

    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.decide_merge = AsyncMock()
        return agent

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_llm_decision_keep(self, mock_agent, mock_db):
        """When LLM says keep, new finding should be added."""
        # Setup
        mock_agent.decide_merge.return_value = "决策：keep\n理由：新发现是全新的\n替换key：无"

        service = MergeService(mock_db, mock_agent)

        new_finding = {
            "requirement_key": "req_001",
            "requirement_content": "新要求内容",
        }

        # Execute
        decision = await service._get_llm_merge_decision(new_finding, [])

        # Verify
        assert decision["action"] == "keep"
        assert decision["parse_failed"] is False

    @pytest.mark.asyncio
    async def test_llm_decision_replace(self, mock_agent, mock_db):
        """When LLM says replace, should identify the key to replace."""
        mock_agent.decide_merge.return_value = "决策：replace\n理由：新发现更完整\n替换key：req_001"

        service = MergeService(mock_db, mock_agent)

        decision = await service._get_llm_merge_decision({}, [])

        assert decision["action"] == "replace"
        assert decision["replace_key"] == "req_001"

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self, mock_agent, mock_db):
        """When LLM call fails, should use keep_both strategy."""
        mock_agent.decide_merge.side_effect = Exception("LLM API error")

        service = MergeService(mock_db, mock_agent)

        decision = await service._get_llm_merge_decision({}, [])

        assert decision["action"] == "keep_both"
        assert decision["parse_failed"] is True
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/openclaw/bjt_agent && python -m pytest backend/tests/test_merge_service.py -v`
Expected: FAIL - module not found / method not found

- [ ] **Step 3: 修改 MergeService**

修改 `backend/services/merge_service.py`:

1. 构造函数改为接受 `agent: BidReviewAgent` 参数：

```python
class MergeService:
    def __init__(self, db: AsyncSession, agent: BidReviewAgent = None):
        self.db = db
        self.agent = agent
```

2. 新增 `_get_llm_merge_decision` 方法：

```python
    async def _get_llm_merge_decision(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> dict:
        """调用 LLM 获取合并决策。

        Args:
            new_finding: 新发现
            existing_findings: 现有发现列表

        Returns:
            解析后的决策字典
        """
        from backend.services.merge_decision_parser import parse_merge_decision

        try:
            decision_text = await self.agent.decide_merge(new_finding, existing_findings)
            return parse_merge_decision(decision_text)
        except Exception as e:
            logger.warning(f"LLM merge decision failed: {e}, using keep_both strategy")
            return {
                "action": "keep_both",
                "reason": f"LLM调用失败: {str(e)}",
                "replace_key": None,
                "parse_failed": True,
            }
```

3. 修改 `merge_project_results` 方法中的合并逻辑（替换现有的 embedding 相似度逻辑）：

将现有的：
```python
if req_key in existing_by_key:
    existing = existing_by_key[req_key]
    merged_record, is_duplicate = await self._check_and_merge(
        existing, new_result, SIMILARITY_THRESHOLD
    )
    if is_duplicate:
        merge_count += 1
        ...
```

替换为：
```python
if req_key in existing_by_key:
    existing = existing_by_key[req_key]

    # 使用 LLM 决策
    decision = await self._get_llm_merge_decision(
        new_result,
        [existing]
    )

    if decision["action"] == "keep":
        new_result["merged_from_count"] = 2
        new_merged_records.append(new_result)
        matched_keys.add(req_key)
        merge_count += 1
    elif decision["action"] == "replace":
        existing.update(new_result)
        existing["merged_from_count"] = existing.get("merged_from_count", 1) + 1
        new_merged_records.append(existing)
        matched_keys.add(req_key)
        merge_count += 1
    elif decision["action"] == "discard":
        new_merged_records.append(existing)
        matched_keys.add(req_key)
    elif decision["action"] == "keep_both":
        new_result["merged_from_count"] = 1
        existing["merged_from_count"] = existing.get("merged_from_count", 1)
        new_merged_records.append(existing)
        new_merged_records.append(new_result)
        matched_keys.add(req_key)
```

4. 删除不再使用的 `SIMILARITY_THRESHOLD` 常量和 `_check_and_merge` 方法。

- [ ] **Step 4: 运行测试验证**

Run: `cd /home/openclaw/bjt_agent && python -m pytest backend/tests/test_merge_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/merge_service.py backend/tests/test_merge_service.py
git commit -m "feat: use LLM decision instead of embedding for merge

Replaces embedding-based similarity check with LLM-powered merge
decisions in MergeService. Falls back to keep_both on parse failure.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: 修改 review_tasks.py 调用

**Files:**
- Modify: `backend/tasks/review_tasks.py:221-246`

- [ ] **Step 1: 查看当前 merge_review_results 实现**

Run: `sed -n '221,246p' /home/openclaw/bjt_agent/backend/tasks/review_tasks.py`

- [ ] **Step 2: 修改 merge_review_results 创建 BidReviewAgent**

在 `_run_merge()` 函数中，创建 BidReviewAgent 并传给 MergeService：

```python
async def _run_merge():
    session_factory = create_session_factory()
    async with session_factory() as db:
        from backend.services.merge_service import MergeService
        from backend.agent.bid_review_agent import BidReviewAgent

        # Create a minimal agent for merge decisions
        # We need the agent's LLM client, not the full review process
        agent = BidReviewAgent(
            project_id=project_id,
            tender_doc_path="",  # Not needed for merge decisions
            bid_doc_path="",     # Not needed for merge decisions
            user_id="system",
            event_callback=None,
            max_steps=1,
        )

        merge_service = MergeService(db, agent)
        merged_count, total_count = await merge_service.merge_project_results(
            project_id=project_id,
            latest_task_id=latest_task_id,
            event_callback=event_cb,
        )
        return {"status": "success", "merged_count": merged_count, "total_count": total_count}
```

注意：BidReviewAgent 初始化需要有效的 tender_doc_path 和 bid_doc_path，否则会抛出异常。需要调整实现使其不依赖文档路径。

- [ ] **Step 3: 调整 BidReviewAgent 以支持无文档初始化**

修改 `BidReviewAgent.__init__` 接受空路径：

```python
# In BidReviewAgent.__init__, around line 78:
if tender_doc_path and Path(tender_doc_path).exists():
    self.tender_doc_path = tender_doc_path
else:
    self.tender_doc_path = None

if bid_doc_path and Path(bid_doc_path).exists():
    self.bid_doc_path = bid_doc_path
else:
    self.bid_doc_path = None
```

- [ ] **Step 4: 验证修改**

Run: `cd /home/openclaw/bjt_agent && python -c "from backend.tasks.review_tasks import merge_review_results; print('OK')"`
Expected: OK

- [ ] **Step 5: 提交**

```bash
git add backend/tasks/review_tasks.py backend/agent/bid_review_agent.py
git commit -m "feat: pass BidReviewAgent to MergeService for LLM decisions

Adjusts BidReviewAgent to support initialization without valid doc paths
for merge decision use case.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: 完整集成验证

- [ ] **Step 1: 检查所有修改文件状态**

Run: `git status`
Expected: 显示修改的文件

- [ ] **Step 2: 运行所有相关测试**

Run: `cd /home/openclaw/bjt_agent && python -m pytest backend/tests/test_merge_decision_parser.py backend/tests/test_merge_service.py -v`
Expected: PASS

- [ ] **Step 3: 验证导入链完整性**

Run: `cd /home/openclaw/bjt_agent && python -c "
from backend.services.merge_service import MergeService
from backend.agent.tools.merge_decider import MergeDeciderTool
from backend.agent.bid_review_agent import BidReviewAgent
from backend.services.merge_decision_parser import parse_merge_decision
print('All imports OK')
"`
Expected: All imports OK

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "feat: complete LLM merge decision implementation

Integration of all components:
- MergeDeciderTool for LLM-powered decisions
- parse_merge_decision for output parsing
- Updated MergeService to use LLM decisions
- BidReviewAgent support for merge decisions

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## 实施后检查清单

- [ ] 所有测试通过
- [ ] 代码可以正常导入
- [ ] MergeService 正确使用 LLM 决策
- [ ] 解析失败时正确回退到 keep_both
- [ ] 没有引入新的 lint 错误
