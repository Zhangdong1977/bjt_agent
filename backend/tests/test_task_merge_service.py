"""Tests for TaskMergeService."""
import pytest
import sys
from unittest.mock import MagicMock

# Mock mini_agent before import
mock_schema = MagicMock()
mock_schema.Message = MagicMock()
sys.modules['mini_agent'] = MagicMock()
sys.modules['mini_agent.schema'] = mock_schema

from backend.services.task_merge_service import TaskMergeService


class TestTaskMergeServiceValidation:
    """Test finding validation in TaskMergeService."""

    @pytest.fixture
    def service(self):
        return TaskMergeService(agent=None)

    def test_invalid_findings_filtered(self, service):
        """Invalid findings should be filtered before merge."""
        findings = [
            {
                "requirement_key": "req_001",
                "requirement_content": "valid requirement",
                "bid_content": "valid bid",
                "is_compliant": False,
                "severity": "minor",
            },
            {
                "requirement_key": "req_002",
                "requirement_content": "",  # Invalid: empty
                "bid_content": "some content",
                "is_compliant": False,
                "severity": "minor",
            },
            {
                "requirement_key": "req_003",
                "requirement_content": "。",  # Invalid: punctuation only
                "bid_content": "some content",
                "is_compliant": False,
                "severity": "minor",
            },
            {
                "requirement_key": "req_004",
                "requirement_content": "valid requirement 2",
                "bid_content": '{"suggestion": "json"}',  # Invalid: JSON fragment
                "is_compliant": False,
                "severity": "minor",
            },
        ]
        # Manually test _is_valid_finding via the import
        from backend.services.merge_decision_parser import _is_valid_finding
        valid = [f for f in findings if _is_valid_finding(f)]
        assert len(valid) == 1
        assert valid[0]["requirement_key"] == "req_001"


class TestTaskMergeServiceEmptyFirstKeep:
    """Test empty-first keep logic."""

    @pytest.fixture
    def service(self):
        return TaskMergeService(agent=None)

    @pytest.mark.asyncio
    async def test_empty_merged_auto_keep(self, service):
        """When merged_findings is empty, discard should be overridden to keep."""
        # Create a finding that's clearly invalid but passes validation
        findings = [
            {
                "requirement_key": "req_001",
                "requirement_content": "real requirement",
                "bid_content": "some explanation here",
                "is_compliant": False,
                "severity": "minor",
            }
        ]
        result = await service.merge_sub_agent_results(findings)
        # With no agent, it should keep the finding
        assert result["total_findings"] == 1