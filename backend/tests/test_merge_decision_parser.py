"""Tests for merge_decision_parser module."""
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