"""Unit tests for AI编标（bid-wizard）primitives.

风格照 test_bid_generation.py：纯函数 + 注册表断言（HTTP 层由 E2E 覆盖；
文末 TestWizardLifecycleApi 为 2026-09-09 联调缺陷的 API 层回归，用 conftest 的 client/auth_headers）。
"""

import pytest
from decimal import Decimal
from sqlalchemy import select
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
from backend.models import (
    TASK_MODEL_BY_KIND,
    BidWizardSection,
    BidWritingTask,
    BidWizardIndexTask,
    BidWizardQaTask,
)
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


def test_task_models_expose_reconcile_timestamps():
    """reconcile 结算兜底对 TASK_MODEL_BY_KIND 全部模型统一引用 started_at/completed_at。

    缺列会让兜底任务每轮 AttributeError 崩溃（2026-09-10 反馈⑭：BidWizardQaTask 缺列，
    预发布 reconcile 自 M2 部署起每 2 分钟崩一次，异常悬挂任务无人回收）。
    """
    from backend.models import TASK_MODEL_BY_KIND

    for kind, model in TASK_MODEL_BY_KIND.items():
        assert hasattr(model, "started_at"), f"{kind}/{model.__name__} 缺 started_at"
        assert hasattr(model, "completed_at"), f"{kind}/{model.__name__} 缺 completed_at"


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


# ------------------------------------------------------------------ API 层：向导生命周期回归
# 2026-09-09 真机联调反馈缺陷的回归：
# 1) 招标文件集变化（增/删/换）后旧解读（wizard.analysis）必须清空——问卷生成会
#    静默复用它，不清会导致文件集变化后问卷仍基于旧招标要素生成；
#    （2026-09-09 二轮联调改多文件口径：正文 + 补遗/澄清并存，上传为追加语义）
# 2) 需要「新建项目」闭环：归档当前向导后 /wizards/active 不再恢复老向导。


class TestWizardLifecycleApi:
    @pytest.fixture(autouse=True)
    def _stub_parse_dispatch(self, monkeypatch):
        from backend.tasks.document_parser import parse_document

        monkeypatch.setattr(parse_document, "delay", lambda *args, **kwargs: None)

    @pytest.fixture(autouse=True)
    def _enable_access(self, monkeypatch):
        from backend.config import get_settings

        monkeypatch.setattr(get_settings(), "bid_wizard_access_mode", "enabled")

    @staticmethod
    async def _create_wizard(client, auth_headers) -> dict:
        response = await client.post("/api/bid-wizard/wizards", json={}, headers=auth_headers)
        assert response.status_code == 201
        return response.json()

    async def _set_analysis(self, wizard_id: str, payload: dict) -> None:
        from backend.models import BidWizard, async_session_factory, engine

        async with async_session_factory() as session:
            wizard = (
                await session.execute(select(BidWizard).where(BidWizard.id == wizard_id))
            ).scalar_one()
            wizard.analysis = payload
            await session.commit()
        await engine.dispose()

    @staticmethod
    def _pdf_files(name: str) -> dict:
        return {"file": (name, b"%PDF-1.4\n% regression test tender\n", "application/pdf")}

    async def test_upload_second_tender_appends_and_clears_analysis(self, client, auth_headers):
        wizard = await self._create_wizard(client, auth_headers)
        base = f"/api/bid-wizard/wizards/{wizard['id']}"

        first = await client.post(f"{base}/tender", files=self._pdf_files("tender_a.pdf"), headers=auth_headers)
        assert first.status_code == 201
        await self._set_analysis(wizard["id"], {"basic": {"project_name": "旧标书"}})

        # 多文件口径（2026-09-09 联调改）：追加第二份（补遗/澄清）不再替换，两份并存
        second = await client.post(f"{base}/tender", files=self._pdf_files("tender_b.pdf"), headers=auth_headers)
        assert second.status_code == 201
        assert second.json()["document_id"] != first.json()["document_id"]

        detail = (await client.get(base, headers=auth_headers)).json()
        assert detail["analysis"] is None  # 文件集变化 → 旧解读清空，解读卡回到初始态

        docs = (
            await client.get(f"/api/projects/{wizard['project_id']}/documents", headers=auth_headers)
        ).json()["documents"]
        tenders = [item for item in docs if item["doc_type"] == "tender"]
        assert sorted(item["original_filename"] for item in tenders) == [
            "tender_a.pdf",
            "tender_b.pdf",
        ]

    async def test_delete_single_tender_document(self, client, auth_headers):
        wizard = await self._create_wizard(client, auth_headers)
        base = f"/api/bid-wizard/wizards/{wizard['id']}"

        first = await client.post(f"{base}/tender", files=self._pdf_files("tender_a.pdf"), headers=auth_headers)
        second = await client.post(f"{base}/tender", files=self._pdf_files("tender_b.pdf"), headers=auth_headers)
        await self._set_analysis(wizard["id"], {"basic": {"project_name": "旧标书"}})

        removed = await client.delete(
            f"{base}/tender/{first.json()['document_id']}", headers=auth_headers
        )
        assert removed.status_code == 200
        assert removed.json()["analysis"] is None

        docs = (
            await client.get(f"/api/projects/{wizard['project_id']}/documents", headers=auth_headers)
        ).json()["documents"]
        tenders = [item for item in docs if item["doc_type"] == "tender"]
        assert [item["original_filename"] for item in tenders] == ["tender_b.pdf"]

        # 已删除的文件再删 → 404
        missing = await client.delete(
            f"{base}/tender/{first.json()['document_id']}", headers=auth_headers
        )
        assert missing.status_code == 404

    async def test_delete_tender_clears_analysis(self, client, auth_headers):
        wizard = await self._create_wizard(client, auth_headers)
        base = f"/api/bid-wizard/wizards/{wizard['id']}"

        upload = await client.post(f"{base}/tender", files=self._pdf_files("tender_a.pdf"), headers=auth_headers)
        assert upload.status_code == 201
        await self._set_analysis(wizard["id"], {"basic": {"project_name": "旧标书"}})

        removed = await client.delete(f"{base}/tender", headers=auth_headers)
        assert removed.status_code == 200
        assert removed.json()["analysis"] is None

    async def test_archive_then_active_returns_new_wizard(self, client, auth_headers):
        wizard_a = await self._create_wizard(client, auth_headers)

        archived = await client.post(
            f"/api/bid-wizard/wizards/{wizard_a['id']}/archive", headers=auth_headers
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"

        # 归档后入口不再恢复老向导（无活动向导 → 404）
        active = await client.get("/api/bid-wizard/wizards/active", headers=auth_headers)
        assert active.status_code == 404

        # 新建后入口恢复到新向导
        wizard_b = await self._create_wizard(client, auth_headers)
        assert wizard_b["id"] != wizard_a["id"]
        active_new = await client.get("/api/bid-wizard/wizards/active", headers=auth_headers)
        assert active_new.status_code == 200
        assert active_new.json()["id"] == wizard_b["id"]

    async def test_archive_blocked_while_writing(self, client, auth_headers):
        from backend.models import BidWizard, BidWritingTask, async_session_factory, engine

        wizard = await self._create_wizard(client, auth_headers)
        async with async_session_factory() as session:
            row = (
                await session.execute(select(BidWizard).where(BidWizard.id == wizard["id"]))
            ).scalar_one()
            session.add(
                BidWritingTask(
                    wizard_id=row.id,
                    project_id=row.project_id,
                    user_id=row.user_id,
                    selected_nodes=["1"],
                    status="running",
                    billing_multiplier=Decimal("1"),
                    billing_status="pending",
                )
            )
            await session.commit()
        await engine.dispose()

        blocked = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/archive", headers=auth_headers
        )
        assert blocked.status_code == 409


