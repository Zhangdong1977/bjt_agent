"""Tests for merge_decision_parser module."""
import pytest
from backend.services.merge_decision_parser import parse_merge_decision, parse_batch_merge_decisions


class TestParseMergeDecision:
    def test_parse_keep(self):
        text = """决策：keep
理由：这是一个全新的发现，与现有所有发现都不重复。
替换key：无"""
        result = parse_merge_decision(text)
        assert result["action"] == "keep"
        assert result["parse_failed"] is False
        assert result["replace_key"] is None
        assert result["reason"] == "这是一个全新的发现，与现有所有发现都不重复。"

    def test_parse_replace(self):
        text = """决策：replace
理由：新发现的位置信息更精确，severity更高。
替换key：req_001"""
        result = parse_merge_decision(text)
        assert result["action"] == "replace"
        assert result["replace_key"] == "req_001"
        assert result["reason"] == "新发现的位置信息更精确，severity更高。"

    def test_parse_discard(self):
        text = """决策：discard
理由：新发现与现有发现完全重复，没有提供任何新信息。
替换key：无"""
        result = parse_merge_decision(text)
        assert result["action"] == "discard"
        assert result["reason"] == "新发现与现有发现完全重复，没有提供任何新信息。"

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
        assert result["replace_key"] is None
        assert result["reason"] == "测试"


class TestParseBatchMergeDecisions:
    def test_parse_batch_merge_decisions_success(self):
        """Test normal parsing of multiple decisions with 新发现 markers."""
        text = """新发现[1]决策：keep
理由：这是一个全新的发现，与现有所有发现都不重复。
替换key：无

新发现[2]决策：replace
理由：新发现的位置信息更精确，severity更高。
替换key：req_001

新发现[3]决策：discard
理由：新发现与现有发现完全重复，没有提供任何新信息。
替换key：无"""
        keys = ["finding_001", "finding_002", "finding_003"]
        results = parse_batch_merge_decisions(text, keys)

        assert len(results) == 3
        assert results[0]["action"] == "keep"
        assert results[0]["parse_failed"] is False
        assert results[1]["action"] == "replace"
        assert results[1]["replace_key"] == "req_001"
        assert results[2]["action"] == "discard"
        assert results[2]["parse_failed"] is False

    def test_parse_batch_merge_decisions_partial_failure(self):
        """Test partial parse failure falls back to keep_both for that finding."""
        text = """新发现[1]决策：keep
理由：这是一个全新的发现。
替换key：无

新发现[2]这是一段无法解析的文本

新发现[3]决策：discard
理由：新发现与现有发现完全重复。
替换key：无"""
        keys = ["finding_001", "finding_002", "finding_003"]
        results = parse_batch_merge_decisions(text, keys)

        assert len(results) == 3
        assert results[0]["action"] == "keep"
        assert results[0]["parse_failed"] is False
        assert results[1]["action"] == "keep_both"
        assert results[1]["parse_failed"] is True
        assert results[2]["action"] == "discard"
        assert results[2]["parse_failed"] is False

    def test_parse_batch_merge_decisions_less_findings_than_keys(self):
        """Test padding when block count is less than key count."""
        text = """新发现[1]决策：keep
理由：这是一个全新的发现。
替换key：无

新发现[2]决策：replace
理由：新发现的位置信息更精确。
替换key：req_001"""
        # Only 2 findings in text but 4 keys provided
        keys = ["finding_001", "finding_002", "finding_003", "finding_004"]
        results = parse_batch_merge_decisions(text, keys)

        assert len(results) == 4
        assert results[0]["action"] == "keep"
        assert results[1]["action"] == "replace"
        # Missing findings should be padded with keep_both
        assert results[2]["action"] == "keep_both"
        assert results[2]["parse_failed"] is True
        assert results[2]["reason"] == "no corresponding decision block"
        assert results[3]["action"] == "keep_both"
        assert results[3]["parse_failed"] is True