# -*- coding: utf-8 -*-
"""阶段5 联调：本地后端 E2E（真实 HTTP + 真实 LLM + 预发布库）。

覆盖：JWT 直签 → 润色任务全链路 → 活跃任务 409 → 标书生成全链路（上传→解析→
大纲注入→逐节→assembled）→ 计费结算（consumption_records + wallet + usage）。
注：本机 httpx 与 uvicorn(--reload) 不合（RemoteProtocolError），改用 requests。
用法：backend/ 目录下 PYTHONIOENCODING=utf-8 PYTHONPATH=<仓库根> python ../scripts/e2e_bid_generation.py
"""
import asyncio
import io
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000/api"
USERNAME = "e2e_bidgen_test"

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


def ensure_user() -> str:
    from backend.models import User, async_session_factory

    def _run():
        async def inner():
            async with async_session_factory() as db:
                result = await db.execute(
                    User.__table__.select().where(User.username == USERNAME)
                )
                row = result.mappings().first()
                if row is None:
                    new_id = str(uuid.uuid4())
                    await db.execute(
                        User.__table__.insert().values(
                            id=new_id,
                            username=USERNAME,
                            email=f"{USERNAME}@local",
                            password_hash="local-e2e",
                        )
                    )
                    await db.commit()
                    return new_id
                return str(row["id"])

        return asyncio.run(inner())

    return _run()


def make_tender_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    text = (
        "某智慧园区信息化建设项目招标文件（联调样例）\n\n"
        "一、项目概况：本项目为智慧园区信息化建设，预算 1200 万元，工期 180 日历天。\n"
        "二、资格要求：投标人须具备 ISO9001 质量管理体系认证，近三年具有类似业绩不少于 2 项。\n"
        "三、技术要求：提供园区综合管理平台、物联感知网络与运维保障方案。\n"
        "四、评分办法：技术方案 40 分，商务 20 分，价格 30 分，业绩 10 分。\n"
        "五、废标条款：未按要求签字盖章、报价超最高限价、资质证明缺失的投标将被否决。\n"
    )
    page.insert_text((72, 90), text, fontname="china-s", fontsize=12, lineheight=22)
    data = doc.tobytes()
    doc.close()
    return data


def wait_status(session, url, terminal, timeout_s, headers):
    started = time.monotonic()
    last = {}
    while time.monotonic() - started < timeout_s:
        r = session.get(url, headers=headers, timeout=60)
        last = r.json()
        if last.get("status") in terminal:
            return last
        time.sleep(2)
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


def verify_billing(polish_id: str, draft_id: str, user_id: str) -> None:
    async def inner():
        import asyncpg

        user, pwd, host, port, db = db_settings()
        conn = await asyncpg.connect(host=host, port=int(port), user=user, password=pwd, database=db)
        try:
            for task_id, kind in ((polish_id, "polish"), (draft_id, "bid_draft")):
                if not task_id:
                    continue
                # consumption_records 行存在即代表结算已落账（billing_* 列在任务表上）
                row = await conn.fetchrow(
                    "select sales_points, cost_cny, task_type from consumption_records where task_id=$1",
                    task_id,
                )
                record(
                    f"billing: {kind} consumption settled",
                    row is not None and row["sales_points"] is not None,
                    f"row={'none' if row is None else dict(row)}",
                )
            wallet = await conn.fetchrow(
                "select recharge_balance_points, gift_balance_points from user_wallets where user_id=$1",
                user_id,
            )
            record("billing: wallet exists for test user", wallet is not None, f"wallet={'none' if wallet is None else dict(wallet)}")
            usage = await conn.fetchval(
                "select count(*) from ai_usage_records where task_id = any($1::text[])",
                [t for t in (polish_id, draft_id) if t],
            )
            record("billing: llm usage recorded", int(usage or 0) > 0, f"rows={usage}")
        finally:
            await conn.close()

    asyncio.run(inner())