# ------------------------------------------------------------------ API 层：项目管理（2026-09-09 第二轮 grilling 决策 25-29）


class TestProjectManagementApi:
    @pytest.fixture(autouse=True)
    def _stub_parse_dispatch(self, monkeypatch):
        from backend.tasks.document_parser import parse_document

        monkeypatch.setattr(parse_document, "delay", lambda *args, **kwargs: None)

    @pytest.fixture(autouse=True)
    def _enable_access(self, monkeypatch):
        from backend.config import get_settings

        monkeypatch.setattr(get_settings(), "bid_wizard_access_mode", "enabled")

    @pytest.fixture(autouse=True)
    def _stub_cleanup_dispatch(self, monkeypatch):
        from backend.tasks.bid_wizard_tasks import cleanup_wizard_workspace

        calls: list[tuple] = []
        monkeypatch.setattr(cleanup_wizard_workspace, "delay", lambda *a: calls.append(a))
        self.cleanup_calls = calls

    @staticmethod
    async def _create_wizard(client, auth_headers, name: str | None = None) -> dict:
        response = await client.post(
            "/api/bid-wizard/wizards", json={"project_name": name}, headers=auth_headers
        )
        assert response.status_code == 201
        return response.json()

    @staticmethod
    def _pdf_files(name: str) -> dict:
        return {"file": (name, b"%PDF-1.4\n% project list test\n", "application/pdf")}

    async def test_list_wizards_groups_and_orders(self, client, auth_headers):
        wizard_a = await self._create_wizard(client, auth_headers, name="园区项目")
        wizard_b = await self._create_wizard(client, auth_headers, name="医院项目")
        upload = await client.post(
            f"/api/bid-wizard/wizards/{wizard_b['id']}/tender",
            files=self._pdf_files("医院弱电招标.pdf"),
            headers=auth_headers,
        )
        assert upload.status_code == 201

        listed = (await client.get("/api/bid-wizard/wizards", headers=auth_headers)).json()
        assert [item["project_name"] for item in listed["active"]] == ["医院项目", "园区项目"]
        by_name = {item["project_name"]: item for item in listed["active"]}
        assert by_name["医院项目"]["tender_filename"] == "医院弱电招标.pdf"
        assert by_name["园区项目"]["tender_filename"] is None
        assert listed["archived"] == []

        archived = await client.post(
            f"/api/bid-wizard/wizards/{wizard_b['id']}/archive", headers=auth_headers
        )
        assert archived.status_code == 200
        listed2 = (await client.get("/api/bid-wizard/wizards", headers=auth_headers)).json()
        assert [item["project_name"] for item in listed2["active"]] == ["园区项目"]
        assert [item["project_name"] for item in listed2["archived"]] == ["医院项目"]

    async def test_restore_archived_wizard_keeps_stage(self, client, auth_headers):
        wizard = await self._create_wizard(client, auth_headers, name="待恢复")
        await client.post(f"/api/bid-wizard/wizards/{wizard['id']}/stage", json={"stage": "requirement"}, headers=auth_headers)
        await client.post(f"/api/bid-wizard/wizards/{wizard['id']}/archive", headers=auth_headers)

        restored = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/restore", headers=auth_headers
        )
        assert restored.status_code == 200
        assert restored.json()["status"] == "active"
        assert restored.json()["stage"] == "requirement"  # 回归档前断点

        double = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/restore", headers=auth_headers
        )
        assert double.status_code == 400  # 未归档无需恢复

    async def test_delete_requires_archive_first(self, client, auth_headers):
        wizard = await self._create_wizard(client, auth_headers)
        blocked = await client.delete(f"/api/bid-wizard/wizards/{wizard['id']}", headers=auth_headers)
        assert blocked.status_code == 400

    async def test_delete_soft_deletes_and_hides_everywhere(self, client, auth_headers):
        from backend.models import Project, async_session_factory, engine

        wizard = await self._create_wizard(client, auth_headers, name="要删除")
        wizard_id = wizard["id"]
        project_id = wizard["project_id"]
        await client.post(f"/api/bid-wizard/wizards/{wizard_id}/archive", headers=auth_headers)

        removed = await client.delete(f"/api/bid-wizard/wizards/{wizard_id}", headers=auth_headers)
        assert removed.status_code == 200

        listed = (await client.get("/api/bid-wizard/wizards", headers=auth_headers)).json()
        assert listed["active"] == [] and listed["archived"] == []
        assert (await client.get(f"/api/bid-wizard/wizards/{wizard_id}", headers=auth_headers)).status_code == 404
        assert (await client.get("/api/bid-wizard/wizards/active", headers=auth_headers)).status_code == 404

        # 软删审计保留：project 行还在且带删除标记；清理任务被派发
        async with async_session_factory() as session:
            project = (await session.execute(select(Project).where(Project.id == project_id))).scalar_one()
            assert project.is_deleted is True
            assert project.deleted_by_user_id is not None
        await engine.dispose()
        assert self.cleanup_calls and self.cleanup_calls[0][0] == wizard_id

    async def test_tender_upload_renames_default_project_name_only(self, client, auth_headers):
        default = await self._create_wizard(client, auth_headers)  # 默认名 AI编标 日期
        custom = await self._create_wizard(client, auth_headers, name="我起的名字")

        for wizard, expect in ((default, "智慧园区招标文件"), (custom, "我起的名字")):
            upload = await client.post(
                f"/api/bid-wizard/wizards/{wizard['id']}/tender",
                files=self._pdf_files("智慧园区招标文件.pdf"),
                headers=auth_headers,
            )
            assert upload.status_code == 201

        listed = (await client.get("/api/bid-wizard/wizards", headers=auth_headers)).json()
        names = {item["project_name"] for item in listed["active"]}
        assert names == {"智慧园区招标文件", "我起的名字"}


