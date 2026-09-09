# -*- coding: utf-8 -*-
"""AI编标（bid-wizard）M1 后端 E2E（doc/workspace/20-bid-wizard.md §8 第一项）。

覆盖：建向导 → 传招标文件（解析）→ AI 解读（suggested_materials）→ 传素材
（上传即自动索引）→ 问卷 → 保存需求 → Spec 生成 + AI 修订 + 一步回退 →
确认 Spec → 撰写任务 → 逐章内容 + written 回报 → 计费结算断言（三 kind 用量
进 ai_usage_records、任务行 settled、consumption_records 落账）。

前置：本地 backend:8000 + celery worker（generation/parser/billing）+ beat 在跑、
LLM key 有效、BID_WIZARD_ACCESS_MODE=enabled、素材索引倍率价目可用。
注：本机 httpx 与 uvicorn(--reload) 不合（RemoteProtocolError），沿用 requests。
用法：backend/ 目录下 PYTHONIOENCODING=utf-8 PYTHONPATH=<仓库根> python ../scripts/e2e_bid_wizard.py
"""
import asyncio
import io
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000/api"
USERNAME = "e2e_bidwiz_test"
WALLET_TOPUP_POINTS = Decimal("50000")

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def make_token(user_id: str) -> str:
    import jwt

    from backend.config import get_settings

    settings = get_settings()
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def ensure_user_with_points() -> str:
    """建/取测试用户并补足点数（402 余额闸门会拦截空钱包）。"""
    from backend.models import User, UserWallet, async_session_factory

    async def inner() -> str:
        async with async_session_factory() as db:
            row = (
                await db.execute(User.__table__.select().where(User.username == USERNAME))
            ).mappings().first()
            if row is None:
                user_id = str(uuid.uuid4())
                await db.execute(
                    User.__table__.insert().values(
                        id=user_id,
                        username=USERNAME,
                        email=f"{USERNAME}@local",
                        password_hash="local-e2e",
                    )
                )
            else:
                user_id = str(row["id"])
            wallet = (
                await db.execute(
                    UserWallet.__table__.select().where(UserWallet.user_id == user_id)
                )
            ).mappings().first()
            if wallet is None:
                await db.execute(
                    UserWallet.__table__.insert().values(
                        user_id=user_id,
                        recharge_balance_points=WALLET_TOPUP_POINTS,
                        gift_balance_points=Decimal("0"),
                    )
                )
            elif Decimal(str(wallet["recharge_balance_points"])) < Decimal("1000"):
                await db.execute(
                    UserWallet.__table__.update()
                    .where(UserWallet.user_id == user_id)
                    .values(recharge_balance_points=WALLET_TOPUP_POINTS)
                )
            await db.commit()
            return user_id

    return asyncio.run(inner())


def make_pdf(title: str, body: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), f"{title}\n\n{body}", fontname="china-s", fontsize=12, lineheight=22)
    data = doc.tobytes()
    doc.close()
    return data


def make_tender_pdf() -> bytes:
    return make_pdf(
        "某智慧园区信息化建设项目招标文件（AI编标联调样例）",
        "一、项目概况：智慧园区信息化建设，预算 1200 万元，工期 180 日历天。\n"
        "二、资格要求：投标人须具备 ISO9001 认证，近三年类似业绩不少于 2 项。\n"
        "三、技术要求：提供园区综合管理平台、物联感知网络与运维保障方案。\n"
        "四、评分办法：技术方案 40 分，商务 20 分，价格 30 分，业绩 10 分。\n"
        "五、废标条款：未按要求签字盖章、资质证明缺失的投标将被否决。\n",
    )


def make_material_pdf() -> bytes:
    return make_pdf(
        "公司简介与业绩（AI编标联调素材）",
        "我司成立于 2010 年，具备 ISO9001 质量管理体系认证。\n"
        "类似业绩：2019 年某市智慧园区管理平台项目（合同额 800 万元）；\n"
        "2021 年某区物联感知网络建设项目（合同额 600 万元）。\n"
        "项目经理均持有一级建造师（机电）证书，运维团队 20 人。\n",
    )


def wait_for(check, timeout_s: int, interval_s: float = 2.0):
    started = time.monotonic()
    last = None
    while time.monotonic() - started < timeout_s:
        last = check()
        if last is not None:
            return last
        time.sleep(interval_s)
    return last


def db_settings():
    env = None
    for candidate in ("backend/.env", "../backend/.env"):
        p = Path(candidate)
        if p.exists():
            env = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(
                r'^DATABASE_URL\s*=\s*"postgresql\+asyncpg://([^:]+):([^@]+)@([^:/]+):(\d+)/(\w+)"',
                env,
                re.M,
            )
            if m:
                return m.groups()
    raise RuntimeError("DATABASE_URL not found")


