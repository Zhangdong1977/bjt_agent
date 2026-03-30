# 文档搜索工具输出优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化 `DocSearchTool` 在时间线上的输出，对非技术用户更友好

**Architecture:** 修改 `backend/agent/tools/doc_search.py` 中的返回格式，根据请求类型（full_content/query/none）返回不同格式。添加 `_extract_summary()` 方法进行内容摘要提取。

**Tech Stack:** Python, pytest, DocSearchTool

---

## 文件结构

- **修改:** `backend/agent/tools/doc_search.py`
- **测试:** `backend/tests/test_doc_search.py` (如不存在则创建)

---

## 实现任务

### Task 1: 添加 `_extract_summary()` 方法

**Files:**
- Modify: `backend/agent/tools/doc_search.py:48-58` (在 `_find_line_around` 方法后添加)

- [ ] **Step 1: 添加 _extract_summary 方法**

在 `DocSearchTool` 类中添加以下方法（在 `_find_line_around` 方法之后）：

```python
def _extract_summary(self, content: str) -> str:
    """Extract a structured summary from document content.

    Returns a human-friendly summary with categorized sections.
    """
    lines = content.split('\n')
    summary_parts = []

    # Define category patterns
    categories = {
        "技术": {"patterns": [r"技术", r"Python", r"Vue", r"FastAPI", r"开发"], "icon": "🛠️"},
        "工期": {"patterns": [r"工期", r"时间", r"交付", r"完成"], "icon": "⏱️"},
        "预算": {"patterns": [r"预算", r"价格", r"万", r"元", r"费用", r"成本"], "icon": "💰"},
        "资质": {"patterns": [r"资质", r"证书", r"认证", r"ISO", r"CMMI"], "icon": "📋"},
    }

    # Find matching lines for each category
    categorized_lines = {cat: [] for cat in categories}

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped or len(line_stripped) < 5:
            continue
        for cat_name, cat_info in categories.items():
            for pattern in cat_info["patterns"]:
                if re.search(pattern, line_stripped, re.IGNORECASE):
                    categorized_lines[cat_name].append(line_stripped[:150])
                    break

    # Build summary
    for cat_name, cat_info in categories.items():
        lines_for_cat = categorized_lines[cat_name][:3]  # Max 3 lines per category
        if lines_for_cat:
            summary_parts.append(f"\n{cat_info['icon']} {cat_name}要求")
            for l in lines_for_cat:
                summary_parts.append(f"• {l}")

    if not summary_parts:
        # Fallback: first few non-empty lines
        summary_parts.append("\n📝 文档内容")
        for line in lines[:5]:
            if line.strip():
                summary_parts.append(f"• {line.strip()[:100]}")

    return "\n".join(summary_parts)
```

- [ ] **Step 2: 运行测试验证文件语法正确**

Run: `python -m py_compile backend/agent/tools/doc_search.py`
Expected: 无输出（成功）

- [ ] **Step 3: 提交**

```bash
git add backend/agent/tools/doc_search.py
git commit -m "feat(doc_search): add _extract_summary method for content categorization"
```

---

### Task 2: 修改 full_content 返回格式

**Files:**
- Modify: `backend/agent/tools/doc_search.py:181-192` (原 `if full_content:` 分支)

- [ ] **Step 1: 修改 full_content 分支的返回格式**

将原来的：
```python
if full_content:
    content = "\n".join(lines)
    if len(content) > self.chunk_size * 3:
        content = self._chunk_content(content, chunk)
        if chunk > 0:
            content = f"[... Chunk {chunk} ...]\n{content}"
    return ToolResult(
        success=True,
        content=content,
        data={"line_count": len(lines), "chunk": chunk},
    )
```