# ------------------------------------------------------------------ API 层：移除全部 AI 内容配套端点（决策 31）


class TestSectionsResetAndLatestApi:
    @pytest.fixture(autouse=True)
    def _enable_access(self, monkeypatch):
        from backend.config import get_settings

        monkeypatch.setattr(get_settings(), "bid_wizard_access_mode", "enabled")

    @staticmethod
    async def _seed_task(wizard: dict, status: str, sections: list[dict]):
        from backend.models import BidWritingTask, async_session_factory, engine

        async with async_session_factory() as session:
            task = BidWritingTask(
                wizard_id=wizard["id"],
                project_id=wizard["project_id"],
                user_id=wizard["user_id"] if "user_id" in wizard else None,
                selected_nodes=[s["node_id"] for s in sections],
                status=status,
                billing_multiplier=Decimal("1"),
                billing_status="pending",
            )
            if task.user_id is None:
                from backend.models import BidWizard

                row = (
                    await session.execute(select(BidWizard).where(BidWizard.id == wizard["id"]))
                ).scalar_one()
                task.user_id = row.user_id
            session.add(task)
            await session.flush()
            for item in sections:
                session.add(
                    BidWizardSection(
                        task_id=task.id,
                        node_id=item["node_id"],
                        title=item.get("title", item["node_id"]),
                        status=item["status"],
                        written_at=item.get("written_at"),
                    )
                )
            await session.commit()
        await engine.dispose()

    async def test_reset_written_and_latest(self, client, auth_headers):
        from backend.utils.time_utils import utc_now

        wizard = (
            await client.post(
                "/api/bid-wizard/wizards", json={"project_name": "移除测试"}, headers=auth_headers
            )
        ).json()
        await self._seed_task(
            wizard,
            status="completed",
            sections=[
                {"node_id": "1", "status": "written", "written_at": utc_now()},
                {"node_id": "2", "status": "generated"},
            ],
        )

        latest = (
            await client.get(
                f"/api/bid-wizard/wizards/{wizard['id']}/sections/latest", headers=auth_headers
            )
        ).json()
        assert [row["node_id"] for row in latest] == ["1", "2"]

        reset = (
            await client.post(
                f"/api/bid-wizard/wizards/{wizard['id']}/sections/reset-written",
                headers=auth_headers,
            )
        ).json()
        assert reset["reset_count"] == 1
        latest2 = (
            await client.get(
                f"/api/bid-wizard/wizards/{wizard['id']}/sections/latest", headers=auth_headers
            )
        ).json()
        assert all(row["status"] == "generated" for row in latest2)

    async def test_reset_blocked_while_writing(self, client, auth_headers):
        wizard = (
            await client.post("/api/bid-wizard/wizards", json={}, headers=auth_headers)
        ).json()
        await self._seed_task(
            wizard, status="running", sections=[{"node_id": "1", "status": "written", "written_at": None}]
        )
        blocked = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/sections/reset-written", headers=auth_headers
        )
        assert blocked.status_code == 409