def verify_billing(wizard_id: str) -> None:
    """三 kind 结算断言：任务行 settled、用量进 ai_usage_records、consumption 落账。"""
    async def inner():
        import asyncpg

        user, pwd, host, port, db = db_settings()
        conn = await asyncpg.connect(host=host, port=int(port), user=user, password=pwd, database=db)
        try:
            for table, label in (
                ("bid_wizard_qa_tasks", "qa"),
                ("bid_wizard_index_tasks", "index"),
                ("bid_writing_tasks", "write"),
            ):
                rows = await conn.fetch(
                    f"select id, status, billing_settled_at, billing_status from {table} where wizard_id=$1",
                    wizard_id,
                )
                record(
                    f"billing: {label} task rows exist",
                    len(rows) > 0,
                    f"count={len(rows)} statuses={[r['status'] for r in rows]}",
                )
                unsettled = [dict(r) for r in rows if r["billing_settled_at"] is None]
                record(
                    f"billing: {label} rows all settled",
                    len(rows) > 0 and not unsettled,
                    f"unsettled={unsettled[:3]}",
                )
                task_ids = [r["id"] for r in rows]
                usage = await conn.fetchval(
                    "select count(*) from ai_usage_records where task_id = any($1::text[])",
                    task_ids,
                )
                record(
                    f"billing: {label} llm usage recorded",
                    int(usage or 0) > 0,
                    f"rows={usage}",
                )
                if label in ("index", "write"):
                    consumed = await conn.fetch(
                        "select sales_points, cost_cny from consumption_records where task_id = any($1::text[])",
                        task_ids,
                    )
                    record(
                        f"billing: {label} consumption settled",
                        len(consumed) > 0 and all(r["sales_points"] is not None for r in consumed),
                        f"rows={[dict(r) for r in consumed][:3]}",
                    )
        finally:
            await conn.close()

    asyncio.run(inner())


