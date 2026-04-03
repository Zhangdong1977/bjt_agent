"""Tests for AI think tags stripping in document parser."""

import pytest

from backend.utils.text_utils import strip_ai_think_tags


def test_strip_ai_think_tags_basic():
    """Test basic think tag removal."""
    input_text = "这是一个描述。<think>这是思考过程</think>这是正文继续。"
    expected = "这是一个描述。这是正文继续。"
    result = strip_ai_think_tags(input_text)
    assert result == expected, f"Expected '{expected}', got '{result}'"


def test_strip_ai_think_tags_no_tags():
    """Test that content without think tags remains unchanged."""
    input_text = "这是一个正常的描述。"
    result = strip_ai_think_tags(input_text)
    assert result == input_text


def test_strip_ai_think_tags_multiple():
    """Test multiple think tags removal."""
    input_text = "开始<think>思考1</think>中间<think>思考2</think>结束"
    expected = "开始中间结束"
    result = strip_ai_think_tags(input_text)
    assert result == expected


def test_strip_ai_think_tags_multiline():
    """Test multiline think tag removal."""
    input_text = "文本开始<think>第一段\n第二段</think>文本继续"
    expected = "文本开始文本继续"
    result = strip_ai_think_tags(input_text)
    assert result == expected


def test_strip_ai_think_tags_real_format():
    """Test with actual AI think tag format using <think> and</think>."""
    input_text = "这是一个描述。<thought>这是思考过程</thought>这是正文继续。"
    expected = "这是一个描述。这是正文继续。"
    result = strip_ai_think_tags(input_text)
    assert result == expected


def test_strip_ai_think_tags_real_format_no_tags():
    """Test normal content with real format."""
    input_text = "这是一个正常的描述。"
    result = strip_ai_think_tags(input_text)
    assert result == input_text


def test_strip_ai_think_tags_real_format_multiple():
    """Test multiple think tags with real format."""
    input_text = "开始<thought>思考1</thought>中间<thought>思考2</thought>结束"
    expected = "开始中间结束"
    result = strip_ai_think_tags(input_text)
    assert result == expected


def test_strip_think_tags_angled_bracket_real():
    """Test with actual AI思考标签 format <think> and</think>."""
    input_text = "描述文本。<think>思考内容</think>继续文本。"
    expected = "描述文本。继续文本。"
    result = strip_ai_think_tags(input_text)
    assert result == expected