修改为：
```python
if full_content:
    full_text = "\n".join(lines)
    if len(full_text) > self.chunk_size * 3:
        full_text = self._chunk_content(full_text, chunk)
        if chunk > 0:
            full_text = f"[... Chunk {chunk} ...]\n{full_text}"

    # Generate friendly summary
    summary = self._extract_summary(full_text)
    doc_label = "招标" if doc_type == "tender" else "投标"

    friendly_content = f"""📄 {doc_label}书内容摘要

这份{doc_label}书共 {len(lines)} 行，内容如下：

{summary}

[完整文档已加载]"""

    return ToolResult(
        success=True,
        content=friendly_content,
        data={"line_count": len(lines), "chunk": chunk, "full_content": full_text},
    )
```

- [ ] **Step 2: 运行测试验证**

Run: `python -c "from backend.agent.tools.doc_search import DocSearchTool; print('OK')"`
Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add backend/agent/tools/doc_search.py
git commit -m "feat(doc_search): return friendly summary for full_content requests"
```

---

### Task 3: 修改 keyword 搜索返回格式

**Files:**
- Modify: `backend/agent/tools/doc_search.py:194-223` (原 `if query:` 分支)

- [ ] **Step 1: 修改 query 搜索的返回格式**

将原来的：
```python
if query:
    matches = self._search_by_keyword(lines, query)

    if not matches:
        return ToolResult(
            success=True,
            content=f"No content matching '{query}' found in {doc_type} document.",
            data={"query": query, "matches": 0},
        )

    # Format results with context
    formatted = [f"Found {len(matches)} matches for '{query}':\n"]
    for m in matches[:MAX_LINES_PER_QUERY]:
        formatted.append(f"Line {m['line_number']}: {m['line_content']}")
        if m.get('context_before'):
            formatted.append(f"  <- {m['context_before']}")
        if m.get('context_after'):
            formatted.append(f"  -> {m['context_after']}")
        formatted.append("")

    return ToolResult(
        success=True,
        content="\n".join(formatted),
        data={
            "query": query,
            "matches": len(matches),
            "results": matches,
        },
    )
```

修改为：
```python
if query:
    matches = self._search_by_keyword(lines, query)
    doc_label = "招标" if doc_type == "tender" else "投标"

    if not matches:
        return ToolResult(
            success=True,
            content=f"抱歉，未在{doc_label}书中找到与\"{query}\"相关的内容。",
            data={"query": query, "matches": 0},
        )

    # Format results in friendly style
    formatted_lines = [
        f"🔍 在{doc_label}书中找到 **{len(matches)}** 处提到\"{query}\"：\n"
    ]
    for i, m in enumerate(matches[:10], 1):  # Show max 10 matches
        formatted_lines.append(f"{i}. {m['line_content'][:100]}")
        if m.get('context_before'):
            formatted_lines.append(f"   ↳ 上文: {m['context_before'][:50]}")
        if m.get('context_after'):
            formatted_lines.append(f"   ↳ 下文: {m['context_after'][:50]}")

    if len(matches) > 10:
        formatted_lines.append(f"\n... 还有 {len(matches) - 10} 处匹配")

    return ToolResult(
        success=True,
        content="\n".join(formatted_lines),
        data={
            "query": query,
            "matches": len(matches),
            "results": matches,
        },
    )
```

- [ ] **Step 2: 运行测试验证**

Run: `python -c "from backend.agent.tools.doc_search import DocSearchTool; print('OK')"`
Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add backend/agent/tools/doc_search.py
git commit -m "feat(doc_search): improve keyword search output format"
```

---

### Task 4: 优化无参数时的返回格式

**Files:**
- Modify: `backend/agent/tools/doc_search.py:225-234` (原无参数分支)

- [ ] **Step 1: 修改无参数请求的返回格式**

将原来的：
```python
# No query, return document info
return ToolResult(
    success=True,
    content=f"{doc_type.capitalize()} document loaded: {len(lines)} lines",
    data={
        "line_count": len(lines),
        "doc_type": doc_type,
        "path": str(doc_path),
    },
)
```

