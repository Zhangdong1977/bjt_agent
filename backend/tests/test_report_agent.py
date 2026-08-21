"""OverallReportAgent 纯函数单测：输入组装 / 评级矩阵 / 报告组装与降级 / JSON 解析.

不依赖数据库与 LLM——build_report_input / assemble_report / compute_rejection_risk
均为纯函数，LLM 部分通过注入输出 dict 验证校验与降级路径。
"""

from backend.agent.report_agent import (
    REJECTION_RULE_DOC_CODES,
    _parse_json,
    assemble_report,
    build_report_input,
    compute_rejection_risk,
)


def _todo(name, status="completed", check_items=None, result=None):
    return {
        "rule_doc_name": name,
        "status": status,
        "check_items": check_items or [{"index": 1, "name": "x"}],
        "result": result,
    }


def _finding(rule_doc, check_item, severity, explanation="问题说明", compliant=False, **kw):
    return {
        "rule_doc_name": rule_doc,
        "check_item_name": check_item,
        "requirement_key": f"req_{abs(hash(check_item)) % 1000}",
        "requirement_content": kw.get("requirement", "招标要求原文"),
        "is_compliant": compliant,
        "severity": severity,
        "explanation": explanation,
        "location_page": None,
        "location_line": None,
        "suggestion": None,
    }


# ---------------------------------------------------------------------------
# build_report_input
# ---------------------------------------------------------------------------

def test_build_input_groups_and_dedup_by_check_item():
    todos = [
        _todo("A009 明确否决条款检查.md", check_items=[{"index": 1}, {"index": 2}]),
        _todo("A004 投标函检查.md", check_items=[{"index": 1}]),
    ]
    findings = [
        # 同一检查项两条 finding → 合并为一组，取更重等级
        _finding("A009 明确否决条款检查.md", "集中列出否决条款的响应检查", "major", "承诺函仅响应3/5项"),
        _finding("A009 明确否决条款检查.md", "集中列出否决条款的响应检查", "critical", "承诺函仅响应3/5项"),
        _finding("A004 投标函检查.md", "投标函填写", "minor", "金额大小写不一致"),
        # 合规项应被忽略
        _finding("A004 投标函检查.md", "已合规项", "minor", compliant=True),
    ]
    report_input = build_report_input(todos, findings)

    assert report_input["summary"]["category_count"] == 2
    assert report_input["summary"]["check_item_count"] == 3
    assert report_input["summary"]["risk_item_count"] == 2
    assert report_input["summary"]["severity_dist"] == {"critical": 1, "major": 0, "minor": 1}

    by_code = {c["code"]: c for c in report_input["categories"]}
    assert by_code["A009"]["risk_groups"][0]["severity"] == "critical"
    assert by_code["A009"]["risk_groups"][0]["count"] == 2
    assert by_code["A009"]["rejection_by_rule"] is True
    assert by_code["A004"]["rejection_by_rule"] is False
    # 风险项 id 按大类编码统一编号
    assert by_code["A009"]["risk_groups"][0]["id"] == "A009#1"


def test_build_input_normalizes_unknown_severity_to_major():
    todos = [_todo("A010 标点检查.md")]
    findings = [_finding("A010 标点检查.md", "标点规范", "weird-level")]
    report_input = build_report_input(todos, findings)
    assert report_input["categories"][0]["risk_groups"][0]["severity"] == "major"


def test_build_input_collects_failed_categories_and_score_evidence():
    todos = [
        _todo("A003 基本招标要求符合性检查.md", status="failed"),
        _todo(
            "D001 业绩得分检查.md",
            result={"findings": [
                {"check_item_name": "业绩汇总表检查", "explanation": "业绩满分 8 分，可得 5 分，损失 3 分", "is_compliant": True},
                {"check_item_name": "证明材料", "explanation": "1 项框架合同缺订单", "is_compliant": False},
            ]},
        ),
    ]
    report_input = build_report_input(todos, [])
    assert report_input["summary"]["failed_categories"] == ["A003 基本招标要求符合性检查"]
    by_code = {c["code"]: c for c in report_input["categories"]}
    assert by_code["D001"]["is_scoring"] is True
    assert len(by_code["D001"]["score_evidence"]) == 2
    assert by_code["D001"]["score_evidence"][0]["note"].startswith("业绩满分")


