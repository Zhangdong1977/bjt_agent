# backend/tests/test_merge_service.py
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Mock the models module before importing merge_service
mock_models = MagicMock()
sys.modules['backend.models'] = mock_models

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
        mock_agent.decide_merge.return_value = "决策：keep\n理由：新发现是全新的\n替换key：无"

        service = MergeService(mock_db, mock_agent)

        new_finding = {
            "requirement_key": "req_001",
            "requirement_content": "新要求内容",
        }

        decision = await service._get_llm_merge_decision(new_finding, [])

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

    @pytest.mark.asyncio
    async def test_keep_both_on_parse(self, mock_agent, mock_db):
        """When LLM says keep_both, both records should be kept."""
        # This simulates when parser returns keep_both (parse_failed case handled separately)
        mock_agent.decide_merge.return_value = "some unparseable text"

        service = MergeService(mock_db, mock_agent)

        # The fallback (keep_both) happens on parse failure
        decision = await service._get_llm_merge_decision({}, [{"key": "val"}])

        assert decision["action"] == "keep_both"
