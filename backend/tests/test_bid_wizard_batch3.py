"""批次二余项 + 批次三（23 号 O3/O4/O5/O7，2026-10-23）单元与 API 测试。

- O7 历史标书目录参照：标题树提取/索引注入/spec prompt 注入；
- O4 口径卡：归一/渲染进编写需求/机检口径卡驱动（机检用例在 test_bid_doc_checks）；
- O3 应答口径：capability_map 归一/措辞注入/existing 无依据红线/生成-保存 API；
- O5 审读-修订轮：分批与 findings 归一/预估口径/创建与守卫 API。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select


class _FakeJsonLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def generate_json(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self.payload


# ------------------------------------------------------------------ O7 目录参照


def test_is_history_template_category_matches_both_shapes():
    from backend.agent.bid_wizard_agent import is_history_template_category

    # 本地上传传分类 id（HistoryDoc），客户端上传传分类名（素材-历史模板）——两种都算
    assert is_history_template_category("素材-历史模板")
    assert is_history_template_category("HistoryDoc")
    assert not is_history_template_category("企业-财务报表")
    assert not is_history_template_category("")
    assert not is_history_template_category(None)


def test_extract_toc_tree_levels_indent_and_cap():
    from backend.agent.bid_wizard_agent import extract_toc_tree

    markdown = (
        "# 总体建设方案\n\n正文。\n\n## 1.1 系统架构\n\n正文。\n\n### 1.1.1 网络架构\n\n"
        "## 1.2 实施方案\n\n##### 五级标题不进目录\n\n# 售后服务\n\n正文。"
    )
    tree = extract_toc_tree(markdown)
    lines = tree.split("\n")
    assert lines[0] == "- 总体建设方案"
    assert "  - 1.1 系统架构" in lines
    assert "    - 1.1.1 网络架构" in lines
    assert "  - 1.2 实施方案" in lines
    assert "- 售后服务" in lines
    assert not any("五级标题" in line for line in lines)  # 5-6 级标题不进目录树
    assert extract_toc_tree("") == ""
    assert extract_toc_tree("没有任何标题的正文。") == ""
    capped = extract_toc_tree(markdown, max_headings=2)
    assert "已截断" in capped


def test_build_toc_reference_block_collects_marked_docs_only():
    from backend.agent.bid_wizard_agent import TOC_REFERENCE_HEADING, build_toc_reference_block

    marked = (
        f"{TOC_REFERENCE_HEADING}\n- 总体方案\n  - 系统架构\n\n"
        "## 素材正文\n| 编号 | 标题 |\n| --- | --- |\n| 1 | 概述 |"
    )
    entries = [
        ("d1", "历史标书A.pdf", marked),
        ("d2", "普通素材.pdf", "| 编号 | 标题 |\n| --- | --- |"),
    ]
    block = build_toc_reference_block(entries)
    assert "历史标书目录参照" in block
    assert "历史标书A.pdf" in block
    assert "- 总体方案" in block
    assert "素材正文" not in block  # 只取目录小节，不吞素材正文表
    # 无历史模板素材 → 空串（spec 模板占位替换为空、行为与存量一致）
    assert build_toc_reference_block([("d2", "普通素材.pdf", "| 编号 |")]) == ""
    assert build_toc_reference_block([]) == ""


def test_write_material_index_prepends_toc_section(tmp_path):
    from backend.agent.bid_wizard_agent import (
        TOC_REFERENCE_HEADING,
        split_material_chunks,
        write_material_index,
    )

    chunks = split_material_chunks("# 公司简介\n\n专注智慧城市。")
    write_material_index(tmp_path, "w1", "d1", chunks, {}, toc_text="- 总体方案\n  - 系统架构")
    index_md = (tmp_path / "bid-wizard" / "w1" / "materials" / "d1" / "index.md").read_text(encoding="utf-8")
    assert index_md.startswith(TOC_REFERENCE_HEADING)
    assert "- 总体方案" in index_md
    assert "| 编号 |" in index_md  # 目录小节之后仍是分段索引表
    write_material_index(tmp_path, "w1", "d2", chunks, {})
    plain = (tmp_path / "bid-wizard" / "w1" / "materials" / "d2" / "index.md").read_text(encoding="utf-8")
    assert TOC_REFERENCE_HEADING not in plain


async def test_generate_spec_injects_toc_reference():
    from backend.agent.bid_wizard_agent import generate_spec

    llm = _FakeJsonLLM([{"title": "总体方案", "level": 1, "summary": "响应总体要求"}])
    spec = await generate_spec(
        llm,
        analysis={"basic": {}},
        requirements_text="无",
        material_index_text="（无素材）",
        toc_reference_text="## 历史标书目录参照（仅作层级与覆盖面参照…）\n### 历史标书A\n- 总体方案\n",
    )
    assert spec and spec[0]["title"] == "总体方案"
    user_prompt = llm.calls[0][1]
    assert "历史标书目录参照" in user_prompt
    assert "- 总体方案" in user_prompt
    assert "__TOC_REFERENCE__" not in user_prompt  # 占位已被替换


def test_spec_user_template_carries_placeholder():
    from backend.agent.bid_wizard_agent import SPEC_USER_TEMPLATE

    # 反馈⑧口径：模板占位只增不改——存量调用不传 toc 时替换为空串即回退旧行为
    assert "__TOC_REFERENCE__" in SPEC_USER_TEMPLATE


# ------------------------------------------------------------------ O4 口径卡


def test_normalize_style_card_defaults_bounds_and_none():
    from backend.agent.bid_wizard_agent import SELF_REFERENCE_CHOICES, normalize_style_card

    assert normalize_style_card(None) is None
    assert normalize_style_card("bad") is None
    # 全空且未指定称谓 → None（存量无口径卡语义）
    assert normalize_style_card({}) is None
    # 非法称谓回退「我司」；显式只选称谓也是有效口径卡（O4 机检驱动项）
    card = normalize_style_card({"self_reference": "本公司", "forbidden_words": []})
    assert card is not None and card["self_reference"] == "本公司"
    assert normalize_style_card({"self_reference": "乱写"})["self_reference"] == "我司"
    assert SELF_REFERENCE_CHOICES == ("我司", "我公司", "我方", "本公司")
    card = normalize_style_card(
        {
            "company_name": "x" * 500,
            "forbidden_words": ["现有支持", "现有支持", "行业领先", "", "a" * 80],
            "notes": "n",
        }
    )
    assert card["company_name"] == "x" * 200
    assert card["forbidden_words"] == ["现有支持", "行业领先", "a" * 50]


def test_build_style_card_text_renders_all_fields():
    from backend.agent.bid_wizard_agent import build_style_card_text

    text = build_style_card_text(
        {
            "self_reference": "我司",
            "company_name": "示例科技",
            "project_short_name": "智慧园区项目",
            "forbidden_words": ["现有支持"],
            "notes": "统一用交付口径",
        }
    )
    assert text.startswith("- [口径卡] ")
    assert "「我司」" in text and "示例科技" in text and "智慧园区项目" in text
    assert "现有支持" in text and "统一用交付口径" in text
    assert build_style_card_text(None) == ""
    assert build_style_card_text({"self_reference": "我司"})  # 只有默认称谓也渲染


def test_build_requirements_text_renders_style_card_and_capability():
    from backend.agent.bid_wizard_agent import build_requirements_text

    text = build_requirements_text(
        {
            "style_card": {
                "self_reference": "我司",
                "company_name": "示例科技",
                "forbidden_words": ["现有支持"],
            },
            "capability_map": [
                {"title": "数据中台", "requirement": "支持亿级数据", "stance": "existing", "evidence": "技术白皮书#3", "confirmed": True},
                {"title": "AI 审查", "requirement": "", "stance": "build", "evidence": "", "confirmed": True},
                {"title": "待定接口", "requirement": "", "stance": "confirm", "evidence": "", "confirmed": True},
                {"title": "未拍板项", "requirement": "", "stance": "build", "evidence": "", "confirmed": False},
            ],
        }
    )
    assert "[口径卡]" in text and "「我司」" in text and "示例科技" in text and "现有支持" in text
    assert "[口径-现有已实现] 数据中台" in text and "本系统已实现" in text and "技术白皮书#3" in text
    assert "[口径-本项目建设] AI 审查" in text
    assert "[口径-待确认] 待定接口" in text and "（请补充）" in text
    assert "未拍板项" not in text  # 未确认（AI 建议草稿）不注入下游
    assert build_requirements_text({"questions": []}) == "无"  # 存量无口径数据行为不变


# ------------------------------------------------------------------ O3 应答口径


def test_normalize_capability_map_filters_business_and_bounds():
    from backend.agent.bid_wizard_agent import normalize_capability_map

    items = normalize_capability_map(
        [
            {"title": "数据中台能力", "requirement": "亿级数据接入", "stance": "existing", "evidence": "白皮书#3", "note": "n"},
            {"title": "报价要求", "requirement": "总价不得超过预算", "stance": "build"},  # 商务硬词 → 丢弃
            {"title": "资质要求", "requirement": "提供证书", "stance": "build"},  # 宽泛词无技术语境 → 丢弃
            {"title": "人员技术资质", "requirement": "项目经理职称", "stance": "confirm"},  # 技术语境豁免保留
            {"title": "接口协议", "requirement": "", "stance": "乱写值"},  # stance 非法回退 confirm
            {"requirement": "无标题丢弃"},
            "not-a-dict",
        ]
    )
    titles = [item["title"] for item in items]
    assert titles == ["数据中台能力", "人员技术资质", "接口协议"]
    assert items[0]["confirmed"] is False and items[0]["evidence"] == "白皮书#3"
    assert items[2]["stance"] == "confirm"
    assert normalize_capability_map("bad") == []


def test_capability_stance_issues_flags_confirmed_existing_without_evidence():
    from backend.agent.bid_wizard_agent import capability_stance_issues

    spec = [
        {"node_id": "5", "title": "数据中台方案", "summary": ""},
        {"node_id": "5.3", "title": "架构", "summary": ""},
    ]
    done = [{"node_id": "5", "title": "数据中台方案", "content": "# 正文"}]
    capability = [
        {"title": "数据中台", "stance": "existing", "evidence": "", "confirmed": True},
        {"title": "AI 审查", "stance": "existing", "evidence": "", "confirmed": False},  # 未确认不查
        {"title": "售后", "stance": "existing", "evidence": "服务案例#2", "confirmed": True},  # 有依据不查
    ]
    issues = capability_stance_issues(spec, done, capability)
    assert len(issues) == 1
    assert issues[0]["type"] == "capability_no_evidence"
    assert issues[0]["node_id"] == "5" and issues[0]["severity"] == "warning"
    assert "素材依据" in issues[0]["detail"]
    issues = capability_stance_issues(spec, [], capability)  # 无命中章节 → 全稿级一条
    assert len(issues) == 1 and issues[0]["node_id"] == ""
    assert capability_stance_issues(spec, done, None) == []


async def test_generate_capability_map_normalizes_payload():
    from backend.agent.bid_wizard_agent import generate_capability_map

    llm = _FakeJsonLLM(
        {
            "capability_map": [
                {"title": "数据中台", "requirement": "亿级数据", "stance": "existing", "evidence": "白皮书#3"},
                {"title": "报价要求", "stance": "build"},
            ],
            "style_card": {"self_reference": "我方", "forbidden_words": ["现有支持"]},
        }
    )
    result = await generate_capability_map(
        llm,
        analysis={"tender_requirements": ["数据中台能力"], "scoring_items": [{"title": "报价", "weight": "60", "criteria": ""}]},
        material_index_text="【素材 d1｜白皮书】",
    )
    assert [item["title"] for item in result["capability_map"]] == ["数据中台"]
    assert result["style_card"]["self_reference"] == "我方"
    user_prompt = llm.calls[0][1]
    assert "数据中台能力" in user_prompt and "【素材 d1｜白皮书】" in user_prompt
    # 全被商务过滤 → 空结果报错（用户重试/补素材，不落半截口径）
    llm2 = _FakeJsonLLM({"capability_map": [{"title": "报价", "stance": "build"}], "style_card": {}})
    with pytest.raises(RuntimeError):
        await generate_capability_map(llm2, analysis={}, material_index_text="")


# ------------------------------------------------------------------ O5 审读轮


def test_batch_review_chapters_splits_by_count_and_chars():
    from backend.agent.bid_wizard_agent import batch_review_chapters

    chapters = [
        {"node_id": "1", "content": "x" * 6_000},
        {"node_id": "2", "content": "y" * 6_000},
        {"node_id": "3", "content": "z" * 20_000},  # 单章超限独立成批
        {"node_id": "4", "content": "w" * 100},
        {"node_id": "5", "content": "v" * 100},
    ]
    batches = batch_review_chapters(chapters, max_chapters=4, max_chars=14_000)
    assert [[ch["node_id"] for ch in batch] for batch in batches] == [["1", "2"], ["3"], ["4", "5"]]
    assert batch_review_chapters([]) == []


def test_normalize_review_findings_filters_and_defaults():
    from backend.agent.bid_wizard_agent import normalize_review_findings

    payload = {
        "findings": [
            {"node_id": "5", "severity": "HIGH", "where": "第 2 段", "issue": "口径矛盾"},
            {"node_id": "9", "severity": "high", "issue": "跑偏章节丢弃"},  # node 不在本次章节集
            {"node_id": "5", "severity": "weird", "issue": "severity 回退 low"},
            {"node_id": "5", "severity": "low", "issue": ""},  # 无 issue 丢弃
            "not-a-dict",
        ]
    }
    findings = normalize_review_findings(payload, {"5"})
    assert [(f["node_id"], f["severity"]) for f in findings] == [("5", "high"), ("5", "low")]
    assert findings[0]["where"] == "第 2 段"
    assert normalize_review_findings({"findings": []}, {"5"}) == []
    assert normalize_review_findings(None, {"5"}) == []


def test_estimate_review_usage_and_points_monotonic():
    from backend.agent.bid_wizard_agent import estimate_review_points, estimate_review_usage

    small_prompt, small_completion = estimate_review_usage(10_000)
    large_prompt, large_completion = estimate_review_usage(100_000)
    assert small_prompt > 0 and small_completion > 0
    assert large_prompt > small_prompt and large_completion > small_completion
    small = estimate_review_points(10_000, Decimal("1"))
    large = estimate_review_points(100_000, Decimal("1"))
    if small is not None and large is not None:
        assert large >= small >= 1


# ------------------------------------------------------------------ O3 API：生成-保存-保留


class TestCapabilityApi:
    @pytest.fixture(autouse=True)
    def _stub_env(self, monkeypatch):
        from backend.config import get_settings
        from backend.tasks.document_parser import parse_document

        monkeypatch.setattr(parse_document, "delay", lambda *args, **kwargs: None)
        monkeypatch.setattr(get_settings(), "bid_wizard_access_mode", "enabled")

        import backend.services.sales as sales_service
        import backend.services.task_lifecycle as lifecycle

        async def _fake_async(*args, **kwargs):
            return None

        monkeypatch.setattr(lifecycle, "authorize_billable_task_start", _fake_async)
        monkeypatch.setattr(lifecycle, "finalize_task_usage", _fake_async)
        monkeypatch.setattr(sales_service, "multiplier_for_task", lambda config, kind: Decimal("1"))

        async def _fake_generate(*args, **kwargs):
            return {
                "capability_map": [
                    {"title": "数据中台", "requirement": "亿级数据", "stance": "existing", "evidence": "白皮书#3"},
                    {"title": "AI 审查", "requirement": "", "stance": "build"},
                ],
                "style_card": {"self_reference": "我司", "forbidden_words": ["现有支持"]},
            }

        import backend.agent.bid_wizard_agent as agent_module

        monkeypatch.setattr(agent_module, "generate_capability_map", _fake_generate)

    async def _prepared_wizard(self, client, auth_headers) -> dict:
        created = (
            await client.post("/api/bid-wizard/wizards", json={}, headers=auth_headers)
        ).json()
        from backend.agent.bid_wizard_agent import normalize_questionnaire
        from backend.models import BidWizard, async_session_factory, engine

        questionnaire = normalize_questionnaire(
            {"questions": [{"question": "交付周期？", "suggested_answer": "45 天", "topic": "交付"}]}
        )
        async with async_session_factory() as session:
            wizard = (
                await session.execute(select(BidWizard).where(BidWizard.id == created["id"]))
            ).scalar_one()
            wizard.analysis = {"basic": {"project_name": "口径测试"}, "tender_requirements": ["数据中台能力"]}
            wizard.questionnaire = questionnaire
            await session.commit()
        await engine.dispose()
        return created

    async def test_generate_then_save_capability_and_survive_requirements_save(
        self, client, auth_headers
    ):
        wizard = await self._prepared_wizard(client, auth_headers)
        base = f"/api/bid-wizard/wizards/{wizard['id']}"

        # 未解读的向导不能生成口径
        bare = (await client.post("/api/bid-wizard/wizards", json={}, headers=auth_headers)).json()
        rejected = await client.post(
            f"/api/bid-wizard/wizards/{bare['id']}/capability", headers=auth_headers
        )
        assert rejected.status_code == 400

        generated = await client.post(f"{base}/capability", headers=auth_headers)
        assert generated.status_code == 200, generated.text
        requirements = generated.json()["requirements"]
        assert requirements["capability_map"][0]["stance"] == "existing"
        assert requirements["capability_map"][0]["confirmed"] is False  # AI 建议=未拍板
        assert requirements["style_card"]["forbidden_words"] == ["现有支持"]

        from backend.models import BidWizard, async_session_factory, engine

        async with async_session_factory() as session:
            row = (
                await session.execute(select(BidWizard).where(BidWizard.id == wizard["id"]))
            ).scalar_one()
            row.spec = [
                {"node_id": "1", "title": "方案", "level": 1, "article_count": 2, "text_count": 400}
            ]
            await session.commit()
        await engine.dispose()

        saved = await client.put(
            f"{base}/capability",
            json={
                "items": [
                    {"title": "数据中台", "requirement": "亿级数据", "stance": "build", "evidence": "", "note": ""},
                    {"title": "AI 审查", "requirement": "", "stance": "confirm", "evidence": "", "note": ""},
                ],
                "style_card": {"self_reference": "我方", "forbidden_words": ["行业领先"]},
            },
            headers=auth_headers,
        )
        assert saved.status_code == 200, saved.text
        detail = saved.json()
        assert all(item["confirmed"] is True for item in detail["requirements"]["capability_map"])
        assert detail["requirements"]["style_card"]["self_reference"] == "我方"
        assert detail["spec_stale"] is True  # 口径变化 → 大纲过期

        empty = await client.put(
            f"{base}/capability",
            json={"items": [{"title": "  ", "stance": "build"}]},
            headers=auth_headers,
        )
        assert empty.status_code == 422

        # 口径随「保存作答」保留（merge 基于问卷重建，不带回会整列丢失）
        answers = await client.put(
            f"{base}/requirements",
            json={"answers": [{"question_id": "q1", "action": "adopted", "answer": None}]},
            headers=auth_headers,
        )
        assert answers.status_code == 200
        kept = answers.json()["requirements"]
        assert kept.get("capability_map") and kept["capability_map"][0]["confirmed"] is True
        assert kept.get("style_card", {}).get("self_reference") == "我方"


# ------------------------------------------------------------------ O5 API：预估 + 创建 + 守卫


class TestReviewApi:
    @pytest.fixture(autouse=True)
    def _stub_env(self, monkeypatch):
        from backend.config import get_settings

        monkeypatch.setattr(get_settings(), "bid_wizard_access_mode", "enabled")

        import backend.services.sales as sales_service
        import backend.services.task_lifecycle as lifecycle

        async def _fake_async(*args, **kwargs):
            return None

        monkeypatch.setattr(lifecycle, "authorize_billable_task_start", _fake_async)
        monkeypatch.setattr(lifecycle, "dispatch_task_outbox", _fake_async)
        monkeypatch.setattr(sales_service, "multiplier_for_task", lambda config, kind: Decimal("1"))

    @staticmethod
    async def _seed_completed_task(wizard: dict, *, summary=None) -> str:
        from backend.models import (
            BidWizard,
            BidWizardSection,
            BidWritingTask,
            async_session_factory,
            engine,
        )

        async with async_session_factory() as session:
            owner_id = (
                await session.execute(select(BidWizard.user_id).where(BidWizard.id == wizard["id"]))
            ).scalar_one()
            task = BidWritingTask(
                wizard_id=wizard["id"],
                project_id=wizard["project_id"],
                user_id=owner_id,
                selected_nodes=["1", "2"],
                status="completed",
                summary=summary,
                billing_multiplier=Decimal("1"),
                billing_status="pending",
            )
            session.add(task)
            await session.flush()
            for node_id, words in (("1", 1_200), ("2", 800)):
                session.add(
                    BidWizardSection(
                        task_id=task.id,
                        node_id=node_id,
                        title=f"章节{node_id}",
                        status="generated",
                        word_count=words,
                    )
                )
            await session.commit()
            task_id = task.id
        await engine.dispose()
        return task_id

    @staticmethod
    async def _prepared_wizard(client, auth_headers) -> dict:
        wizard = (
            await client.post(
                "/api/bid-wizard/wizards", json={"project_name": "审读测试"}, headers=auth_headers
            )
        ).json()
        from backend.models import BidWizard, async_session_factory, engine
        from backend.utils.time_utils import utc_now

        async with async_session_factory() as session:
            row = (
                await session.execute(select(BidWizard).where(BidWizard.id == wizard["id"]))
            ).scalar_one()
            row.spec = [
                {"node_id": "1", "title": "章节1", "level": 1, "article_count": 2, "text_count": 400},
                {"node_id": "2", "title": "章节2", "level": 2, "article_count": 2, "text_count": 400},
            ]
            row.spec_confirmed_at = utc_now()
            await session.commit()
        await engine.dispose()
        return wizard

    @staticmethod
    async def _set_task_status(task_id: str, status: str) -> None:
        from backend.models import BidWritingTask, async_session_factory, engine

        async with async_session_factory() as session:
            row = (
                await session.execute(select(BidWritingTask).where(BidWritingTask.id == task_id))
            ).scalar_one()
            row.status = status
            await session.commit()
        await engine.dispose()

    async def test_review_estimate_and_create(self, client, auth_headers):
        wizard = await self._prepared_wizard(client, auth_headers)
        task_id = await self._seed_completed_task(wizard)

        estimate = (
            await client.get(
                f"/api/bid-wizard/writing-tasks/{task_id}/review-estimate", headers=auth_headers
            )
        ).json()
        assert estimate["chapters"] == 2
        assert estimate["total_words"] == 2_000
        assert estimate["estimated_tokens"] > 0

        created = await client.post(f"/api/bid-wizard/writing-tasks/{task_id}/review", headers=auth_headers)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["summary"]["action"] == "review"
        assert body["summary"]["review_of"] == task_id
        assert body["selected_nodes"] == ["1", "2"]  # 按大纲序
        assert body["continue_of"] == task_id

        # 审读任务不能再发起审读（一轮封顶）
        review_id = body["id"]
        again = await client.post(f"/api/bid-wizard/writing-tasks/{review_id}/review", headers=auth_headers)
        assert again.status_code == 400

        # 审读任务进行中：spec/需求锁定（复用既有撰写守卫）
        await self._set_task_status(review_id, "running")
        locked = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/stage",
            json={"stage": "outline"},
            headers=auth_headers,
        )
        assert locked.status_code == 409

    async def test_review_rejected_for_running_task(self, client, auth_headers):
        wizard = await self._prepared_wizard(client, auth_headers)
        task_id = await self._seed_completed_task(wizard)
        await self._set_task_status(task_id, "running")
        rejected = await client.post(f"/api/bid-wizard/writing-tasks/{task_id}/review", headers=auth_headers)
        assert rejected.status_code == 409


# ---------------------------------------------------------------- 措辞铁律（2026-10-24 去 AI 味）

def test_wording_rules_injected_into_all_generation_prompts():
    """措辞铁律（22 号 SOP §六）必须注入章节正文（两种图表模式）、审读修订 system prompt。"""
    from backend.agent.bid_wizard_agent import (
        REVIEW_REVISE_SYSTEM_PROMPT,
        _WIZARD_CHAPTER_INTRO_SYSTEM_PROMPT,
        _WIZARD_WORDING_RULES,
        section_system_prompt,
    )

    for prompt in (
        section_system_prompt(charts_enabled=True),
        section_system_prompt(charts_enabled=False),
        section_system_prompt(charts_enabled=True, fig_mode="bidfig"),
        REVIEW_REVISE_SYSTEM_PROMPT,
    ):
        assert "措辞铁律" in prompt
        assert "首先" in prompt and "综上所述" in prompt
    assert _WIZARD_WORDING_RULES in REVIEW_REVISE_SYSTEM_PROMPT
    assert "套话" in _WIZARD_CHAPTER_INTRO_SYSTEM_PROMPT


def test_review_findings_criteria_mention_ai_flavor():
    from backend.agent.bid_wizard_agent import REVIEW_FINDINGS_USER_TEMPLATE

    assert "AI 味" in REVIEW_FINDINGS_USER_TEMPLATE