def main() -> None:
    user_id = ensure_user()
    token = make_token(user_id)
    headers = {"Authorization": f"Bearer {token}"}
    session = requests.Session()

    me = session.get(f"{BASE}/auth/me", headers=headers, timeout=30)
    record("auth: /auth/me with minted JWT", me.status_code == 200, f"code={me.status_code}")

    # ---------- 1. 润色全链路 + 活跃任务 409 ----------
    polish_body = {
        "mode": "polish",
        "text": "我司拥有丰富的智慧园区建设经验，团队专业，服务好，能保证项目顺利完成。",
        "requirements": "更正式，面向评标专家",
    }
    t0 = session.post(f"{BASE}/polish/tasks", json=polish_body, headers=headers, timeout=60)
    ok_created = t0.status_code == 201
    record("polish: create task", ok_created, f"code={t0.status_code} body={t0.text[:180]}")
    polish_id = t0.json().get("id", "") if ok_created else ""

    if ok_created:
        conflict = session.post(f"{BASE}/polish/tasks", json=polish_body, headers=headers, timeout=60)
        detail = conflict.json().get("detail") if conflict.status_code != 201 else {}
        record(
            "polish: 409 while another billable task active",
            conflict.status_code == 409
            and isinstance(detail, dict)
            and detail.get("code") == "ACTIVE_BILLING_TASK_EXISTS",
            f"code={conflict.status_code}",
        )
        final = wait_status(session, f"{BASE}/polish/tasks/{polish_id}", {"completed", "failed", "cancelled"}, 300, headers)
        record(
            "polish: task completes with result",
            final.get("status") == "completed" and bool(final.get("result_text")),
            f"status={final.get('status')} result_len={len(final.get('result_text') or '')} err={final.get('error_message')}",
        )

    # ---------- 2. 标书生成全链路 ----------
    proj = session.post(
        f"{BASE}/projects",
        json={"name": "E2E标书生成联调", "project_type": "review"},
        headers=headers,
        timeout=60,
    )
    record("bid-draft: create project", proj.status_code == 201, f"code={proj.status_code} body={proj.text[:120]}")
    project_id = proj.json().get("id", "")

    up = session.post(
        f"{BASE}/projects/{project_id}/documents",
        params={"doc_type": "tender"},
        files={"file": ("tender-e2e.pdf", io.BytesIO(make_tender_pdf()), "application/pdf")},
        headers=headers,
        timeout=120,
    )
    record("bid-draft: upload tender pdf", up.status_code == 201, f"code={up.status_code} body={up.text[:160]}")
    document_id = up.json().get("id", "")

    doc = wait_status(
        session,
        f"{BASE}/projects/{project_id}/documents/{document_id}",
        {"parsed", "failed"},
        300,
        headers,
    )
    record(
        "bid-draft: tender parsed",
        doc.get("status") == "parsed",
        f"status={doc.get('status')} words={doc.get('word_count')}",
    )

    draft_body = {
        "project_id": project_id,
        "tender_document_id": document_id,
        "analysis": {
            "basic": {"project_name": "智慧园区信息化项目（联调）"},
            "tender_requirements": ["园区综合管理平台", "物联感知网络", "运维保障方案"],
            "scoring_criteria": ["技术方案40分", "价格30分"],
            "rejection_items": ["未签字盖章否决"],
        },
        "outline": [
            {"title": "技术方案", "level": 1, "article_count": 2, "text_count": 150},
            {"title": "运维保障方案", "level": 1, "article_count": 1, "text_count": 120},
        ],
    }
    t1 = session.post(f"{BASE}/bid-draft/tasks", json=draft_body, headers=headers, timeout=60)
    record("bid-draft: create task", t1.status_code == 201, f"code={t1.status_code} body={t1.text[:180]}")
    draft_id = t1.json().get("id", "") if t1.status_code == 201 else ""

    if draft_id:
        final = wait_status(
            session, f"{BASE}/bid-draft/tasks/{draft_id}", {"completed", "failed", "cancelled"}, 600, headers
        )
        record(
            "bid-draft: task completes",
            final.get("status") == "completed",
            f"status={final.get('status')} phase={final.get('phase')} err={final.get('error_message')}",
        )
        sections = session.get(f"{BASE}/bid-draft/tasks/{draft_id}/sections", headers=headers, timeout=60)
        metas = sections.json()
        record(
            "bid-draft: sections generated",
            sections.status_code == 200
            and len(metas) == 2
            and all(m.get("status") == "generated" for m in metas),
            f"sections={[(m.get('node_id'), m.get('status'), m.get('word_count')) for m in metas]}",
        )
        assembled = session.get(f"{BASE}/bid-draft/tasks/{draft_id}/assembled", headers=headers, timeout=60)
        body = assembled.json()
        record(
            "bid-draft: assembled content",
            assembled.status_code == 200
            and bool(body.get("content"))
            and "技术方案" in (body.get("content") or "")
            and "运维保障方案" in (body.get("content") or ""),
            f"chars={len(body.get('content') or '')} generated={body.get('section_generated')}/{body.get('section_total')}",
        )

    # ---------- 3. 计费结算（留时间给 finalize→settle）----------
    print("waiting 15s for billing settlement ...", flush=True)
    time.sleep(15)
    verify_billing(polish_id, draft_id, user_id)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n==== E2E SUMMARY: {len(results) - len(failed)}/{len(results)} passed ====")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
