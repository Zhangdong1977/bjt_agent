"""Smoke test for DeepSeek API integration.

Tests:
1. Basic connectivity and reasoning content extraction
2. Reasoning content round-trip (pass back in subsequent turns)
3. Tool calling with reasoning content preservation
"""

import asyncio
import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Mini-Agent"))

from backend.utils.mini_agent_utils import setup_mini_agent_path
setup_mini_agent_path()

from backend.services.llm_factory import create_llm_client
from mini_agent.schema import Message


async def test_basic_connectivity():
    """Test 1: Basic API connectivity and reasoning content extraction."""
    print("=" * 60)
    print("测试 1: DeepSeek API 基本连通性")
    print("=" * 60)

    client = create_llm_client()
    print(f"Provider: deepseek")
    print(f"API Base: {client.api_base}")
    print(f"Model: {client.model}")
    print(f"Reasoning Mode: {client._client.reasoning_mode}")
    print()

    messages = [
        Message(role="system", content="你是一个有用的助手。请用中文简短回复。"),
        Message(role="user", content="1+1等于几？简要回答。"),
    ]

    print("发送请求...")
    resp = await client.generate(messages)
    print(f"✅ Content: {resp.content}")
    print(f"✅ Thinking (前200字): {resp.thinking[:200] if resp.thinking else 'None'}...")
    print(f"✅ Usage: {resp.usage}")
    print()

    return resp


async def test_reasoning_roundtrip():
    """Test 2: Verify reasoning_content is correctly passed back in subsequent turns."""
    print("=" * 60)
    print("测试 2: 推理内容回传 (reasoning_content round-trip)")
    print("=" * 60)

    client = create_llm_client()

    messages = [
        Message(role="system", content="你是一个有用的助手。请用中文简短回复。"),
        Message(role="user", content="请分析一下人工智能的发展趋势，用一句话概括。"),
    ]

    # First turn
    print("第一轮请求...")
    resp1 = await client.generate(messages)
    print(f"✅ 回复: {resp1.content[:100]}...")
    print(f"✅ 推理内容 (前200字): {resp1.thinking[:200] if resp1.thinking else 'None'}...")
    print()

    # Add assistant response to history (with thinking preserved)
    messages.append(Message(
        role="assistant",
        content=resp1.content,
        thinking=resp1.thinking,
    ))

    # Second turn - this is where reasoning_content MUST be passed back
    messages.append(Message(role="user", content="再详细解释一下你刚才提到的一点。"))

    print("第二轮请求（带推理内容回传）...")
    resp2 = await client.generate(messages)
    print(f"✅ 回复: {resp2.content[:100]}...")
    print(f"✅ 推理内容 (前200字): {resp2.thinking[:200] if resp2.thinking else 'None'}...")
    print(f"✅ Usage: {resp2.usage}")
    print()
    print("🎉 推理内容回传成功！没有出现 400 错误。")

    return resp2


async def test_tool_calling():
    """Test 3: Tool calling with reasoning content."""
    print("=" * 60)
    print("测试 3: Tool Calling + 推理内容")
    print("=" * 60)

    client = create_llm_client()

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "获取指定城市的天气信息",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "城市名称",
                        },
                    },
                    "required": ["city"],
                },
            },
        }
    ]

    messages = [
        Message(role="system", content="你是一个有用的助手。"),
        Message(role="user", content="北京今天天气怎么样？"),
    ]

    print("发送带工具的请求...")
    resp = await client.generate(messages, tools=tools)
    print(f"✅ Content: {resp.content}")
    print(f"✅ Thinking (前200字): {resp.thinking[:200] if resp.thinking else 'None'}...")
    print(f"✅ Tool Calls: {len(resp.tool_calls) if resp.tool_calls else 0}")

    if resp.tool_calls:
        for tc in resp.tool_calls:
            print(f"   - {tc.function.name}({json.dumps(tc.function.arguments, ensure_ascii=False)})")
    print()

    # Simulate tool result and continue conversation
    if resp.tool_calls:
        messages.append(Message(
            role="assistant",
            content=resp.content or "",
            thinking=resp.thinking,
            tool_calls=resp.tool_calls,
        ))

        messages.append(Message(
            role="tool",
            tool_call_id=resp.tool_calls[0].id,
            content=json.dumps({"temperature": "25°C", "condition": "晴朗", "humidity": "45%"}, ensure_ascii=False),
        ))

        print("发送工具结果（带推理内容回传）...")
        resp2 = await client.generate(messages, tools=tools)
        print(f"✅ 最终回复: {resp2.content}")
        print(f"✅ Thinking (前200字): {resp2.thinking[:200] if resp2.thinking else 'None'}...")
        print(f"✅ Usage: {resp2.usage}")
        print()
        print("🎉 Tool Calling + 推理内容回传成功！")

    return resp


async def main():
    print("\n🚀 DeepSeek API 集成冒烟测试\n")

    try:
        # Test 1: Basic connectivity
        await test_basic_connectivity()
    except Exception as e:
        print(f"❌ 测试 1 失败: {e}")
        import traceback
        traceback.print_exc()
        return

    try:
        # Test 2: Reasoning round-trip
        await test_reasoning_roundtrip()
    except Exception as e:
        print(f"❌ 测试 2 失败: {e}")
        import traceback
        traceback.print_exc()
        return

    try:
        # Test 3: Tool calling
        await test_tool_calling()
    except Exception as e:
        print(f"❌ 测试 3 失败: {e}")
        import traceback
        traceback.print_exc()
        return

    print("\n" + "=" * 60)
    print("🎉 所有测试通过！DeepSeek API 集成工作正常。")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