def test_build_input_includes_findings_without_todo():
    findings = [_finding("Z999 未注册大类.md", "检查项", "major")]
    report_input = build_report_input([], findings)
    assert report_input["categories"][0]["code"] == "Z999"
    assert report_input["summary"]["category_count"] == 0
    assert report_input["summary"]["risk_item_count"] == 1


# ---------------------------------------------------------------------------
# 评级矩阵
# ---------------------------------------------------------------------------

def _sections(critical_entries=(), major_entries=(), minor_entries=()):
    return {
        "critical": list(critical_entries),
        "major": list(major_entries),
        "minor": list(minor_entries),
    }


def test_rating_high_when_rejection_related_critical():
    sections = _sections(critical_entries=[
        {"rule_doc": "A009 明确否决条款检查", "count": 2, "rejection_related": True},
    ])
    level, reason = compute_rejection_risk(sections, None)
    assert level == "高"
    assert "废标" in reason or "否决" in reason


def test_rating_medium_when_critical_but_no_rejection():
    sections = _sections(
        critical_entries=[{"rule_doc": "A010 标点检查", "count": 1, "rejection_related": False}],
        major_entries=[{"rule_doc": "A008 投标报价检查", "count": 2}],
    )
    level, _ = compute_rejection_risk(sections, None)
    assert level == "中"


def test_rating_medium_when_major_only():
    sections = _sections(major_entries=[{"rule_doc": "A008 投标报价检查", "count": 1}])
    level, _ = compute_rejection_risk(sections, None)
    assert level == "中"


def test_rating_low_when_minor_only_or_clean():
    assert compute_rejection_risk(_sections(minor_entries=[{"count": 3}]), None)[0] == "低"
    assert compute_rejection_risk(_sections(), None)[0] == "低"


# ---------------------------------------------------------------------------
# assemble_report：LLM 校验 / 降级 / 评分过滤
# ---------------------------------------------------------------------------

def _sample_input():
    todos = [
        _todo("A009 明确否决条款检查.md"),
        _todo("A004 投标函检查.md"),
        _todo("D001 业绩得分检查.md"),
    ]
    findings = [
        _finding("A009 明确否决条款检查.md", "否决条款响应", "critical", "承诺函仅响应3/5项"),
        _finding("A004 投标函检查.md", "投标函盖章", "critical", "投标函未加盖法人章"),
        _finding("A004 投标函检查.md", "报价一致性", "major", "大小写不一致"),
    ]
    return build_report_input(todos, findings)


def test_assemble_uses_llm_summary_and_rejection_judgment():
    llm_output = {
        "category_summaries": {
            "A009": {"critical": "集中否决条款承诺函仅响应3项、遗漏2项"},
            "A004": {"critical": "投标函未按要求加盖法人章", "major": "报价大小写不一致"},
        },
        # A004 盖章属于典型废标情形 → LLM 判定涉及
        "rejection_judgments": {"A004#1": True},
        "rejection_reason": "承诺函响应不全与投标函未盖章均触及否决条款",
        "score_items": [{"code": "D001", "name": "业绩得分检查", "full_score": 8, "estimated_score": 5, "note": "缺1项订单"}],
    }
    report = assemble_report(_sample_input(), llm_output)

    assert report["degraded"] is False
    assert report["rejection_risk"]["level"] == "高"
    assert report["rejection_risk"]["reason"] == "承诺函响应不全与投标函未盖章均触及否决条款"

    crit = report["risk_sections"]["critical"]
    a009 = next(e for e in crit if e["rule_doc_code"] == "A009")
    a004 = next(e for e in crit if e["rule_doc_code"] == "A004")
    # A009 规则标记 → rejection_related；A004 走 LLM 判定
    assert a009["rejection_related"] is True and a009["summary"].startswith("集中否决条款")
    assert a004["rejection_related"] is True and a004["summary"] == "投标函未按要求加盖法人章"

    assert report["score_items"] == [
        {"code": "D001", "name": "业绩得分检查", "full_score": 8.0, "estimated_score": 5.0, "note": "缺1项订单"},
    ]


