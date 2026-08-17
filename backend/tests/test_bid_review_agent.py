"""Tests for BidReviewAgent - TDD approach."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import tempfile
import asyncio

from backend.agent.bid_review_agent import BidReviewAgent
from mini_agent.schema import Message


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


class TestKeywordFallbackComplianceGuard:
    """Regression tests for the false-risk-item bug.

    Reproduces the production incident where a fully-compliant D002 verdict
    ("损失分值 0 分 / 无缺失资质") was misread by the keyword fallback
    (_extract_keyword_findings) as a non-compliant finding, producing a phantom
    risk item. The fix has three layers, each tested below.
    """

    @pytest.fixture
    def agent(self):
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

    def _assistant(self, content: str) -> Message:
        return Message(role="assistant", content=content, thinking=None, tool_calls=[])

    # --- Layer 1: negation-context filter in _extract_keyword_findings ---------

    def test_keyword_fallback_skips_compliant_summary(self, agent):
        """A compliant verdict containing neutral words like '缺失' inside a
        negation ('无缺失资质', '损失 0 分') must NOT produce a finding.
        This is the exact production D002 case.
        """
        agent.messages = [self._assistant(
            "## 检查项5：企业证书得分情况汇总\n\n"
            "- 企业证书满分分值：8 分\n"
            "- 实际可得分值：8 分（6项得分项全部满足）\n"
            "- 损失分值：0 分（无缺失资质）\n\n"
            "关键合规要点确认：全部6项证书的拥有方均与投标人名称完全一致；"
            "所有体系认证证书均在有效期内，符合评分标准要求。\n"
            "最终结论：企业证书能力得分项可得满分 8 分，无扣分项。"
        )]
        findings = agent._extract_keyword_findings()
        assert findings == [], (
            f"Compliant summary must yield zero findings, got {len(findings)}: "
            f"{[f.get('explanation') for f in findings]}"
        )

    def test_keyword_fallback_keeps_genuine_non_compliance(self, agent):
        """A genuine non-compliance statement ('不满足要求', no negation context)
        must still be caught by the fallback."""
        agent.messages = [self._assistant(
            "## 检查项2\n经核查，投标人提供的ISO9001证书已过有效期，不满足评分标准中"
            "'有效期内'的要求，该可得分项不得分。"
        )]
        findings = agent._extract_keyword_findings()
        # '不满足' is a strong keyword, sentence has no negation phrase -> should fire
        assert len(findings) == 1
        assert findings[0]["is_compliant"] is False

    def test_keyword_fallback_downgrades_severity_to_minor(self, agent):
        """Keyword fallback is low-confidence; its findings must be 'minor',
        not 'major' (production incident had a phantom 'major' risk)."""
        agent.messages = [self._assistant(
            "经核查，该章节内容不符合评分标准要求，存在明显问题。"
        )]
        findings = agent._extract_keyword_findings()
        assert len(findings) >= 1
        assert all(f["severity"] == "minor" for f in findings), (
            f"fallback findings must be minor, got {[f['severity'] for f in findings]}"
        )

    def test_neutral_words_removed_from_strong_keywords(self, agent):
        """'缺失'/'缺少'/'不一致' etc. must no longer be in the strong keyword
        list (they were the root cause: '无缺失资质' matched '缺失')."""
        removed = {"缺失", "缺少", "不一致", "未体现", "未说明", "未明确", "未包含", "未提供"}
        for w in removed:
            assert w not in BidReviewAgent._NON_COMPLIANCE_KEYWORDS, (
                f"'{w}' should be removed from strong keywords"
            )

    # --- Layer 2: _llm_extract_findings empty-list flag ------------------------

    @pytest.mark.asyncio
    async def test_llm_extract_empty_list_sets_all_compliant_flag(self, agent):
        """When the LLM returns a well-formed empty JSON array, that is a
        deliberate 'all compliant' verdict — the flag must be set so run_review
        can skip the keyword fallback."""
        from mini_agent.schema import Message as M

        async def mock_generate(**kwargs):
            return M(role="assistant", content="[]", thinking=None, tool_calls=[])
        agent.llm_client.generate = mock_generate

        result = await agent._llm_extract_findings("some markdown")
        assert result == []
        assert agent._llm_extract_all_compliant is True

    @pytest.mark.asyncio
    async def test_llm_extract_no_content_does_not_set_flag(self, agent):
        """When the LLM returns nothing (extraction failure), the flag must
        stay False so the keyword fallback can still run."""
        from mini_agent.schema import Message as M

        async def mock_generate(**kwargs):
            return M(role="assistant", content="", thinking=None, tool_calls=[])
        agent.llm_client.generate = mock_generate

        result = await agent._llm_extract_findings("some markdown")
        assert result == []
        assert agent._llm_extract_all_compliant is False

    # --- Layer 3: end-to-end guard (flag suppresses keyword fallback) -----------

    def test_flag_initially_false(self, agent):
        """The all-compliant flag must start False so it only affects runs where
        the LLM genuinely returned an empty verdict."""
        assert agent._llm_extract_all_compliant is False


class TestKeywordFallbackContextBlock:
    """Regression tests for the truncated-explanation bug.

    The keyword fallback (_extract_keyword_findings) previously extracted a
    hard ~200-char window around each keyword, which sliced the explanation
    mid-sentence / mid-date (e.g. production finding cut off at "...2027-09-"
    with no ellipsis). The fix (_extract_context_block) expands to paragraph
    boundaries so the explanation stays coherent.
    """

    @pytest.fixture
    def agent(self):
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

    def _assistant(self, content: str) -> Message:
        return Message(role="assistant", content=content, thinking=None, tool_calls=[])

    def test_explanation_not_truncated_mid_date(self, agent):
        """A finding whose explanation is longer than 200 chars must NOT be cut
        mid-token. This reproduces the production case where the date
        "2027-09-02" was truncated to "2027-09-".
        """
        long_para = (
            "## 检查项2：企业体系认证证书得分检查\n\n"
            "经核查，投标人提供的ISO9001证书存在问题：证书有效期标注不清晰，"
            "不满足评分标准中关于有效期的要求。该证书有效期至2026-05-10，"
            "而CMMI证书有效期至2027-01-27，CCRC证书有效期至2027-09-02，"
            "但ISO9001证书的发证机构信息存在明显矛盾，与评分标准要求不符。"
            "综合判定该项不得分，损失1分。"
        )
        agent.messages = [self._assistant(long_para)]
        findings = agent._extract_keyword_findings()
        assert len(findings) >= 1
        expl = findings[0]["explanation"]
        # The full date must survive — no mid-date truncation.
        assert "2027-09-02" in expl, f"date truncated in explanation: ...{expl[-60:]}"
        assert "2026-05-10" in expl, f"earlier date lost: {expl[:80]}..."

    def test_context_block_respects_paragraph_boundary(self, agent):
        """The context should expand to the surrounding paragraph (\\n\\n
        delimited), not stop at an arbitrary fixed window. A multi-sentence
        paragraph that mentions a keyword once should be returned whole."""
        para = (
            "前一段无关内容，这里是一些背景介绍，投标人基本情况说明。\n\n"
            "检查项：该章节内容不符合评分标准要求，详细原因如下所述。"
            "第一，证书扫描件模糊不清无法识别。"
            "第二，证书拥有方名称与投标人名称不一致。"
            "第三，证书已过有效期。"
            "因此综合判定为不符合，该项不得分。\n\n"
            "下一段无关内容，继续其他检查项的说明。"
        )
        agent.messages = [self._assistant(para)]
        findings = agent._extract_keyword_findings()
        assert len(findings) == 1
        expl = findings[0]["explanation"]
        # The middle paragraph must be returned in full (both ends present)
        assert "检查项：该章节内容不符合" in expl
        assert "因此综合判定为不符合，该项不得分。" in expl
        # And must NOT bleed into neighboring paragraphs
        assert "前一段无关内容" not in expl
        assert "下一段无关内容" not in expl

    def test_context_block_capped_for_oversized_paragraph(self, agent):
        """An extremely long paragraph is bounded by max_chars so a runaway
        explanation can't overwhelm the results page / PDF."""
        huge = "A" * 5000 + " 不符合 " + "B" * 5000
        agent.messages = [self._assistant(huge)]
        findings = agent._extract_keyword_findings()
        assert len(findings) == 1
        # Default cap is 2000; explanation must respect it.
        assert len(findings[0]["explanation"]) <= 2000


