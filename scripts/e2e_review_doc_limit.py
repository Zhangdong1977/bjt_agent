# -*- coding: utf-8 -*-
"""标书检查每类文档上限（review_doc_role_limit=20）预发布 E2E：三闸门真实验证。

覆盖：草稿上传第 21 份 400 → 20 份全部关联项目 200 → 第 21 份 attach 400 →
项目内直传第 21 份 400 → 双向确认（bid 类不受 tender 满员影响）→ 清理。
用法：backend/ 目录下 PYTHONIOENCODING=utf-8 PYTHONPATH=<仓库根> python scripts/e2e_review_doc_limit.py
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

BASE = "http://127.0.0.1:8000/api"
USERNAME = "e2e_review_limit_test"

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


# 与后端单测一致的极小 PDF（无需 fitz）
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj
2 0 obj<</Type /Pages /Kids [3 0 R] /Count 1>>endobj
3 0 obj<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R>>endobj
5 0 obj<</Length 44>>stream
BT /F1 12 Tf 100 700 Td (Limit E2E) Tj ET
endstream endobj
trailer<</Size 6 /Root 1 0 R>>
%%EOF"""


def upload_draft(session, headers, doc_type: str, index: int):
    return session.post(
        f"{BASE}/documents/upload",
        params={"doc_type": doc_type},
        files={"file": (f"limit_e2e_{doc_type}_{index}.pdf", MINIMAL_PDF, "application/pdf")},
        headers=headers,
        timeout=60,
    )


def main() -> int:
    from backend.config import get_settings

    limit = get_settings().review_doc_role_limit
    record("settings.review_doc_role_limit", limit == 20, f"value={limit}")

    user_id = ensure_user()
    headers = {"Authorization": f"Bearer {make_token(user_id)}"}
    session = requests.Session()

    me = session.get(f"{BASE}/auth/me", headers=headers, timeout=30)
    record("auth", me.status_code == 200, f"status={me.status_code}")

    # 清场：删掉上次运行可能残留的草稿（失败不影响结论，仅减噪）
    try:
        for doc in session.get(f"{BASE}/documents/drafts", headers=headers, timeout=30).json().get("documents", []):
            if doc["original_filename"].startswith("limit_e2e_"):
                session.delete(f"{BASE}/documents/{doc['id']}", headers=headers, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"pre-clean skipped: {exc}", flush=True)

    # 闸门①：草稿上传，前 limit 份 201，第 limit+1 份 400
    draft_ids = []
    for i in range(limit):
        r = upload_draft(session, headers, "tender", i)
        if r.status_code != 201:
            record(f"draft upload #{i + 1}", False, f"status={r.status_code} body={r.text[:200]}")
            return 1
        draft_ids.append(r.json()["id"])
    record(f"tender draft x{limit} uploaded", True)

    extra = upload_draft(session, headers, "tender", limit)
    ok = extra.status_code == 400 and f"该类型文档已达上限（{limit}个）" in extra.json().get("detail", "")
    record("draft 21st rejected 400", ok, f"status={extra.status_code} detail={extra.json().get('detail', '')!r}")
    if not ok:
        return 1

    # bid 类不受 tender 满员影响
    bid = upload_draft(session, headers, "bid", 0)
    record("bid draft unaffected", bid.status_code == 201, f"status={bid.status_code}")
    bid_id = bid.json()["id"] if bid.status_code == 201 else None

    # 建项目并关联 20 份
    proj = session.post(f"{BASE}/projects", json={"name": "limit-e2e", "description": "e2e"}, headers=headers, timeout=30)
    if proj.status_code not in (200, 201):
        record("create project", False, f"status={proj.status_code} body={proj.text[:200]}")
        return 1
    project_id = proj.json()["id"]

    for i, doc_id in enumerate(draft_ids):
        r = session.post(
            f"{BASE}/documents/{doc_id}/attach", params={"project_id": project_id}, headers=headers, timeout=30
        )
        if r.status_code != 200:
            record(f"attach #{i + 1}", False, f"status={r.status_code} body={r.text[:200]}")
            return 1
    record(f"attach x{limit} ok", True)

    # 闸门②：attach 第 21 份 → 400
    new_draft = upload_draft(session, headers, "tender", limit + 1)
    record("new tender draft after attach", new_draft.status_code == 201, f"status={new_draft.status_code}")
    r = session.post(
        f"{BASE}/documents/{new_draft.json()['id']}/attach",
        params={"project_id": project_id},
        headers=headers,
        timeout=30,
    )
    ok = r.status_code == 400 and f"该类型文档已达上限（{limit}个）" in r.json().get("detail", "")
    record("attach 21st rejected 400", ok, f"status={r.status_code} detail={r.json().get('detail', '')!r}")
    if not ok:
        return 1

    # 闸门③：项目内直传第 21 份 → 400
    r = session.post(
        f"{BASE}/projects/{project_id}/documents",
        params={"doc_type": "tender"},
        files={"file": (f"limit_e2e_direct_{limit}.pdf", MINIMAL_PDF, "application/pdf")},
        headers=headers,
        timeout=60,
    )
    ok = r.status_code == 400 and f"该类型文档已达上限（{limit}个）" in r.json().get("detail", "")
    record("project direct upload 21st rejected 400", ok, f"status={r.status_code} detail={r.json().get('detail', '')!r}")

    # 清理：删草稿 + 删项目（尽力而为）
    cleaned = 0
    for doc_id in [new_draft.json().get("id")] + ([bid_id] if bid_id else []):
        if doc_id:
            cleaned += session.delete(f"{BASE}/documents/{doc_id}", headers=headers, timeout=30).status_code == 204
    deleted_proj = session.delete(f"{BASE}/projects/{project_id}", headers=headers, timeout=30).status_code in (200, 204)
    record("cleanup", deleted_proj and cleaned >= 1, f"docs={cleaned} project={deleted_proj}")

    failed = [name for name, ok_, _ in results if not ok_]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
