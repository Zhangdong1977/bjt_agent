"""Unit tests for AI编标（bid-wizard）primitives.

风格照 test_bid_generation.py：纯函数 + 注册表断言（HTTP 层由 E2E 覆盖）。
"""

from decimal import Decimal
from types import SimpleNamespace

from backend.agent.bid_wizard_agent import (
    _bound_suggested_materials,
    build_chart_plan_text,
    build_material_index_text,
    build_requirements_text,
    estimate_index_points,
    estimate_tokens,
    merge_questionnaire_answers,
    normalize_questionnaire,
    normalize_spec,
    parse_chunk_ref_mapping,
    parse_chunk_refs,
    render_chunk_file,
    render_index_markdown,
    split_material_chunks,
)
from backend.models import TASK_MODEL_BY_KIND, BidWritingTask, BidWizardIndexTask, BidWizardQaTask
from backend.services.sales import multiplier_for_task
from backend.services.task_lifecycle import _TASK_NAMES, _TASK_QUEUES


# ------------------------------------------------------------------ 分段索引


def test_split_material_chunks_by_headings_and_size():
    markdown = (
        "# 公司简介\n\n我司成立于2010年，专注于智慧城市领域。\n\n"
        "## 业绩\n\n" + "某项目交付内容。" * 200 + "\n\n"
        "## 资质\n\nISO9001 认证。"
    )
    chunks = split_material_chunks(markdown, max_chars=500)
    assert len(chunks) >= 3
    assert chunks[0]["heading"] == "公司简介"
    assert chunks[0]["no"] == 1
    # 超长段落被硬切，每段不超过上限（允许切点回退）
    assert all(len(chunk["text"]) <= 500 for chunk in chunks)
    headings = [chunk["heading"] for chunk in chunks]
    assert "业绩" in headings and "资质" in headings
    # location = 标题路径（§5.3 frontmatter「原文定位」），子标题带父级链
    assert chunks[0]["location"] == "公司简介"
    performance = next(chunk for chunk in chunks if chunk["heading"] == "业绩")
    assert performance["location"] == "公司简介 > 业绩"


def test_split_material_chunks_empty_and_numbering():
    assert split_material_chunks("") == []
    # 小段合并且无标题边界：两个短段打包进同一 chunk
    chunks = split_material_chunks("第一段。\n\n第二段。")
    assert [chunk["no"] for chunk in chunks] == [1]
    assert "第一段" in chunks[0]["text"] and "第二段" in chunks[0]["text"]
    # 无标题的段落回退为「第 N 段」定位
    assert chunks[0]["location"] == "第1段"


def test_render_index_and_chunk_files():
    chunks = [
        {"no": 1, "heading": "业绩", "location": "公司简介 > 业绩", "text": "某智慧园区项目，合同额 1200 万。"}
    ]
    metas = {1: {"summary": "智慧园区业绩", "keywords": ["智慧园区", "1200万"]}}
    index_md = render_index_markdown(chunks, metas)
    assert "智慧园区业绩" in index_md and "1200万" in index_md
    chunk_file = render_chunk_file(chunks[0], metas[1])
    assert chunk_file.startswith("<!--")
    assert "location: 公司简介 > 业绩" in chunk_file
    assert "某智慧园区项目" in chunk_file


def test_estimate_tokens_magnitude():
    assert estimate_tokens(10_000) == 7_000
    assert estimate_tokens(0) == 1


def test_estimate_index_points_positive_and_monotonic():
    small = estimate_index_points(1_000, Decimal("1"))
    large = estimate_index_points(100_000, Decimal("1"))
    # 价目缺失的环境允许 None；有值则必须为正且随 token 量级不减
    if small is not None:
        assert small >= 1
    if small is not None and large is not None:
        assert large >= small
    # 倍率放大点数（同口径线性）
    if small is not None:
        doubled = estimate_index_points(1_000, Decimal("2"))
        assert doubled is not None and doubled >= small


