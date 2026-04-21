"""Test MCP image understanding tool integration."""

import sys
import os
from pathlib import Path

# Ensure Mini-Agent path is in sys.path before importing mini_agent modules
mini_agent_path = Path(__file__).parent.parent.parent / "Mini-Agent"
if str(mini_agent_path) not in sys.path:
    sys.path.insert(0, str(mini_agent_path))

# Load .env file for environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import pytest
import asyncio


@pytest.mark.asyncio
async def test_mcp_understand_image_tool_loaded():
    """Test that understand_image MCP tool is loaded successfully."""
    from mini_agent.tools.mcp_loader import load_mcp_tools_async, cleanup_mcp_connections

    mcp_config_path = Path(__file__).parent.parent.parent / "backend" / "mcp.json"
    if not mcp_config_path.exists():
        pytest.skip("MCP config not found")

    try:
        tools = await load_mcp_tools_async(str(mcp_config_path))
        tool_names = [t.name for t in tools]

        print(f"[TEST] Loaded MCP tools: {tool_names}")

        assert "understand_image" in tool_names, f"understand_image tool not found. Available tools: {tool_names}"
        print("[TEST] ✓ understand_image tool is available")

    finally:
        await cleanup_mcp_connections()


@pytest.mark.asyncio
async def test_mcp_web_search_tool_loaded():
    """Test that web_search MCP tool is loaded successfully."""
    from mini_agent.tools.mcp_loader import load_mcp_tools_async, cleanup_mcp_connections

    mcp_config_path = Path(__file__).parent.parent.parent / "backend" / "mcp.json"
    if not mcp_config_path.exists():
        pytest.skip("MCP config not found")

    try:
        tools = await load_mcp_tools_async(str(mcp_config_path))
        tool_names = [t.name for t in tools]

        print(f"[TEST] Loaded MCP tools: {tool_names}")

        assert "web_search" in tool_names, f"web_search tool not found. Available tools: {tool_names}"
        print("[TEST] ✓ web_search tool is available")

    finally:
        await cleanup_mcp_connections()


@pytest.mark.asyncio
async def test_bid_review_agent_initialization_with_mcp():
    """Test that BidReviewAgent initializes with MCP tools."""
    import tempfile
    from backend.agent.bid_review_agent import BidReviewAgent
    from mini_agent.tools.mcp_loader import cleanup_mcp_connections

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write("# Test Rule\n检查规则内容")
        rule_doc_path = f.name

    agent = BidReviewAgent(
        project_id="test_mcp_project",
        tender_doc_path="/tmp/test_tender.md",
        bid_doc_path="/tmp/test_bid.md",
        user_id="test_mcp_user",
        rule_doc_path=rule_doc_path,
        max_steps=5,
    )

    try:
        # Initialize agent (loads MCP tools)
        await agent.initialize()

        # Check if understand_image is in agent tools
        assert "understand_image" in agent.tools, f"understand_image not in agent.tools. Available: {list(agent.tools.keys())}"
        print("[TEST] ✓ BidReviewAgent has understand_image tool")

        assert "web_search" in agent.tools, f"web_search not in agent.tools. Available: {list(agent.tools.keys())}"
        print("[TEST] ✓ BidReviewAgent has web_search tool")

    finally:
        await cleanup_mcp_connections()


@pytest.mark.asyncio
async def test_understand_image_with_simple_image():
    """Test understand_image tool with a simple test image.

    Note: This test requires MINIMAX_API_KEY to be set and may incur API costs.
    """
    from mini_agent.tools.mcp_loader import load_mcp_tools_async, cleanup_mcp_connections
    from PIL import Image
    import tempfile
    import os

    # Create a simple test image
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        img = Image.new('RGB', (100, 100), color='red')
        img.save(f, 'PNG')
        test_image_path = f.name

    mcp_config_path = Path(__file__).parent.parent.parent / "backend" / "mcp.json"
    if not mcp_config_path.exists():
        os.unlink(test_image_path)
        pytest.skip("MCP config not found")

    try:
        tools = await load_mcp_tools_async(str(mcp_config_path))
        tool_map = {t.name: t for t in tools}

        if "understand_image" not in tool_map:
            os.unlink(test_image_path)
            pytest.skip("understand_image tool not available")

        understand_image = tool_map["understand_image"]

        # Call the tool with a simple prompt
        result = await understand_image.execute(
            prompt="描述这张图片的内容",
            image_source=test_image_path
        )

        print(f"[TEST] understand_image result: {result}")

        assert result.success, f"understand_image failed: {result.error}"
        assert result.content is not None
        assert len(result.content) > 0
        print(f"[TEST] ✓ understand_image returned: {result.content[:200]}...")

    finally:
        await cleanup_mcp_connections()
        if os.path.exists(test_image_path):
            os.unlink(test_image_path)


if __name__ == "__main__":
    # Run tests manually
    print("=" * 60)
    print("Testing MCP Image Understanding Integration")
    print("=" * 60)

    async def run_tests():
        print("\n[Test 1] Testing MCP tool loading...")
        try:
            await test_mcp_understand_image_tool_loaded()
        except Exception as e:
            print(f"[TEST 1] FAILED: {e}")

        print("\n[Test 2] Testing BidReviewAgent initialization with MCP...")
        try:
            await test_bid_review_agent_initialization_with_mcp()
        except Exception as e:
            print(f"[TEST 2] FAILED: {e}")

        print("\n[Test 3] Testing understand_image with test image...")
        try:
            await test_understand_image_with_simple_image()
        except Exception as e:
            print(f"[TEST 3] FAILED: {e}")

        print("\n" + "=" * 60)
        print("Tests completed")
        print("=" * 60)

    asyncio.run(run_tests())