# ------------------------------------------------------------------ 多轮追问（决策 32）


def test_build_requirements_text_includes_supplementals():
    text = build_requirements_text(
        {
            "questions": [
                {
                    "topic": "公司",
                    "question": "公司资质？",
                    "effective_answer": "ISO9001",
                    "action": "adopted",
                    "inferred": False,
                }
            ],
            "supplementals": [
                {"question": "业绩怎么突出", "answer": "引用两个千万级合同"},
                {"question": "", "answer": "无效条目应被忽略"},
            ],
        }
    )
    assert "ISO9001" in text
    assert "[补充说明] 业绩怎么突出：引用两个千万级合同" in text
    assert "无效条目" not in text


# ------------------------------------------------------------------ 开关 DB 化（决策 36）


class TestAccessModeDbPriority:
    @pytest.fixture(autouse=True)
    def _enable_env(self, monkeypatch):
        from backend.config import get_settings

        monkeypatch.setattr(get_settings(), "bid_wizard_access_mode", "enabled")

    @staticmethod
    async def _set_mode(mode: str | None) -> None:
        from backend.models import BidWizardSetting, async_session_factory, engine

        async with async_session_factory() as session:
            if mode is None:
                await session.execute(
                    __import__("sqlalchemy").delete(BidWizardSetting).where(True)
                )
            else:
                row = (
                    await session.execute(
                        select(BidWizardSetting).where(BidWizardSetting.id == "default")
                    )
                ).scalar_one_or_none()
                if row is None:
                    session.add(BidWizardSetting(id="default", mode=mode))
                else:
                    row.mode = mode
            await session.commit()
        await engine.dispose()

    @staticmethod
    async def _snapshot_rows() -> list[dict]:
        from backend.models import BidWizardSetting, async_session_factory, engine

        async with async_session_factory() as session:
            rows = (await session.execute(select(BidWizardSetting))).scalars().all()
            result = [{"id": r.id, "mode": r.mode} for r in rows]
        await engine.dispose()
        return result

    @staticmethod
    async def _restore_rows(before: list[dict]) -> None:
        """还原到测试前状态——测试库可能是共享的预发布库，无条件清表会把线上开关行删掉
        （2026-09-10 反馈⑭事故：全站 403 两次，均为测试套件收尾 _set_mode(None) 自伤）。"""
        from backend.models import BidWizardSetting, async_session_factory, engine

        keep_ids = {item["id"] for item in before}
        async with async_session_factory() as session:
            for row in (await session.execute(select(BidWizardSetting))).scalars().all():
                if row.id not in keep_ids:
                    await session.delete(row)
            for item in before:
                row = (
                    await session.execute(
                        select(BidWizardSetting).where(BidWizardSetting.id == item["id"])
                    )
                ).scalar_one_or_none()
                if row is None:
                    session.add(BidWizardSetting(id=item["id"], mode=item["mode"]))
                else:
                    row.mode = item["mode"]
            await session.commit()
        await engine.dispose()

    async def test_db_row_overrides_env(self, client, auth_headers):
        before = await self._snapshot_rows()
        try:
            await self._set_mode("disabled")
            access = (
                await client.get("/api/bid-wizard/access", headers=auth_headers)
            ).json()
            assert access["enabled"] is False and access["mode"] == "disabled"

            await self._set_mode("enabled")
            access2 = (
                await client.get("/api/bid-wizard/access", headers=auth_headers)
            ).json()
            assert access2["enabled"] is True and access2["mode"] == "enabled"
        finally:
            # 还原到测试前快照（此前若为空表则删测试行；此前若有值则写回）
            await self._restore_rows(before)