def test_bound_suggested_materials_shape_and_bounds():
    items = _bound_suggested_materials(
        [
            {"name": "类似业绩合同", "reason": "资格要求近三年类似业绩不少于 2 项"},
            {"name": "", "reason": "空名丢弃"},
            "not-a-dict",
            *[
                {"name": f"素材{i}", "reason": "x" * 500}
                for i in range(10)  # 超出 8 项截断
            ],
        ]
    )
    assert len(items) == 8
    assert items[0] == {"name": "类似业绩合同", "reason": "资格要求近三年类似业绩不少于 2 项"}
    assert all(len(item["reason"]) <= 200 for item in items)
    assert _bound_suggested_materials(None) == []
    assert _bound_suggested_materials(["x"]) == []


# ------------------------------------------------------------------ 问卷与需求


def test_normalize_questionnaire_ids_and_bounds():
    payload = {
        "questions": [
            {"question": "交付周期多长？", "topic": "交付", "why": "招标要求 60 天内交付",
             "suggested_answer": "45 天（来源：素材#3）", "source": "素材#3", "inferred": False},
            {"question": "", "topic": "商务"},  # 空问题被丢弃
            {"question": "项目经理是谁？", "suggested_answer": "建议张三（常见做法）", "inferred": True},
        ]
    }
    questionnaire = normalize_questionnaire(payload)
    assert [q["id"] for q in questionnaire["questions"]] == ["q1", "q2"]
    assert questionnaire["questions"][0]["action"] is None
    assert questionnaire["questions"][1]["inferred"] is True
    assert normalize_questionnaire({"questions": []}) == {"questions": []}


def test_merge_questionnaire_answers_actions():
    questionnaire = normalize_questionnaire(
        {"questions": [
            {"question": "交付周期？", "suggested_answer": "45 天", "inferred": False},
            {"question": "报价策略？", "suggested_answer": "按成本加成 8%", "inferred": True},
            {"question": "项目经理？", "suggested_answer": "张三", "inferred": True},
        ]}
    )
    merged = merge_questionnaire_answers(
        questionnaire["questions"] and {"questions": questionnaire["questions"]},
        [
            {"question_id": "q1", "action": "answered", "answer": "50 天"},
            {"question_id": "q2", "action": "adopted", "answer": None},
            {"question_id": "q3", "action": "skipped", "answer": None},
            {"question_id": "q404", "action": "answered", "answer": "无效 id 忽略"},
        ],
    )
    questions = merged["questions"]
    assert questions[0]["effective_answer"] == "50 天"
    assert questions[1]["effective_answer"] == "按成本加成 8%"
    assert questions[2]["effective_answer"] is None


def test_build_requirements_text_skips_unanswered_and_flags_inferred():
    requirements = {
        "questions": [
            {"topic": "交付", "question": "交付周期？", "effective_answer": "50 天", "inferred": False, "action": "answered"},
            {"topic": "商务", "question": "报价策略？", "effective_answer": "按成本加成 8%", "inferred": True, "action": "adopted"},
            {"topic": "人员", "question": "项目经理？", "effective_answer": None, "action": "skipped"},
        ]
    }
    text = build_requirements_text(requirements)
    assert "50 天" in text
    assert "AI 推断，请确认" in text  # adopted + inferred 标记
    assert "项目经理" not in text  # skipped 不进下游
    assert build_requirements_text(None) == "无"


# ------------------------------------------------------------------ Spec


def test_normalize_spec_keeps_summary_and_charts():
    spec = normalize_spec(
        [
            {"title": "1.1 公司简介", "level": 2, "summary": "引用业绩素材",
             "article_count": 3, "text_count": 500,
             "charts": [{"type": "mermaid", "title": "组织架构图", "points": "三层架构"},
                        {"type": "bogus", "title": "异常类型归一为表格"},
                        {"type": "table", "title": ""}]},
            {"title": "施工组织设计", "level": 1},
            {"title": "深层跳级", "level": 6},
        ]
    )
    # 首节点 level=2 被平滑为 1（防 "0.1"）；跳级被夹到 prev+1
    assert [node["node_id"] for node in spec] == ["1", "2", "2.1"]
    assert spec[0]["title"] == "公司简介"  # 数字前缀剥离 + summary/charts 双键匹配
    assert spec[0]["summary"] == "引用业绩素材"
    charts = spec[0]["charts"]
    assert charts is not None and len(charts) == 2  # 空标题图表被丢弃
    assert charts[0]["type"] == "mermaid" and charts[0]["points"] == "三层架构"
    assert charts[1]["type"] == "table"  # 非法类型归一
    assert spec[2]["level"] == 2
    assert normalize_spec([]) == []


