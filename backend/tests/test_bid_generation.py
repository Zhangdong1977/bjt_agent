"""Unit tests for bid-draft / polish generation primitives."""

from decimal import Decimal
from types import SimpleNamespace

from backend.agent.bid_draft_agent import normalize_outline, parse_json_payload, strip_code_fence
from backend.services.sales import multiplier_for_task
from backend.services.task_lifecycle import _TASK_NAMES, _TASK_QUEUES
from backend.tasks.polish_tasks import build_messages


def test_normalize_outline_assigns_hierarchical_ids():
    outline = normalize_outline(
        [
            {"title": "技术方案", "level": 1},
            {"title": "总体设计", "level": 2},
            {"title": "网络架构", "level": 2},
            {"title": "施工组织设计", "level": 1},
            {"title": "   ", "level": 2},  # blank title dropped
        ]
    )
    assert [node["node_id"] for node in outline] == ["1", "1.1", "1.2", "2"]


def test_normalize_outline_clamps_settings():
    outline = normalize_outline(
        [{"title": "售后承诺", "level": 9, "article_count": 99, "text_count": 10}]
    )
    node = outline[0]
    assert node["level"] == 6
    assert node["article_count"] == 8
    assert node["text_count"] == 100


def test_parse_json_payload_tolerant():
    assert parse_json_payload('{"a": 1}') == {"a": 1}
    assert parse_json_payload('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_payload('前言 {"a": [1, 2]} 后记') == {"a": [1, 2]}
    assert parse_json_payload("[1, 2]") == [1, 2]
    assert parse_json_payload("不是 JSON") is None
    assert parse_json_payload("") is None


def test_strip_code_fence():
    assert strip_code_fence("```\n正文\n```") == "正文"
    assert strip_code_fence("正文") == "正文"


def test_multiplier_fallback_for_new_kinds():
    config = SimpleNamespace(
        sales_multiplier=Decimal("4"),
        review_multiplier=None,
        duplicate_multiplier=Decimal("2"),
        blind_check_multiplier=None,
        bid_draft_multiplier=None,
        polish_multiplier=Decimal("0"),
    )
    # Unset bid_draft falls back to the global multiplier.
    assert multiplier_for_task(config, "bid_draft") == Decimal("4")
    # A configured 0 means "free feature" and must NOT fall back to global.
    assert multiplier_for_task(config, "polish") == Decimal("0")
    assert multiplier_for_task(config, "duplicate") == Decimal("2")


def test_dispatch_registry_queues():
    assert _TASK_NAMES["bid_draft"] == "backend.tasks.bid_draft_tasks.run_bid_draft"
    assert _TASK_NAMES["polish"] == "backend.tasks.polish_tasks.run_polish"
    assert _TASK_QUEUES["bid_draft"] == "generation"
    assert _TASK_QUEUES["polish"] == "review"
    assert _TASK_QUEUES["review"] == "review"


def test_polish_messages_carry_mode_and_requirements():
    snapshot = {
        "mode": "expand",
        "input_text": "本项目采用先进的运维体系。",
        "requirements": "更技术化",
        "target_length": 800,
    }
    messages = build_messages(snapshot)
    assert messages[0].role == "system"
    user = messages[1].content
    assert "专业扩写" in user
    assert "本项目采用先进的运维体系。" in user
    assert "更技术化" in user
    assert "800" in user


def test_polish_messages_default_mode():
    snapshot = {"mode": "unknown-mode", "input_text": "片段"}
    messages = build_messages(snapshot)
    assert "专业润色" in messages[1].content


def test_node_sort_key_orders_naturally():
    from backend.api.bid_draft import _node_sort_key

    ids = ["1", "10", "10.1", "10.2", "1.1", "2", "2.1", "5.2.1", "5.10", "5.2"]
    assert sorted(ids, key=_node_sort_key) == [
        "1", "1.1", "2", "2.1", "5.2", "5.2.1", "5.10", "10", "10.1", "10.2",
    ]


def test_normalize_outline_strips_numeric_title_prefix():
    outline = normalize_outline(
        [
            {"title": "第一章 投标函及承诺函", "level": 1},
            {"title": "1.1 投标函", "level": 2},
            {"title": "10.2其他需要补充的材料", "level": 2},
            {"title": "2024年度业绩一览", "level": 2},
        ]
    )
    assert outline[0]["title"] == "第一章 投标函及承诺函"
    assert outline[1]["title"] == "投标函"
    # 无分隔符的编号暂不剥（保守），但不影响 node_id 编号语义
    assert outline[2]["title"] == "10.2其他需要补充的材料"
    assert outline[3]["title"] == "2024年度业绩一览"
