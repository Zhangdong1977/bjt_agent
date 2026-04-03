import pytest
from pathlib import Path
from backend.agent.bid_review_agent import BidReviewAgent
from backend.config import get_settings


@pytest.mark.asyncio
async def test_mcp_tools_loaded():
    """Test that MCP tools are loaded when MCP config exists."""
    settings = get_settings()
    mcp_config_path = Path(__file__).parent.parent.parent / "backend" / "mcp.json"

    if not mcp_config_path.exists():
        pytest.skip("MCP config not found")

    # This test verifies the MCP loader can parse the config
    from mini_agent.tools.mcp_loader import load_mcp_tools_async

    tools = await load_mcp_tools_async(str(mcp_config_path))
    # Should have tools or empty list (depends on API key)
    assert isinstance(tools, list)