# ------------------------------------------------------------------ 补充素材 + 再次检查（反馈⑭）


def test_merge_questionnaire_answers_supplemented_action():
    questionnaire = normalize_questionnaire(
        {"questions": [
            {"question": "证书编号？", "suggested_answer": "素材未提供", "inferred": False},
        ]}
    )
    merged = merge_questionnaire_answers(
        {"questions": questionnaire["questions"]},
        [{"question_id": "q1", "action": "supplemented", "answer": None}],
    )
    question = merged["questions"][0]
    assert question["action"] == "supplemented"
    assert question["effective_answer"] is None  # 答案待下一轮检查给出


def test_build_requirements_text_marks_supplemented():
    requirements = {
        "questions": [
            {"topic": "资质", "question": "ISO9001 证书编号与有效期？",
             "effective_answer": None, "action": "supplemented"},
            {"topic": "交付", "question": "交付周期？",
             "effective_answer": "50 天", "action": "answered"},
        ]
    }
    text = build_requirements_text(requirements)
    assert "[已补充素材] ISO9001 证书编号与有效期？" in text
    assert "答案待下一轮检查确认" in text
    assert "50 天" in text


def test_render_existing_questions_text_labels_actions():
    from backend.agent.bid_wizard_agent import render_existing_questions_text

    text = render_existing_questions_text(
        {"questions": [
            {"topic": "资质", "question": "证书编号？", "action": "supplemented"},
            {"topic": "交付", "question": "交付周期？", "action": "adopted"},
            {"topic": "人员", "question": "项目经理？"},  # 未回答
        ]}
    )
    assert "（已补充素材）" in text and "证书编号" in text
    assert "（已采纳建议）" in text and "交付周期" in text
    assert "（未回答）" in text and "项目经理" in text
    assert render_existing_questions_text(None) == ""


def test_generate_questionnaire_round_context_and_normalization():
    import asyncio

    from backend.agent.bid_wizard_agent import generate_questionnaire_round

    captured = {}

    class FakeLLM:
        async def generate_json(self, system_prompt, user_prompt):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return {
                "questions": [
                    {"question": "证书编号是否为 ISO-2026-001？", "topic": "资质",
                     "why": "★必备资格", "suggested_answer": "ISO-2026-001（证书扫描件提取）",
                     "source": "证书扫描件#1", "inferred": False},
                    {"question": ""},  # 空问题被丢弃
                ]
            }

    questionnaire = asyncio.run(
        generate_questionnaire_round(
            FakeLLM(),
            analysis={"project_name": "智慧园区"},
            requirements_text="- [已补充素材] 证书编号？：用户已为该问题补充新素材，答案待下一轮检查确认",
            material_index_text="【素材 d1｜证书扫描件】| 1 | 证书 | ISO-2026-001 |",
            existing_questions_text="- [资质] 证书编号？（已补充素材）",
        )
    )
    assert [q["id"] for q in questionnaire["questions"]] == ["q1"]
    assert questionnaire["questions"][0]["source"] == "证书扫描件#1"
    user = captured["user"]
    assert "已补充素材" in user  # 需求文本中的补充标记进了 prompt
    assert "智慧园区" in user  # 招标要素
    assert "ISO-2026-001" in user  # 新素材索引事实进了上下文
    assert "已问过的问题" in user

    class EmptyLLM:
        async def generate_json(self, system_prompt, user_prompt):
            return {"questions": []}

    # 反馈⑱：空轮=AI 判定需求已充分，是合法返回（不再报错）
    empty = asyncio.run(
        generate_questionnaire_round(
            EmptyLLM(), analysis={}, requirements_text="",
            material_index_text="", existing_questions_text="",
        )
    )
    assert empty["questions"] == []


