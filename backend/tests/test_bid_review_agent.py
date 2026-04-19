"""Tests for BidReviewAgent - TDD approach."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import tempfile
import asyncio

from backend.agent.bid_review_agent import BidReviewAgent


class TestBidReviewAgentNormalization:
    """Tests for BidReviewAgent finding normalization methods."""

    @pytest.fixture
    def agent(self):
        """Create agent instance without full initialization."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Rule\n检查规则内容")
            rule_doc_path = f.name

        agent = BidReviewAgent(
            project_id="test_project",
            tender_doc_path="/tmp/test_tender.md",
            bid_doc_path="/tmp/test_bid.md",
            user_id="test_user",
            rule_doc_path=rule_doc_path,
            max_steps=5,
        )
        return agent

    # === RED: Write failing test first ===

    def test_normalize_finding_requires_requirement_content(self, agent):
        """RED: Finding without requirement_content should return None."""
        result = agent._normalize_finding({"explanation": "some text"}, counter=1)
        assert result is None

    def test_normalize_finding_rejects_json_fragment_as_requirement(self, agent):
        """RED: Finding with JSON fragment as requirement_content should return None."""
        result = agent._normalize_finding({
            "requirement_content": '"explanation": "some text"'
        }, counter=1)
        assert result is None

    def test_normalize_finding_rejects_table_header_as_requirement(self, agent):
        """RED: Finding with table header as requirement_content should return None."""
        result = agent._normalize_finding({
            "requirement_content": "要求 | 符合状态 | 严重程度"
        }, counter=1)
        assert result is None

    def test_normalize_finding_accepts_valid_finding(self, agent):
        """RED: Valid finding should be normalized correctly."""
        result = agent._normalize_finding({
            "requirement_key": "req_001",
            "requirement_content": "投标人必须具有ISO9001认证",
            "bid_content": "我司具有ISO9001认证",
            "is_compliant": True,
        }, counter=1)

        assert result is not None
        assert result["requirement_key"] == "req_001"
        assert result["requirement_content"] == "投标人必须具有ISO9001认证"
        assert result["is_compliant"] is True
        assert result["severity"] is None

    def test_normalize_finding_defaults_severity_for_non_compliant(self, agent):
        """RED: Non-compliant finding without severity should default to 'major'."""
        result = agent._normalize_finding({
            "requirement_content": "投标人必须具有ISO9001认证",
            "is_compliant": False,
        }, counter=1)

        assert result is not None
        assert result["is_compliant"] is False
        assert result["severity"] == "major"

    def test_normalize_finding_infers_minor_for_optional_requirement(self, agent):
        """RED: Non-compliant optional requirement should be severity 'minor'."""
        result = agent._normalize_finding({
            "requirement_content": "可提供ISO14001认证（如有）",
            "bid_content": None,
            "is_compliant": False,
            "severity": "major",  # Explicitly set but should be overridden
        }, counter=1)

        assert result is not None
        assert result["severity"] == "minor"

    def test_try_parse_json_direct(self, agent):
        """RED: Direct JSON string should be parsed."""
        result = agent._try_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_try_parse_json_in_code_block(self, agent):
        """RED: JSON inside markdown code block should be parsed."""
        result = agent._try_parse_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_try_parse_json_invalid_returns_none(self, agent):
        """RED: Invalid JSON should return None."""
        result = agent._try_parse_json('not valid json at all')
        assert result is None

    def test_infer_severity_critical(self, agent):
        """RED: Text with critical keywords should return 'critical'."""
        assert agent._infer_severity("这是严重的问题") == "critical"
        assert agent._infer_severity("这是critical问题") == "critical"

    def test_infer_severity_major(self, agent):
        """RED: Text with major keywords should return 'major'."""
        assert agent._infer_severity("这是重要的问题") == "major"
        assert agent._infer_severity("这是major问题") == "major"

    def test_infer_severity_minor(self, agent):
        """RED: Text with minor keywords should return 'minor'."""
        assert agent._infer_severity("这是轻微的问题") == "minor"

    def test_infer_severity_default_minor(self, agent):
        """RED: Text without keywords should default to 'minor'."""
        assert agent._infer_severity("这是一个普通的问题") == "minor"

    def test_parse_findings_from_text_review_pass(self, agent):
        """RED: Review passing text should return compliant finding."""
        text = "投标文件完全符合招标要求，无不符合项"
        findings = agent._parse_findings_from_text(text)

        assert len(findings) == 1
        assert findings[0]["is_compliant"] is True

    def test_parse_findings_from_text_extracts_json_findings(self, agent):
        """RED: Text with JSON array should extract structured findings."""
        text = '''
        以下是审查结果：
        ```json
        [
            {"requirement_key": "req_001", "requirement_content": "要求1", "is_compliant": false, "severity": "major"}
        ]
        ```
        '''
        findings = agent._parse_findings_from_text(text)

        assert len(findings) >= 1
        non_compliant = [f for f in findings if not f["is_compliant"]]
        assert len(non_compliant) >= 1

    def test_load_rule_doc_returns_content(self, agent):
        """RED: _load_rule_doc should return rule document content."""
        content = agent._load_rule_doc()
        assert content is not None
        assert len(content) > 0

    def test_load_rule_doc_raises_on_missing_file(self, agent):
        """RED: _load_rule_doc should raise FileNotFoundError for missing file."""
        agent.rule_doc_path = "/nonexistent/path/rule.md"
        with pytest.raises(FileNotFoundError):
            agent._load_rule_doc()

    def test_build_system_prompt_includes_rule_content(self, agent):
        """RED: System prompt should include rule document content."""
        rule_content = "# Test Rule\n这是测试规则内容"
        prompt = agent._build_system_prompt(rule_content)
        assert "这是测试规则内容" in prompt

    def test_decide_merge_calls_merge_decider_tool(self, agent):
        """RED: decide_merge should call MergeDeciderTool and return result."""
        new_finding = {
            "requirement_key": "req_001",
            "requirement_content": "Test requirement",
            "is_compliant": False,
            "severity": "major",
        }
        existing_findings = []

        with patch('backend.agent.bid_review_agent.MergeDeciderTool') as MockTool:
            mock_instance = MagicMock()
            mock_result = MagicMock(success=True, content="Should merge")
            mock_instance.execute = AsyncMock(return_value=mock_result)
            MockTool.return_value = mock_instance

            result = asyncio.run(agent.decide_merge(new_finding, existing_findings))

            assert result == "Should merge"
            mock_instance.execute.assert_called_once()