def test_assemble_degrades_when_llm_missing_or_invalid():
    report_input = _sample_input()

    # LLM 整体失败 → 降级，摘要取原文摘录，评级仍按规则（A009 规则标记 → 高）
    degraded = assemble_report(report_input, None, degraded=True)
    assert degraded["degraded"] is True
    assert degraded["rejection_risk"]["level"] == "高"
    a009 = next(e for e in degraded["risk_sections"]["critical"] if e["rule_doc_code"] == "A009")
    assert "承诺函" in a009["summary"]

    # LLM 输出结构不合法（summaries 非法、score_items 幻觉非 D 类）→ 逐项降级
    bad = assemble_report(report_input, {
        "category_summaries": "not-a-dict",
        "score_items": [{"code": "A001", "name": "幻觉项", "full_score": 3, "estimated_score": 1}],
    })
    assert bad["risk_sections"]["critical"][0]["summary"]  # fallback 文本非空
    assert bad["score_items"] == []  # 非 D 类被过滤


def test_assemble_critical_without_rejection_falls_to_medium():
    todos = [_todo("A010 标点检查.md")]
    findings = [_finding("A010 标点检查.md", "标点", "critical", "逗号使用不规范")]
    report = assemble_report(build_report_input(todos, findings), {})
    assert report["rejection_risk"]["level"] == "中"


def test_assemble_rule_based_rejection_codes():
    assert "A009" in REJECTION_RULE_DOC_CODES
    assert "B001" in REJECTION_RULE_DOC_CODES
    assert "C001" in REJECTION_RULE_DOC_CODES


# ---------------------------------------------------------------------------
# _parse_json 容错
# ---------------------------------------------------------------------------

def test_parse_json_plain_and_fenced():
    assert _parse_json('{"a": 1}') == {"a": 1}
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('前置说明 {"a": {"b": 2}} 后置') == {"a": {"b": 2}}
    assert _parse_json("不是JSON") is None
    assert _parse_json("[1,2]") is None


# ---------------------------------------------------------------------------
# PDF 导出：总体报告版式 / 旧版式回归
# ---------------------------------------------------------------------------

def _pdf_groups():
    return [{
        "label": "A004 投标函检查",
        "is_compliant": False,
        "non_compliant_count": 1,
        "findings": [{
            "check_item_name": "投标函盖章",
            "requirement_key": "req_1",
            "requirement_content": "投标函须加盖法人章",
            "bid_content": "未盖章",
            "is_compliant": False,
            "severity": "critical",
            "location_page": 3,
            "location_line": None,
            "suggestion": "补盖公章",
            "explanation": "投标函未加盖法人章",
        }],
    }]


def test_pdf_with_overall_report_renders():
    from backend.services.pdf_export import build_review_pdf

    report = assemble_report(_sample_input(), {
        "category_summaries": {"A004": {"critical": "投标函未按要求加盖法人章"}},
        "score_items": [{"code": "D001", "name": "业绩得分检查", "full_score": 8, "estimated_score": 5, "note": "缺1项订单"}],
    })
    pdf_bytes = build_review_pdf(
        "测试项目", None,
        {"category_count": 3, "check_item_count": 3, "risk_item_count": 3},
        _pdf_groups(), overall_report=report,
    )
    assert pdf_bytes[:5] == b"%PDF-"


def test_pdf_without_overall_report_keeps_legacy_layout():
    from backend.services.pdf_export import build_review_pdf

    pdf_bytes = build_review_pdf(
        "测试项目", None,
        {"category_count": 1, "check_item_count": 1, "risk_item_count": 1},
        _pdf_groups(),
    )
    assert pdf_bytes[:5] == b"%PDF-"