class TestQuestionnaireRoundApi:
    """再次检查端点（反馈⑭）：追加不覆盖、索引未完成 409。"""

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

        def _fake_multiplier(config, task_kind):
            from decimal import Decimal

            return Decimal("1")

        monkeypatch.setattr(sales_service, "multiplier_for_task", _fake_multiplier)

    async def _prepare_wizard(self, client, auth_headers) -> dict:
        """向导 + 已解析招标文件 + 第一轮问卷 + 已保存需求（q1 supplemented）。"""
        created = (
            await client.post("/api/bid-wizard/wizards", json={}, headers=auth_headers)
        ).json()
        await client.post(
            f"/api/bid-wizard/wizards/{created['id']}/tender",
            files={"file": ("tender.pdf", b"%PDF-1.4\n regression tender\n", "application/pdf")},
            headers=auth_headers,
        )
        from backend.models import BidWizard, Document, async_session_factory, engine
        from backend.agent.bid_wizard_agent import normalize_questionnaire

        questionnaire = normalize_questionnaire(
            {"questions": [
                {"question": "ISO9001 证书编号？", "suggested_answer": "", "topic": "资质"},
                {"question": "交付周期？", "suggested_answer": "45 天", "topic": "交付"},
            ]}
        )
        async with async_session_factory() as session:
            wizard = (
                await session.execute(select(BidWizard).where(BidWizard.id == created["id"]))
            ).scalar_one()
            wizard.analysis = {"project_name": "回归测试"}
            wizard.questionnaire = questionnaire
            tender_documents = (
                await session.execute(
                    select(Document).where(
                        Document.project_id == wizard.project_id,
                        Document.doc_type == "tender",
                    )
                )
            ).scalars().all()
            for document in tender_documents:
                document.status = "parsed"
            await session.commit()
        await engine.dispose()
        saved = await client.put(
            f"/api/bid-wizard/wizards/{created['id']}/requirements",
            json={"answers": [
                {"question_id": "q1", "action": "supplemented", "answer": None},
                {"question_id": "q2", "action": "adopted", "answer": None},
            ]},
            headers=auth_headers,
        )
        assert saved.status_code == 200
        assert saved.json()["requirements"]["questions"][0]["action"] == "supplemented"
        return created

    async def _add_material(self, wizard: dict, index_status: str) -> None:
        from backend.models import (
            BidWizard,
            BidWizardMaterial,
            Document,
            async_session_factory,
            engine,
        )

        async with async_session_factory() as session:
            owner_id = (
                await session.execute(select(BidWizard.user_id).where(BidWizard.id == wizard["id"]))
            ).scalar_one()
            document = Document(
                project_id=wizard["project_id"],
                doc_type="material",
                original_filename="证书扫描件.pdf",
                file_path="/tmp/cert.pdf",
                status="parsed",
            )
            session.add(document)
            await session.flush()
            session.add(
                BidWizardMaterial(
                    wizard_id=wizard["id"],
                    user_id=owner_id,
                    document_id=document.id,
                    index_status=index_status,
                )
            )
            await session.commit()
        await engine.dispose()

    async def test_round_rejected_while_material_indexing(self, client, auth_headers):
        wizard = await self._prepare_wizard(client, auth_headers)
        # 两份在途素材：存在性检查必须容忍多行（2026-09-11 反馈㉒：scalar_one_or_none 撞多行 500）
        await self._add_material(wizard, "indexing")
        await self._add_material(wizard, "pending")
        response = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/questionnaire/round",
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "索引" in response.json()["detail"]

    async def test_round_appends_and_preserves(self, client, auth_headers, monkeypatch):
        from backend.agent import bid_wizard_agent

        class FakeLLM:
            def __init__(self, timeout=None):
                pass

            async def generate_json(self, system_prompt, user_prompt):
                assert "[已补充素材]" in user_prompt  # 补充标记进入新一轮 prompt
                return {
                    "questions": [
                        {"question": "证书编号是否为 ISO-2026-001？", "topic": "资质",
                         "why": "★必备", "suggested_answer": "ISO-2026-001",
                         "source": "证书扫描件#1", "inferred": False},
                    ]
                }

        monkeypatch.setattr(bid_wizard_agent, "WizardLLM", FakeLLM)
        wizard = await self._prepare_wizard(client, auth_headers)
        await self._add_material(wizard, "indexed")
        response = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/questionnaire/round",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        ids = [item["id"] for item in body["questionnaire"]["questions"]]
        assert ids == ["q1", "q2", "q3"]  # 追加续号、旧问题原样保留
        assert body["requirements"]["questions"][0]["action"] == "supplemented"
        assert body["requirements"]["questions"][1]["effective_answer"] == "45 天"
        assert body["requirements_stale"] is False

    async def _set_questionnaire(self, wizard_id: str, count: int) -> None:
        """直接把向导问卷覆盖为 count 道题（q1..qN，不带作答状态）。

        不走 normalize_questionnaire——它按单轮上限截断到 15 条，造不出 20 题。
        """
        from backend.models import BidWizard, async_session_factory, engine

        questionnaire = {
            "questions": [
                {
                    "id": f"q{index}",
                    "question": f"回归问题 {index}？",
                    "suggested_answer": f"建议 {index}",
                    "topic": "资质",
                    "why": "",
                    "source": "",
                    "inferred": False,
                }
                for index in range(1, count + 1)
            ]
        }
        async with async_session_factory() as session:
            row = (
                await session.execute(select(BidWizard).where(BidWizard.id == wizard_id))
            ).scalar_one()
            row.questionnaire = questionnaire
            row.requirements = None
            await session.commit()
        await engine.dispose()

    async def test_round_open_cap_blocks_only_unanswered(self, client, auth_headers):
        """20 道全未作答 → 拒绝（待答上限）；这是闸门本意：逼用户先处理存量问题。"""
        wizard = await self._prepare_wizard(client, auth_headers)
        await self._set_questionnaire(wizard["id"], 20)
        response = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/questionnaire/round",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "待答" in response.json()["detail"]

    async def test_round_allowed_when_all_answered(self, client, auth_headers, monkeypatch):
        """反馈⑯场景：20 道全部已作答（采纳/补充）→ 再次检查必须放行（总数不再是闸门）。"""
        from backend.agent import bid_wizard_agent

        class FakeLLM:
            def __init__(self, timeout=None):
                pass

            async def generate_json(self, system_prompt, user_prompt):
                assert "已采纳建议" in user_prompt  # 作答状态标签来自 requirements 视图
                return {
                    "questions": [
                        {"question": "证书编号是否为 ISO-2026-009？", "topic": "资质",
                         "why": "★必备", "suggested_answer": "ISO-2026-009",
                         "source": "证书扫描件#2", "inferred": False},
                    ]
                }

        monkeypatch.setattr(bid_wizard_agent, "WizardLLM", FakeLLM)
        wizard = await self._prepare_wizard(client, auth_headers)
        await self._set_questionnaire(wizard["id"], 20)
        saved = await client.put(
            f"/api/bid-wizard/wizards/{wizard['id']}/requirements",
            json={"answers": [
                {"question_id": f"q{index}", "action": "adopted", "answer": None}
                for index in range(1, 20)
            ] + [{"question_id": "q20", "action": "supplemented", "answer": None}]},
            headers=auth_headers,
        )
        assert saved.status_code == 200  # 20 条作答载荷在新总上限内
        await self._add_material(wizard, "indexed")
        response = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/questionnaire/round",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        ids = [item["id"] for item in body["questionnaire"]["questions"]]
        assert len(ids) == 21 and ids[-1] == "q21"  # 20 全终态 + 追加 1 题续号

    async def test_round_auto_marks_done_on_empty(self, client, auth_headers, monkeypatch):
        """AI 判定需求已充分（空轮）：trigger=auto 返回 200 并标记 done，不追加问题。"""
        from backend.agent import bid_wizard_agent

        class FakeLLM:
            def __init__(self, timeout=None):
                pass

            async def generate_json(self, system_prompt, user_prompt):
                assert "无需追问" in user_prompt  # prompt 明确允许空列表
                return {"questions": []}

        monkeypatch.setattr(bid_wizard_agent, "WizardLLM", FakeLLM)
        wizard = await self._prepare_wizard(client, auth_headers)
        response = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/questionnaire/round?trigger=auto",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["questionnaire"]["questions"]) == 2  # 原样保留
        assert body["questionnaire"]["followup"] == {"auto_rounds": 0, "status": "done"}

    async def test_round_auto_rejected_when_done_or_capped(self, client, auth_headers):
        """done 状态或自动轮数达上限时，trigger=auto 直接拒绝（不再白跑计费评估）。"""
        from backend.models import BidWizard, async_session_factory, engine

        wizard = await self._prepare_wizard(client, auth_headers)
        for followup in ({"auto_rounds": 0, "status": "done"}, {"auto_rounds": 5, "status": "active"}):
            async with async_session_factory() as session:
                row = (
                    await session.execute(select(BidWizard).where(BidWizard.id == wizard["id"]))
                ).scalar_one()
                row.questionnaire = {**row.questionnaire, "followup": followup}
                await session.commit()
            await engine.dispose()
            response = await client.post(
                f"/api/bid-wizard/wizards/{wizard['id']}/questionnaire/round?trigger=auto",
                headers=auth_headers,
            )
            assert response.status_code == 400

    async def test_round_auto_appends_and_counts_round(self, client, auth_headers, monkeypatch):
        """trigger=auto 追加问题：轮次标记 round=2、auto_rounds 计数 +1；manual 不占配额。"""
        from backend.agent import bid_wizard_agent

        class FakeLLM:
            def __init__(self, timeout=None):
                pass

            async def generate_json(self, system_prompt, user_prompt):
                return {
                    "questions": [
                        {"question": "自动追问：项目经理是谁？", "topic": "人员",
                         "why": "★必备", "suggested_answer": "", "inferred": False},
                    ]
                }

        monkeypatch.setattr(bid_wizard_agent, "WizardLLM", FakeLLM)
        wizard = await self._prepare_wizard(client, auth_headers)
        response = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/questionnaire/round?trigger=auto",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        questions = body["questionnaire"]["questions"]
        assert questions[-1]["id"] == "q3" and questions[-1]["round"] == 2
        assert questions[0].get("round") in (None, 1)  # 首轮无 round 字段
        assert body["questionnaire"]["followup"] == {"auto_rounds": 1, "status": "active"}
        manual = await client.post(
            f"/api/bid-wizard/wizards/{wizard['id']}/questionnaire/round",
            headers=auth_headers,
        )
        assert manual.status_code == 200
        assert manual.json()["questionnaire"]["followup"]["auto_rounds"] == 1  # manual 不占配额


