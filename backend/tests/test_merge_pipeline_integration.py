"""Integration test for full merge pipeline."""
import pytest
from unittest.mock import MagicMock, AsyncMock

# Simulate the scenario from the bug: 32 findings with invalid ones mixed in
INVALID_FINDINGS = [
    {"requirement_key": "req_002", "requirement_content": "。", "bid_content": "文件对此关键商务条款未提供任何具体内容，属于文档不完整。", "is_compliant": False, "severity": "minor"},
    {"requirement_key": "req_010", "requirement_content": "", "bid_content": '{"suggestion": "test"}', "is_compliant": False, "severity": "minor"},
]

VALID_FINDINGS = [
    {"requirement_key": "req_001", "requirement_content": "所有备件必须符合国家标准及行业要求。", "bid_content": "已取得ISO9001认证", "is_compliant": True, "severity": None},
    {"requirement_key": "req_003", "requirement_content": "检测标准依据国家有关规定执行。", "bid_content": "符合国家标准", "is_compliant": True, "severity": None},
    {"requirement_key": "req_004", "requirement_content": "交货期不得超过45天。", "bid_content": "交货期60天", "is_compliant": False, "severity": "major"},
]

class TestMergePipelineIntegration:
    def test_invalid_findings_filtered_before_merge(self):
        """Invalid findings should be filtered BEFORE LLM is called."""
        from backend.services.merge_decision_parser import _is_valid_finding

        all_findings = INVALID_FINDINGS + VALID_FINDINGS
        valid_findings = [f for f in all_findings if _is_valid_finding(f)]

        # Should filter out empty content and JSON fragments
        assert len(valid_findings) == len(VALID_FINDINGS)
        for f in valid_findings:
            assert f["requirement_key"] in ["req_001", "req_003", "req_004"]

    def test_placeholder_messages_filtered(self):
        """Incomplete document placeholder messages should be filtered."""
        from backend.services.merge_decision_parser import _is_valid_finding

        finding = {
            "requirement_key": "req_test",
            "requirement_content": "some requirement",
            "bid_content": "文件对此技术要求未提供任何说明，属于文档不完整。",
            "is_compliant": False,
            "severity": "minor",
        }
        # This is a short placeholder message, should be filtered
        assert _is_valid_finding(finding) is False

    def test_json_fragment_bid_content_filtered(self):
        """JSON fragment bid_content should be filtered."""
        from backend.services.merge_decision_parser import _is_valid_finding

        finding = {
            "requirement_key": "req_test",
            "requirement_content": "some requirement",
            "bid_content": '{"suggestion": "test"}',
            "is_compliant": False,
            "severity": "minor",
        }
        assert _is_valid_finding(finding) is False

    def test_empty_requirement_content_filtered(self):
        """Empty requirement_content should be filtered."""
        from backend.services.merge_decision_parser import _is_valid_finding

        finding = {
            "requirement_key": "req_test",
            "requirement_content": "",
            "bid_content": "some bid content",
            "is_compliant": False,
            "severity": "minor",
        }
        assert _is_valid_finding(finding) is False

    def test_punctuation_only_requirement_filtered(self):
        """Punctuation-only requirement_content should be filtered."""
        from backend.services.merge_decision_parser import _is_valid_finding

        finding = {
            "requirement_key": "req_test",
            "requirement_content": "。",
            "bid_content": "some bid content",
            "is_compliant": False,
            "severity": "minor",
        }
        assert _is_valid_finding(finding) is False