def test_build_chart_plan_text():
    assert "无" in build_chart_plan_text({"charts": None})
    text = build_chart_plan_text(
        {"charts": [{"type": "table", "title": "开标一览表", "points": "报价构成"},
                    {"type": "mermaid", "title": "进度横道图", "points": None}]}
    )
    assert "[表格] 开标一览表：报价构成" in text
    assert "[mermaid 图] 进度横道图" in text


# ------------------------------------------------------------------ 素材检索 refs


def test_parse_chunk_refs_validation():
    valid = {"doc-1", "doc-2"}
    refs = parse_chunk_refs(
        {"chunks": ["doc-1#3", "doc-2#01", "doc-1#x", "doc-404#1", "doc-1#3", "bad"]},
        valid,
    )
    assert refs == ["doc-1#3", "doc-2#1"]
    assert len(parse_chunk_refs({"chunks": [f"doc-1#{i}" for i in range(100)]}, valid)) == 60


def test_parse_chunk_ref_mapping_only_known_nodes():
    mapping = parse_chunk_ref_mapping(
        {"mapping": {"1": ["doc-1#2"], "404": ["doc-1#1"], "2": "bad"}},
        {"doc-1"},
        ["1", "2"],
    )
    assert mapping == {"1": ["doc-1#2"], "2": []}


def test_build_material_index_text_prefixes_docs():
    text = build_material_index_text([("doc-1", "业绩.docx", "| 1 | 业绩 | 摘要 | 关键词 |")])
    assert text.startswith("【素材 doc-1｜业绩.docx】")


# ------------------------------------------------------------------ 注册表


def test_dispatch_registry_wizard_kinds():
    assert _TASK_NAMES["bid_wizard_index"] == "backend.tasks.bid_wizard_tasks.run_bid_wizard_index"
    assert _TASK_NAMES["bid_wizard_write"] == "backend.tasks.bid_wizard_tasks.run_bid_wizard_write"
    assert _TASK_QUEUES["bid_wizard_index"] == "generation"
    assert _TASK_QUEUES["bid_wizard_write"] == "generation"
    # 同步问答微任务不走 outbox（不应出现在派发注册表里）
    assert "bid_wizard_qa" not in _TASK_NAMES


def test_task_model_registry_wizard_kinds():
    assert TASK_MODEL_BY_KIND["bid_wizard_qa"] is BidWizardQaTask
    assert TASK_MODEL_BY_KIND["bid_wizard_index"] is BidWizardIndexTask
    assert TASK_MODEL_BY_KIND["bid_wizard_write"] is BidWritingTask


def test_multiplier_fallback_wizard_kinds():
    config = SimpleNamespace(
        sales_multiplier=Decimal("4"),
        review_multiplier=None,
        duplicate_multiplier=None,
        blind_check_multiplier=None,
        bid_draft_multiplier=None,
        polish_multiplier=None,
        bid_wizard_qa_multiplier=None,
        bid_wizard_index_multiplier=None,
        bid_wizard_write_multiplier=Decimal("2"),
    )
    assert multiplier_for_task(config, "bid_wizard_qa") == Decimal("4")
    assert multiplier_for_task(config, "bid_wizard_write") == Decimal("2")


def test_celery_routes_wizard_tasks():
    from backend.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert routes["backend.tasks.bid_wizard_tasks.run_bid_wizard_index"] == {"queue": "generation"}
    assert routes["backend.tasks.bid_wizard_tasks.run_bid_wizard_write"] == {"queue": "generation"}
    annotations = celery_app.conf.task_annotations
    assert annotations["backend.tasks.bid_wizard_tasks.run_bid_wizard_write"]["time_limit"] == 7200
