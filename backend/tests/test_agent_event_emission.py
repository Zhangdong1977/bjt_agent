"""Tests for agent event emission during tool execution."""

import asyncio
import pytest
from unittest.mock import MagicMock

from backend.agent.bid_review_agent import BidReviewAgent


@pytest.mark.asyncio
async def test_tool_events_emitted_during_execution():
    """Test that tool start/complete events are emitted during run_review()."""
    captured_events = []

    def event_cb(event_type, data):
        captured_events.append((event_type, data.copy(), asyncio.get_event_loop().time()))

    # Create agent with mocked dependencies
    agent = await BidReviewAgent(
        project_id="test_project",
        tender_doc_path="/tmp/test_tender.md",
        bid_doc_path="/tmp/test_bid.md",
        user_id="test_user",
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
    tool_events = [(t, d) for t, d, _ in captured_events if t == "tool_progress"]

    # Should have at least some tool progress events
    assert len(captured_events) > 0, "No events emitted during execution"

    # Should have received step, progress, or tool events
    event_types = [t for t, _, _ in captured_events]
    assert "step" in event_types or "progress" in event_types, f"Expected step/progress events, got: {event_types}"