修改为：
```python
# No query, return document info
doc_label = "招标" if doc_type == "tender" else "投标"
return ToolResult(
    success=True,
    content=f"📄 已加载{doc_label}书，共 {len(lines)} 行。",
    data={
        "line_count": len(lines),
        "doc_type": doc_type,
        "path": str(doc_path),
    },
)
```

- [ ] **Step 2: 提交**

```bash
git add backend/agent/tools/doc_search.py
git commit -m "feat(doc_search): improve no-query response format"
```

---

### Task 5: 整体测试验证

**Files:**
- Test: 创建测试文件 `backend/tests/test_doc_search_output.py`

- [ ] **Step 1: 创建测试文件**

创建 `backend/tests/test_doc_search_output.py`：

```python
"""Tests for DocSearchTool friendly output format."""

import pytest
import tempfile
from pathlib import Path
from backend.agent.tools.doc_search import DocSearchTool


class TestDocSearchOutput:
    """Test output format improvements."""

    @pytest.fixture
    def temp_doc(self, tmp_path):
        """Create a temporary test document."""
        content = """招标书测试

招标范围：软件开发服务
技术要求： Python 3.8+, Vue 3, FastAPI
工期要求： 6 个月
预算范围： 100-200 万

其他要求：需要有相关资质证书
"""
        doc_path = tmp_path / "test_tender.md"
        doc_path.write_text(content, encoding="utf-8")
        return doc_path

    @pytest.fixture
    def search_tool(self, temp_doc, tmp_path):
        """Create DocSearchTool instance."""
        return DocSearchTool(
            tender_doc_path=str(temp_doc),
            bid_doc_path=str(tmp_path / "bid.md"),
        )

    @pytest.mark.asyncio
    async def test_full_content_returns_summary(self, search_tool):
        """full_content should return friendly summary, not '0 matches'."""
        result = await search_tool.execute(doc_type="tender", full_content=True)

        assert result.success is True
        # Should NOT contain "0" as "found matches"
        assert "找到 0 条" not in result.content
        # Should contain document summary indicators
        assert "内容摘要" in result.content or "📄" in result.content
        # Should contain line count
        assert "行" in result.content

    @pytest.mark.asyncio
    async def test_keyword_search_returns_count(self, search_tool):
        """keyword search should show match count."""
        result = await search_tool.execute(doc_type="tender", query="技术")

        assert result.success is True
        # Should show count
        assert "找到" in result.content and "处" in result.content
        # Should NOT say "Found 0 matches"
        assert "Found 0" not in result.content

    @pytest.mark.asyncio
    async def test_keyword_no_match_friendly_message(self, search_tool):
        """No matches should show friendly message."""
        result = await search_tool.execute(doc_type="tender", query="不存在的关键词")

        assert result.success is True
        assert "抱歉" in result.content or "未找到" in result.content
        # Should NOT be technical "No content matching"
        assert "No content matching" not in result.content

    @pytest.mark.asyncio
    async def test_no_param_returns_line_count(self, search_tool):
        """No param request should show friendly message."""
        result = await search_tool.execute(doc_type="tender")

        assert result.success is True
        assert "已加载" in result.content or "📄" in result.content
        assert "行" in result.content
```

- [ ] **Step 2: 运行测试**

Run: `cd /home/openclaw/bjt_agent && python -m pytest backend/tests/test_doc_search_output.py -v`
Expected: 所有测试通过

- [ ] **Step 3: 提交**

```bash
git add backend/tests/test_doc_search_output.py
git commit -m "test: add output format tests for DocSearchTool"
```

---

## 自检清单

- [ ] 所有 Task 完成
- [ ] 代码编译通过
- [ ] 测试全部通过
- [ ] 无 "TODO"、"TBD" 等占位符
- [ ] git 已提交

---

**Plan saved to:** `docs/superpowers/plans/2026-03-30-doc-search-output-optimization.md`