class TestBidReviewAgentIntegration:
    """Integration tests for BidReviewAgent with mocked dependencies."""

    @pytest.fixture
    def temp_rule_doc(self):
        """Create temporary rule document."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# 评标规则\n\n## 检查项1\n- 规则内容...")
            path = f.name
        yield path
        import os
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_run_review_with_mocked_llm_and_tools(self, temp_rule_doc):
        """RED: run_review should complete with mocked LLM and tools."""
        agent = BidReviewAgent(
            project_id="test_project",
            tender_doc_path="/tmp/test_tender.md",
            bid_doc_path="/tmp/test_bid.md",
            user_id="test_user",
            rule_doc_path=temp_rule_doc,
            max_steps=3,
        )

        # Mock initialize to skip MCP tool loading
        async def mock_initialize():
            pass
        agent.initialize = mock_initialize

        # Mock LLM to return a simple compliant response
        async def mock_generate(**kwargs):
            from mini_agent.schema import Message
            return Message(
                role="assistant",
                content='[{"requirement_key": "req_001", "requirement_content": "测试要求", "bid_content": "测试投标", "is_compliant": true}]',
                thinking=None,
                tool_calls=[]
            )

        agent.llm_client.generate = mock_generate

        # Mock tools
        for tool in agent.tools.values():
            tool.execute = AsyncMock(return_value=MagicMock(success=True, content="mocked"))

        findings = await agent.run_review()

        assert isinstance(findings, list)
        await agent.close()

    @pytest.mark.asyncio
    async def test_run_review_handles_missing_output_file(self, temp_rule_doc):
        """RED: run_review should return empty list when output file is missing."""
        agent = BidReviewAgent(
            project_id="test_project",
            tender_doc_path="/tmp/test_tender.md",
            bid_doc_path="/tmp/test_bid.md",
            user_id="test_user",
            rule_doc_path=temp_rule_doc,
            max_steps=1,
        )

        async def mock_initialize():
            pass
        agent.initialize = mock_initialize

        # Mock LLM that doesn't write output file
        async def mock_generate(**kwargs):
            from mini_agent.schema import Message
            return Message(
                role="assistant",
                content="审查完成",
                thinking=None,
                tool_calls=[]
            )

        agent.llm_client.generate = mock_generate

        for tool in agent.tools.values():
            tool.execute = AsyncMock(return_value=MagicMock(success=True, content="mocked"))

        findings = await agent.run_review()

        assert isinstance(findings, list)
        await agent.close()
