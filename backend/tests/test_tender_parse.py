"""招标文件解析（tender-parse）纯函数单测：结果归一化 + 字段回填定位。"""

from backend.agent.tender_parse_agent import (
    TAB_BASIC_FIELDS,
    TAB_ORDER,
    find_field_value,
    normalize_parse_result,
)


def test_normalize_fixed_tabs_fill_missing_fields_in_order():
    raw = {
        "basic": [
            {"field": "项目名称", "value": "XX 产业园运维项目", "source": "第一章 招标公告：项目名称为…"},
            {"field": "不存在的字段", "value": "应被归入尾部追加", "source": ""},
        ],
        # qualification 整组缺失 → 全字段空行补全
    }
    result = normalize_parse_result(raw)
    assert list(result.keys()) == list(TAB_ORDER)
    basic_fields = [row["field"] for row in result["basic"]]
    assert basic_fields[: len(TAB_BASIC_FIELDS)] == list(TAB_BASIC_FIELDS)
    # 缺失字段补空行（内容/原文位置为空）
    assert {"field": "开标时间", "value": "", "source": ""} in result["basic"]
    # 固定字段外但有值的行追加在尾部
    assert result["basic"][-1]["field"] == "不存在的字段"
    assert all(row["value"] == "" for row in result["qualification"])


def test_normalize_dynamic_tabs_drop_empty_rows_and_bad_rows():
    raw = {
        "scoring": [
            {"field": "评标方法", "value": "综合评分法", "source": "第三章 评标办法：本项目采用综合评分法"},
            {"field": "价格分", "value": "", "source": "不应出现（空内容行丢弃）"},
            {"field": "", "value": "技术分 40 分", "source": "评分细则表"},
            "不是字典的行",
        ],
        "technical": [],
        "others": None,
    }
    result = normalize_parse_result(raw)
    scoring = result["scoring"]
    assert len(scoring) == 2
    assert scoring[0]["field"] == "评标方法"
    # 空字段名 → Tab 兜底标签
    assert scoring[1]["field"] == "评分办法"
    assert result["technical"] == []
    assert result["others"] == []


def test_normalize_cleans_str_fields_and_truncates():
    long_value = "长" * 10_000
    raw = {"basic": [{"field": 123, "value": long_value, "source": None}]}
    result = normalize_parse_result(raw)
    # 非固定字段名 → 转字符串后追加在固定字段尾部
    row = result["basic"][-1]
    assert row["field"] == "123"
    assert len(row["value"]) <= 4_000
    assert row["source"] == ""


def test_find_field_value_matches_fixed_label():
    result = normalize_parse_result(
        {"basic": [{"field": "招标方名称", "value": "西安经发集团有限公司", "source": ""}]}
    )
    assert find_field_value(result, "basic", "招标方名称") == "西安经发集团有限公司"
    assert find_field_value(result, "basic", "预算金额") == ""
