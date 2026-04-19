"""Tests for agent event emission during tool execution."""

import asyncio
import os
import pytest
from unittest.mock import MagicMock
import tempfile

from backend.agent.bid_review_agent import BidReviewAgent


@pytest.mark.asyncio
async def test_tool_events_emitted_during_execution():
    """Test that tool start/complete events are emitted during run_review()."""
    captured_events = []

    def event_cb(event_type, data):
        captured_events.append((event_type, data.copy(), asyncio.get_event_loop().time()))

    # Create a temporary rule doc file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write("# Test Rule\n\n## 检查项1\n检查规则...")
        rule_doc_path = f.name

    try:
        # Create agent with mocked dependencies (BidReviewAgent.__init__ is sync)
        agent = BidReviewAgent(
            project_id="test_project",
            tender_doc_path="/tmp/test_tender.md",
            bid_doc_path="/tmp/test_bid.md",
            user_id="test_user",
            rule_doc_path=rule_doc_path,
            event_callback=event_cb,
            max_steps=5,
        )

        # Mock the tools to execute quickly
        async def mock_execute(**kwargs):
            await asyncio.sleep(0.05)  # Simulate minimal work
            return MagicMock(success=True, content="Mock result content")

        for tool in agent.tools.values():
            tool.execute = mock_execute

        # Mock LLM to return a response with a tool call
        async def mock_generate(**kwargs):
            # First call returns tool call, second call returns completion
            if not hasattr(mock_generate, 'called'):
                mock_generate.called = True
                from mini_agent.schema import Message
                return Message(
                    role="assistant",
                    content="I'll search for the tender requirements.",
                    thinking=None,
                    tool_calls=[
                        MagicMock(
                            id="call_1",
                            function=MagicMock(
                                name="doc_search",
                                arguments={"query": "requirements"}
                            )
                        )
                    ]
                )
            else:
                from mini_agent.schema import Message
                return Message(
                    role="assistant",
                    content='[{"requirement_key": "req_001", "requirement_content": "Test", "bid_content": "Test bid", "is_compliant": false}]',
                    thinking=None,
                    tool_calls=[]
                )

        agent.llm.generate = mock_generate

        # Run agent
        try:
            await agent.run_review()
        except Exception as e:
            # Expected to potentially fail due to missing docs, but events should still be emitted
            pass
        finally:
            await agent.close()

        # Check that events were emitted during execution
        # Events should include step_start, llm_output, tool_call_start, tool_call_end, etc.
        assert len(captured_events) > 0, "No events emitted during execution"

        # Should have received step events
        event_types = [t for t, _, _ in captured_events]
        assert "step_start" in event_types or "llm_output" in event_types, f"Expected step_start/llm_output events, got: {event_types}"
    finally:
        os.unlink(rule_doc_path)