class TestParseMdFindingsAnchoring:
    """页码/整改建议小节的规则解析（历史缺陷：两字段恒 None）。"""

    @pytest.fixture
    def agent(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Rule\n检查规则内容")
            rule_doc_path = f.name
        return BidReviewAgent(
            project_id="test_project",
            tender_doc_path="/tmp/test_tender.md",
            bid_doc_path="/tmp/test_bid.md",
            user_id="test_user",
            rule_doc_path=rule_doc_path,
            max_steps=5,
        )

    def test_extracts_page_and_suggestion_sections(self, agent):
        md = """## 检查项1: 投标人公章检查

### 规则项
投标人公章检查

### 招标书要求
响应文件所要求盖章的盖章齐全。

### 投标文件内容
投标函落款处未加盖公章。

### 不符合项说明
所有要求盖章的落款处均未实际盖章，存在投标无效风险。

### 严重程度
critical

### 页码
第 12 页

### 整改建议
在投标函落款处补盖投标人公章。
"""
        findings = agent._parse_md_findings(md)
        assert findings is not None and len(findings) == 1
        assert findings[0]["location_page"] == 12
        assert findings[0]["suggestion"] == "在投标函落款处补盖投标人公章。"

    def test_legacy_md_without_sections_yields_none_fields(self, agent):
        md = """## 检查项1: 投标函检查

### 招标书要求
投标函须按模板填写。

### 投标文件内容
投标函中报价大小写不一致。

### 不符合项说明
报价金额大小写不一致。

### 严重程度
major
"""
        findings = agent._parse_md_findings(md)
        assert findings is not None and len(findings) == 1
        assert findings[0]["location_page"] is None
        assert findings[0]["suggestion"] is None

    def test_unknown_page_text_stays_none(self, agent):
        md = """## 检查项1: 签字检查

### 招标书要求
签字齐全。

### 投标文件内容
授权书未签字。

### 不符合项说明
缺少签字。

### 严重程度
critical

### 页码
未知

### 建议
补签。
"""
        findings = agent._parse_md_findings(md)
        assert findings is not None and len(findings) == 1
        assert findings[0]["location_page"] is None
        assert findings[0]["suggestion"] == "补签。"
