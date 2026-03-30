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
        # Should NOT contain "找到 0 条"
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
        # Should NOT say "Found 0"
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
