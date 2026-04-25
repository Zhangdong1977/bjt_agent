"""Tests for BidReviewAgent heartbeat and cancel_event integration."""

import sys
from pathlib import Path

# Ensure project root is in sys.path for backend module imports
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
import asyncio
import tempfile

from backend.agent.bid_review_agent import BidReviewAgent


class TestCancelEventIntegration:
    """Tests for cancel_event integration with BidReviewAgent."""

    @pytest.fixture
    def temp_rule_doc(self):
        """Create a temporary rule document."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# 测试规则\n\n## 检查项1\n- 规则内容...")
            path = f.name
        yield path
        import os
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_cancel_event_parameter_accepted(self, temp_rule_doc):
        """Test that cancel_event parameter is accepted by BidReviewAgent constructor.

        This verifies the constructor doesn't raise on cancel_event parameter.
        """
        cancel_event = asyncio.Event()

        # Should not raise
        agent = BidReviewAgent(
            project_id="test-project",
            tender_doc_path="/tmp/test_tender.md",
            bid_doc_path="/tmp/test_bid.md",
            user_id="test-user",
            rule_doc_path=temp_rule_doc,
            cancel_event=cancel_event,
        )

        # Cleanup
        await agent.close()

    @pytest.mark.asyncio
    async def test_heartbeat_timeout_configurable(self, temp_rule_doc):
        """Test that heartbeat_timeout parameter is accepted and stored."""
        agent = BidReviewAgent(
            project_id="test-project",
            tender_doc_path="/tmp/test_tender.md",
            bid_doc_path="/tmp/test_bid.md",
            user_id="test-user",
            rule_doc_path=temp_rule_doc,
            heartbeat_timeout=30,
        )

        # Verify heartbeat_timeout is stored
        assert agent.heartbeat_timeout == 30

        # Cleanup
        await agent.close()

    @pytest.mark.asyncio
    async def test_cancel_event_stops_agent(self, temp_rule_doc):
        """Test that setting cancel_event actually stops the agent.

        When cancel_event is pre-set before calling run_review(), the agent
        should stop immediately without executing any LLM calls.
        """
        # Create cancel_event that is already set (immediate cancellation)
        cancel_event = asyncio.Event()
        cancel_event.set()

        agent = BidReviewAgent(
            project_id="test-project",
            tender_doc_path="/tmp/test_tender.md",
            bid_doc_path="/tmp/test_bid.md",
            user_id="test-user",
            rule_doc_path=temp_rule_doc,
            cancel_event=cancel_event,
        )

        # Track if any LLM calls were made by mocking the llm_client
        llm_calls = []
        original_generate = agent.llm_client.generate

        async def mock_generate(*args, **kwargs):
            llm_calls.append(args)
            return await original_generate(*args, **kwargs)

        agent.llm_client.generate = mock_generate

        # Call run_review - should return empty findings due to immediate cancellation
        findings = await agent.run_review()

        # Cleanup
        await agent.close()

        # Verify: No LLM calls should have been made since cancel was immediate
        assert len(llm_calls) == 0, f"Expected 0 LLM calls but got {len(llm_calls)}"
        # Findings should be empty since agent was cancelled before execution
        assert findings == [], f"Expected empty findings but got {findings}"
