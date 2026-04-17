"""Tests for finding validation in merge_decision_parser."""
import pytest
from backend.services.merge_decision_parser import _is_valid_finding


class TestIsValidFinding:
    def test_valid_finding_with_content(self):
        finding = {
            "requirement_key": "req_001",
            "requirement_content": "所有备件必须符合国家标准",
            "bid_content": "已取得ISO9001认证",
            "is_compliant": True,
        }
        assert _is_valid_finding(finding) is True

    def test_empty_requirement_content_invalid(self):
        finding = {
            "requirement_key": "req_001",
            "requirement_content": "",
            "bid_content": "some content",
        }
        assert _is_valid_finding(finding) is False

    def test_punctuation_only_requirement_invalid(self):
        finding = {
            "requirement_key": "req_001",
            "requirement_content": "。",
            "bid_content": "some content",
        }
        assert _is_valid_finding(finding) is False

    def test_json_placeholder_bid_invalid(self):
        finding = {
            "requirement_key": "req_001",
            "requirement_content": "some requirement",
            "bid_content": '{"suggestion": "test"}',
        }
        assert _is_valid_finding(finding) is False

    def test_incomplete_document_placeholder_invalid(self):
        finding = {
            "requirement_key": "req_001",
            "requirement_content": "some requirement",
            "bid_content": "文件对此技术要求未提供任何说明，属于文档不完整。",
        }
        assert _is_valid_finding(finding) is False

    def test_valid_short_bid_content(self):
        finding = {
            "requirement_key": "req_001",
            "requirement_content": "检测标准",
            "bid_content": "符合国家检测标准要求",
        }
        assert _is_valid_finding(finding) is True