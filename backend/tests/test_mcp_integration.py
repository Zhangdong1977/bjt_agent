"""MCP integration tests."""

import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_mcp_config_exists():
    """Test that MCP config file exists."""
    mcp_config_path = Path(__file__).parent.parent.parent / "backend" / "mcp.json"
    assert mcp_config_path.exists(), f"MCP config not found at {mcp_config_path}"


@pytest.mark.asyncio
async def test_mcp_config_valid_json():
    """Test that MCP config is valid JSON."""
    import json
    mcp_config_path = Path(__file__).parent.parent.parent / "backend" / "mcp.json"
    if not mcp_config_path.exists():
        pytest.skip("MCP config not found")

    with open(mcp_config_path) as f:
        config = json.load(f)

    assert "mcpServers" in config, "Config must have mcpServers key"
    assert isinstance(config["mcpServers"], dict), "mcpServers must be a dict"


@pytest.mark.asyncio
async def test_mcp_loader_returns_list():
    """Test that MCP loader returns a list (empty or with tools)."""
    from mini_agent.tools.mcp_loader import load_mcp_tools_async, cleanup_mcp_connections

    mcp_config_path = Path(__file__).parent.parent.parent / "backend" / "mcp.json"
    if not mcp_config_path.exists():
        pytest.skip("MCP config not found")

    try:
        tools = await load_mcp_tools_async(str(mcp_config_path))
        assert isinstance(tools, list), "MCP loader should return a list"
    finally:
        await cleanup_mcp_connections()