def main() -> None:
    user_id = ensure_user_with_points()
    token = make_token(user_id)
    headers = {"Authorization": f"Bearer {token}"}
    session = requests.Session()

    me = session.get(f"{BASE}/auth/me", headers=headers, timeout=30)
    record("auth: /auth/me with minted JWT", me.status_code == 200, f"code={me.status_code}")

    access = session.get(f"{BASE}/bid-wizard/access", headers=headers, timeout=30).json()
    if not access.get("enabled"):
        record("wizard: access enabled", False, f"access={access}（需 BID_WIZARD_ACCESS_MODE=enabled）")
        _summary()
        return
    record("wizard: access enabled", True, f"mode={access.get('mode')}")

    # ---------- 1. 建向导 + 传招标文件 ----------
    wizard = session.post(f"{BASE}/bid-wizard/wizards", json={}, headers=headers, timeout=60).json()
    wizard_id = wizard.get("id", "")
    project_id = wizard.get("project_id", "")
    record("wizard: create", bool(wizard_id), f"id={wizard_id} stage={wizard.get('stage')}")

    up = session.post(
        f"{BASE}/bid-wizard/wizards/{wizard_id}/tender",
        files={"file": ("tender-e2e.pdf", io.BytesIO(make_tender_pdf()), "application/pdf")},
        headers=headers,
        timeout=120,
    )
    tender_doc_id = up.json().get("document_id", "") if up.status_code == 201 else ""
    record("wizard: upload tender", up.status_code == 201, f"code={up.status_code} body={up.text[:160]}")

    doc = wait_for(
        lambda: (
            lambda d: d if d.get("status") in ("parsed", "failed") else None
        )(session.get(f"{BASE}/projects/{project_id}/documents/{tender_doc_id}", headers=headers, timeout=60).json()),
        timeout_s=300,
    )
    record("wizard: tender parsed", (doc or {}).get("status") == "parsed", f"status={(doc or {}).get('status')}")

    # ---------- 2. AI 解读（suggested_materials） ----------
    analyzed = session.post(f"{BASE}/bid-wizard/wizards/{wizard_id}/analysis", headers=headers, timeout=180)
    analysis = analyzed.json().get("analysis") if analyzed.status_code == 200 else None
    record(
        "wizard: AI tender analysis",
        analyzed.status_code == 200
        and isinstance(analysis, dict)
        and bool(analysis.get("tender_requirements")),
        f"code={analyzed.status_code} reqs={len((analysis or {}).get('tender_requirements') or [])}",
    )
    record(
        "wizard: suggested_materials is list",
        isinstance((analysis or {}).get("suggested_materials"), list),
        f"suggestions={(analysis or {}).get('suggested_materials')}",
    )

    # ---------- 3. 传素材：上传即解析+自动索引 ----------
    mat = session.post(
        f"{BASE}/bid-wizard/wizards/{wizard_id}/materials",
        files={"file": ("company-profile-e2e.pdf", io.BytesIO(make_material_pdf()), "application/pdf")},
        headers=headers,
        timeout=120,
    )
    record("wizard: upload material", mat.status_code == 201, f"code={mat.status_code} body={mat.text[:160]}")

    def check_indexed():
        rows = session.get(f"{BASE}/bid-wizard/wizards/{wizard_id}/materials", headers=headers, timeout=60).json()
        if rows and all(r.get("index_status") in ("indexed", "failed") for r in rows):
            return rows
        return None

    materials = wait_for(check_indexed, timeout_s=600)
    ok_indexed = bool(materials) and any(r.get("index_status") == "indexed" for r in (materials or []))
    record(
        "wizard: material auto-indexed (决策 17a)",
        ok_indexed,
        f"rows={[(r.get('index_status'), r.get('chunk_count'), r.get('index_error')) for r in (materials or [])]}",
    )

    # ---------- 4. 问卷 → 需求 ----------
    quest = session.post(f"{BASE}/bid-wizard/wizards/{wizard_id}/questionnaire", headers=headers, timeout=180)
    questions = (quest.json().get("questionnaire") or {}).get("questions") if quest.status_code == 200 else []
    record("wizard: questionnaire generated", quest.status_code == 200 and len(questions) >= 1,
           f"code={quest.status_code} questions={len(questions or [])}")

    req = session.put(
        f"{BASE}/bid-wizard/wizards/{wizard_id}/requirements",
        json={"answers": [{"question_id": q["id"], "action": "adopted", "answer": None} for q in questions]},
        headers=headers,
        timeout=60,
    )
    record("wizard: requirements saved", req.status_code == 200, f"code={req.status_code}")

    # ---------- 5. Spec 生成 + AI 修订 + 回退 + 确认 ----------
    spec_resp = session.post(f"{BASE}/bid-wizard/wizards/{wizard_id}/spec", headers=headers, timeout=180)
    spec_nodes = spec_resp.json().get("spec") if spec_resp.status_code == 200 else []
    record("wizard: spec generated", spec_resp.status_code == 200 and len(spec_nodes) >= 2,
           f"code={spec_resp.status_code} nodes={len(spec_nodes or [])}")

    revise = session.post(
        f"{BASE}/bid-wizard/wizards/{wizard_id}/spec/revise",
        json={"instruction": "保持结构不变，各章摘要更精炼"},
        headers=headers,
        timeout=180,
    )
    record("wizard: spec AI revised", revise.status_code == 200, f"code={revise.status_code} body={revise.text[:160]}")

    rollback = session.post(f"{BASE}/bid-wizard/wizards/{wizard_id}/spec/rollback", headers=headers, timeout=60)
    record("wizard: spec rollback one step", rollback.status_code == 200, f"code={rollback.status_code}")

    spec_nodes = rollback.json().get("spec") or spec_nodes
    confirmed = session.post(f"{BASE}/bid-wizard/wizards/{wizard_id}/spec/confirm", headers=headers, timeout=60)
    record(
        "wizard: spec confirmed",
        confirmed.status_code == 200 and bool(confirmed.json().get("spec_confirmed_at")),
        f"code={confirmed.status_code}",
    )

    # ---------- 6. 撰写任务 → 逐章 written 回报 ----------
    node_ids = [node["node_id"] for node in spec_nodes]
    task_resp = session.post(
        f"{BASE}/bid-wizard/wizards/{wizard_id}/writing-tasks",
        json={"node_ids": node_ids},
        headers=headers,
        timeout=60,
    )
    write_task_id = task_resp.json().get("id", "") if task_resp.status_code == 201 else ""
    record("wizard: writing task created", task_resp.status_code == 201,
           f"code={task_resp.status_code} body={task_resp.text[:160]}")

    final = wait_for(
        lambda: (
            lambda t: t if t.get("status") in ("completed", "failed", "cancelled") else None
        )(session.get(f"{BASE}/bid-wizard/writing-tasks/{write_task_id}", headers=headers, timeout=60).json()),
        timeout_s=900,
    )
    record("wizard: writing task completed", (final or {}).get("status") == "completed",
           f"status={(final or {}).get('status')} summary={(final or {}).get('summary')} err={(final or {}).get('error_message')}")

    sections = session.get(f"{BASE}/bid-wizard/writing-tasks/{write_task_id}/sections", headers=headers, timeout=60).json()
    generated = [s for s in sections if s.get("status") in ("generated", "written")]
    record("wizard: sections generated", len(generated) >= 1,
           f"rows={[(s.get('node_id'), s.get('status'), s.get('word_count')) for s in sections]}")

    written_ok = 0
    for section in generated:
        content = session.get(
            f"{BASE}/bid-wizard/writing-tasks/{write_task_id}/sections/{section['node_id']}",
            headers=headers, timeout=60,
        ).json()
        if not content.get("content"):
            continue
        mark = session.post(
            f"{BASE}/bid-wizard/writing-tasks/{write_task_id}/sections/{section['node_id']}/written",
            headers=headers, timeout=60,
        )
        if mark.status_code == 200 and mark.json().get("status") == "written":
            written_ok += 1
    record("wizard: sections content read + written reported", written_ok >= 1, f"written={written_ok}/{len(generated)}")

    # ---------- 7. 计费结算断言（留时间给 finalize→settle） ----------
    print("waiting 20s for billing settlement ...", flush=True)
    time.sleep(20)
    verify_billing(wizard_id)
    _summary()


def _summary() -> None:
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n==== E2E SUMMARY: {len(results) - len(failed)}/{len(results)} passed ====")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