class TestUsageLedgerFixes:
    """2026-09-11 全流程复盘修复回归：用量汇总维度补齐 + 短素材索引短路。"""

    async def test_task_summary_merges_wizard_dimensions(self, client, auth_headers):
        created = (
            await client.post("/api/bid-wizard/wizards", json={}, headers=auth_headers)
        ).json()
        from sqlalchemy import text as sa_text
        import uuid as _uuid

        from backend.models import BidWizard, BidWizardQaTask, async_session_factory, engine
        from backend.models.ai_usage_record import AiUsageRecord
        from backend.services.usage_summary import refresh_task_summary

        qa_id = str(_uuid.uuid4())
        from datetime import datetime, timezone

        async with async_session_factory() as session:
            wizard = (
                await session.execute(select(BidWizard).where(BidWizard.id == created["id"]))
            ).scalar_one()
            session.add(
                BidWizardQaTask(
                    id=qa_id,
                    wizard_id=wizard.id,
                    user_id=wizard.user_id,
                    action="questionnaire",
                    status="completed",
                    billing_multiplier=Decimal("1"),
                    billing_status="settled",
                    started_at=wizard.created_at,
                    completed_at=wizard.updated_at,
                )
            )
            session.add(
                AiUsageRecord(
                    usage_type="llm",
                    provider="tencent",
                    model="test-model",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    latency_ms=1,
                    status="success",
                    cost_cny=Decimal("0.001"),
                    local_user_id=wizard.user_id,
                    user_name="regression",
                    project_id=wizard.project_id,
                    task_id=qa_id,
                    usage_date=datetime.now(timezone.utc).date(),
                )
            )
            await session.commit()
        try:
            await refresh_task_summary(qa_id, strict=True)
            async with async_session_factory() as session:
                row = (
                    await session.execute(
                        sa_text(
                            "SELECT task_type, task_status, project_id, duration_seconds "
                            "FROM ai_usage_task_summary WHERE id = :tid"
                        ),
                        {"tid": qa_id},
                    )
                ).fetchone()
            assert row is not None
            assert row.task_type == "bid_wizard_qa"
            assert row.task_status == "completed"
            assert str(row.project_id) == created["project_id"]
            assert row.duration_seconds is not None
        finally:
            await engine.dispose()

    def test_index_meta_short_circuit(self):
        from backend.tasks.bid_wizard_tasks import index_meta_llm_needed

        assert index_meta_llm_needed("ISO9001 质量管理体系认证证书，有效期至 2027-08。") is False
        assert index_meta_llm_needed("字" * 400) is True
        assert index_meta_llm_needed("   \n\t ") is False

    def test_finalize_survives_poisoned_pool_across_loops(self):
        """2026-09-11 409 事故回归：celery prefork 每任务 asyncio.run 换 loop，
        finalize 第 3 步曾用池化工厂——池里残留上一 loop 的连接时 checkout 的
        pre_ping 在已关 loop 上必抛 RuntimeError: Event loop is closed，结算卡
        pending 占住并发闸门。先用 loop A 毒化池，再在全新 loop B/C 里 finalize，
        必须全部成功（NullPool 工厂不依赖池状态）。"""
        import asyncio
        import uuid as _uuid

        from sqlalchemy import select, text as sa_text2

        from backend.models import BidWizard, BidWizardQaTask
        from backend.models.base import engine, usage_session_factory, async_session_factory
        from backend.services.task_lifecycle import finalize_task_usage

        qa_id = str(_uuid.uuid4())

        async def _setup() -> None:
            async with usage_session_factory() as session:
                wizard = (
                    await session.execute(
                        select(BidWizard).order_by(BidWizard.created_at.desc()).limit(1)
                    )
                ).scalar_one()
                session.add(
                    BidWizardQaTask(
                        id=qa_id,
                        wizard_id=wizard.id,
                        user_id=wizard.user_id,
                        action="questionnaire",
                        status="completed",
                        billing_multiplier=Decimal("1"),
                        billing_status="pending",
                        started_at=wizard.created_at,
                        completed_at=wizard.updated_at,
                    )
                )
                await session.commit()

        async def _poison() -> None:
            # 池化工厂在当前 loop 留一条连接；loop 关闭后它成为"跨 loop 残留"。
            # 二次毒化时 checkout 自身可能先吃到上一次的残留而报错——那一次失败
            # 恰好把毒化连接逐出池，重试一次即完成注入（与 worker 实况一致）。
            try:
                async with async_session_factory() as session:
                    await session.execute(sa_text2("select 1"))
            except RuntimeError:
                async with async_session_factory() as session:
                    await session.execute(sa_text2("select 1"))

        try:
            asyncio.run(_setup())
            asyncio.run(_poison())
            assert asyncio.run(finalize_task_usage("bid_wizard_qa", qa_id)) is True
            asyncio.run(_poison())
            assert asyncio.run(finalize_task_usage("bid_wizard_qa", qa_id)) is True
        finally:
            async def _cleanup() -> None:
                async with usage_session_factory() as session:
                    await session.execute(
                        sa_text2("DELETE FROM bid_wizard_qa_tasks WHERE id = :tid"),
                        {"tid": qa_id},
                    )
                    await session.commit()

            asyncio.run(_cleanup())
            asyncio.run(engine.dispose())
