# -*- coding: utf-8 -*-
"""AI编标（bid-wizard）全流程无界面 E2E——真实招标文件 + doc/workspace 素材。

目的：23 号 §8 批次五前的全链路质量验证。覆盖（产品真实链路，零前端零 Word）：
  prep         建用户(JWT 直铸)+钱包 → 建向导 → 传真实招标 docx → 等解析
  analyze      POST /analysis（同步 LLM 解读）
  materials    传 doc/workspace 素材(.md) → 等自动索引
  questionnaire生成问卷 → 全采纳保存需求
  capability   生成应答口径(含口径卡) → 整批确认保存
  spec         生成大纲 → 确认
  write        选章子集(--chapters N)发起撰写 → 轮询 → 落盘章节 + 质量指标
  review       审读轮(预估→发起→轮询) → 跨任务最新章节复算指标
  report       汇总 quality_report.json + gate 结论（供迭代工作流判定）

用法（conda env bjt-agent，仓库根为 PYTHONPATH）：
  D:/miniconda3/envs/bjt-agent/python.exe tool/e2e_wizard_fullflow.py --run r1 --phase all
  ... --run r1 --phase write --chapters 3      # 复用已就绪向导，只重跑撰写
  ... --run r1 --phase write --chapters 0      # 0=全大纲

报告目录：tool/e2e-reports/<run>/（state.json / sections/*.md / quality_report.json）
注意：本机 httpx 与 uvicorn 不合，一律 requests（AGENTS 16 号 §0.4）。
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

BASE = "http://127.0.0.1:8000/api"
USERNAME = "test_e2e_fullflow"  # 命中清理脚本 test_% 模式
WALLET_TOPUP_POINTS = Decimal("200000")

TENDER_GLOB = "E:/dvlp/doc/项目/河南省信息咨询设计研究有限公司AI辅助投标系统/*技术规范书*.docx"
MATERIAL_DIR = REPO_ROOT.parent / "doc" / "workspace"
# 素材与招标主题（AI辅助投标系统建设）的相关映射；category 仅备注用途
MATERIAL_FILES = [
    ("03-projects.md", "产品方案"),
    ("16-bid-generation.md", "技术方案"),
    ("20-bid-wizard.md", "技术方案"),
    ("22-tech-bid-sop.md", "技术方案"),
    ("23-bid-wizard-optimization.md", "技术方案"),
    ("05-production-env.md", "运维方案"),
    ("00-overview.md", "公司简介"),
]

REPORT_ROOT = REPO_ROOT / "tool" / "e2e-reports"

# 本地复算用：营销套话词（措辞铁律①——独立于产品机检，双保险口径）
_MARKETING_WORDS = (
    "全方位", "多维度", "一站式", "赋能", "助力", "打造",
    "强大的", "优秀的", "完善的", "深入", "深度融合",
)

_state: dict = {}
_run_dir: Path


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"FATAL: {msg}", flush=True)
    save_state()
    sys.exit(2)


def save_state() -> None:
    _run_dir.mkdir(parents=True, exist_ok=True)
    (_run_dir / "state.json").write_text(
        json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_state(run: str, reuse: str | None = None) -> None:
    global _run_dir
    _run_dir = REPORT_ROOT / run
    _run_dir.mkdir(parents=True, exist_ok=True)
    p = _run_dir / "state.json"
    if not p.exists() and reuse:
        src = REPORT_ROOT / reuse / "state.json"
        if src.exists():
            _state.update(json.loads(src.read_text(encoding="utf-8")))
            log(f"state reused from {reuse}: wizard={_state.get('wizard_id')}")
            save_state()
            return
    if p.exists():
        _state.update(json.loads(p.read_text(encoding="utf-8")))
        log(f"state loaded: wizard={_state.get('wizard_id')} keys={sorted(_state)}")


# ---------------------------------------------------------------- auth

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
                        id=user_id, username=USERNAME,
                        email=f"{USERNAME}@local", password_hash="local-e2e",
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
            elif Decimal(str(wallet["recharge_balance_points"])) < Decimal("10000"):
                await db.execute(
                    UserWallet.__table__.update()
                    .where(UserWallet.user_id == user_id)
                    .values(recharge_balance_points=WALLET_TOPUP_POINTS)
                )
            await db.commit()
            return user_id

    return asyncio.run(inner())


# ---------------------------------------------------------------- http

class Api:
    def __init__(self) -> None:
        self.user_id = ensure_user_with_points()
        self.retoken()

    def retoken(self) -> None:
        self.headers = {"Authorization": f"Bearer {make_token(self.user_id)}"}

    def call(self, method: str, path: str, *, timeout: int = 120, **kw):
        url = f"{BASE}{path}"
        last = None
        for attempt in range(6):
            try:
                r = requests.request(method, url, headers=self.headers, timeout=timeout, **kw)
                if r.status_code == 409 and "ACTIVE_BILLING_TASK" in r.text:
                    last = r
                    log(f"409 active-billing gate, wait 15s retry ({attempt+1}/6) ...")
                    time.sleep(15)
                    self.retoken()
                    continue
                if r.status_code >= 500:
                    last = r
                    log(f"{r.status_code} from {method} {path}, retry in 10s ({attempt+1}/6)")
                    time.sleep(10)
                    self.retoken()
                    continue
                return r
            except requests.ConnectionError as exc:
                last = exc
                log(f"conn err {method} {path}: {exc}; retry 10s ({attempt+1}/6)")
                time.sleep(10)
        return last

    def ok(self, method: str, path: str, want: tuple[int, ...], *, timeout: int = 120, **kw):
        r = self.call(method, path, timeout=timeout, **kw)
        code = getattr(r, "status_code", 0)
        if code not in want:
            die(f"{method} {path} -> {code}: {getattr(r, 'text', r)}")
        return r.json()

    def get(self, path: str, **kw):
        return self.ok("GET", path, (200,), **kw)

    def post(self, path: str, want: tuple[int, ...] = (200, 201), **kw):
        return self.ok("POST", path, want, **kw)


def wait_for(desc: str, check, timeout_s: int, interval_s: float = 10.0):
    started = time.monotonic()
    last = None
    while time.monotonic() - started < timeout_s:
        try:
            last = check()
        except Exception as exc:  # noqa: BLE001
            last = f"check error: {exc}"
        done = last if isinstance(last, bool) else None
        if done is True:
            log(f"{desc}: done in {time.monotonic()-started:.0f}s")
            return True
        log(f"{desc}: ... {last if isinstance(last, str) else ''} ({time.monotonic()-started:.0f}s)")
        time.sleep(interval_s)
    die(f"{desc}: TIMEOUT after {timeout_s}s (last={last})")


# ---------------------------------------------------------------- phases

def phase_prep(api: Api) -> None:
    access = api.get("/bid-wizard/access")
    if not access.get("enabled"):
        die(f"wizard access disabled: {access}")
    me = api.get("/auth/me")
    log(f"auth ok user={me.get('username')}")

    wizard = api.post("/bid-wizard/wizards", json={})
    _state.update(wizard_id=wizard["id"], project_id=wizard.get("project_id", ""))
    log(f"wizard created: {wizard['id']} stage={wizard.get('stage')}")
    save_state()

    matches = sorted(Path("E:/dvlp/doc/项目/河南省信息咨询设计研究有限公司AI辅助投标系统").glob("*技术规范书*.docx"))
    if not matches:
        die(f"tender docx not found for glob {TENDER_GLOB}")
    tender_path = matches[0]
    log(f"tender file: {tender_path.name} ({tender_path.stat().st_size} bytes)")
    up = api.post(
        f"/bid-wizard/wizards/{_state['wizard_id']}/tender",
        files={"file": (tender_path.name, tender_path.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        timeout=300,
    )
    _state["tender_doc_id"] = up.get("document_id", "")
    log(f"tender uploaded doc={_state['tender_doc_id']}")
    save_state()

    def parsed() -> bool:
        doc = api.get(f"/projects/{_state['project_id']}/documents/{_state['tender_doc_id']}")
        st = doc.get("status")
        if st == "failed":
            die(f"tender parse failed: {doc}")
        return st == "parsed"

    wait_for("tender parse", parsed, timeout_s=900, interval_s=10)


def phase_analyze(api: Api) -> None:
    log("POST /analysis（同步 LLM，可能数分钟）...")
    t0 = time.monotonic()
    w = api.post(f"/bid-wizard/wizards/{_state['wizard_id']}/analysis", timeout=900)
    analysis = w.get("analysis") or {}
    reqs = analysis.get("tender_requirements") or []
    log(f"analysis done in {time.monotonic()-t0:.0f}s: requirements={len(reqs)} "
        f"suggested_materials={len(analysis.get('suggested_materials') or [])}")
    (_run_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    if not reqs:
        die("analysis produced no tender_requirements")
    _state["analysis_done"] = True
    save_state()


def phase_materials(api: Api) -> None:
    wid = _state["wizard_id"]
    pending = [f for f in MATERIAL_FILES if f[0] not in _state.get("materials_uploaded", [])]
    for fname, category in pending:
        path = MATERIAL_DIR / fname
        r = api.post(
            f"/bid-wizard/wizards/{wid}/materials",
            files={"file": (fname, path.read_bytes(), "text/markdown")},
            params={"category": category},
            timeout=300,
        )
        _state.setdefault("materials_uploaded", []).append(fname)
        save_state()
        log(f"material uploaded: {fname} ({path.stat().st_size}B) -> {r.get('id')}")

    def all_indexed() -> bool:
        rows = api.get(f"/bid-wizard/wizards/{wid}/materials")
        states = [(r.get("original_filename"), r.get("index_status"), r.get("index_error")) for r in rows]
        bad = [s for s in states if s[1] == "failed"]
        if bad:
            die(f"material index failed: {bad}")
        pending = [s for s in states if s[1] not in ("indexed", "failed")]
        if pending:
            log(f"index pending: {pending}")
            return False
        return True

    wait_for("materials indexed", all_indexed, timeout_s=2400, interval_s=20)
    rows = api.get(f"/bid-wizard/wizards/{wid}/materials")
    log("materials indexed: " + ", ".join(
        f"{r.get('original_filename')}:{r.get('index_status')}/chunks={r.get('chunk_count')}" for r in rows))


def phase_questionnaire(api: Api) -> None:
    wid = _state["wizard_id"]
    log("POST /questionnaire（同步 LLM）...")
    t0 = time.monotonic()
    w = api.post(f"/bid-wizard/wizards/{wid}/questionnaire", timeout=900)
    qs = (w.get("questionnaire") or {}).get("questions") or []
    log(f"questionnaire done in {time.monotonic()-t0:.0f}s: {len(qs)} questions")
    if not qs:
        die("no questionnaire questions")
    api.ok(
        "PUT", f"/bid-wizard/wizards/{wid}/requirements",
        (200,),
        json={"answers": [{"question_id": q["id"], "action": "adopted", "answer": None} for q in qs]},
    )
    log(f"requirements saved ({len(qs)} adopted)")
    _state["questionnaire_done"] = True
    save_state()


def phase_capability(api: Api) -> None:
    wid = _state["wizard_id"]
    log("POST /capability（同步 LLM，可能数分钟）...")
    t0 = time.monotonic()
    w = api.post(f"/bid-wizard/wizards/{wid}/capability", timeout=900)
    reqs = w.get("requirements") or {}
    cap = reqs.get("capability_map") or []
    style = reqs.get("style_card")
    log(f"capability done in {time.monotonic()-t0:.0f}s: items={len(cap)} style_card={style}")
    (_run_dir / "capability.json").write_text(
        json.dumps({"capability_map": cap, "style_card": style}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if not cap:
        die("capability map empty")
    items = [
        {
            "title": it.get("title", ""),
            "requirement": it.get("requirement") or "",
            "stance": it.get("stance", "confirm"),
            "evidence": (it.get("evidence") or "")[:200],
            "note": it.get("note") or "",
        }
        for it in cap
    ]
    body = {"items": items}
    if isinstance(style, dict):
        body["style_card"] = style
    w2 = api.ok("PUT", f"/bid-wizard/wizards/{wid}/capability", (200,), json=body, timeout=120)
    saved = (w2.get("requirements") or {}).get("capability_map") or []
    log(f"capability confirmed & saved: {len(saved)} items")
    _state["capability_done"] = True
    save_state()


def phase_spec(api: Api) -> None:
    wid = _state["wizard_id"]
    log("POST /spec（同步 LLM，可能数分钟）...")
    t0 = time.monotonic()
    w = api.post(f"/bid-wizard/wizards/{wid}/spec", timeout=900)
    spec = w.get("spec") or []
    log(f"spec done in {time.monotonic()-t0:.0f}s: nodes={len(spec)}")
    if len(spec) < 3:
        die(f"spec too small: {len(spec)}")
    (_run_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    conf = api.post(f"/bid-wizard/wizards/{wid}/spec/confirm", timeout=120)
    if not conf.get("spec_confirmed_at"):
        die("spec confirm failed")
    _state["spec_confirmed"] = True
    save_state()
    roots = [n for n in spec if "." not in str(n.get("node_id"))]
    log(f"spec confirmed: {len(spec)} nodes, {len(roots)} level-1 chapters: "
        + " / ".join(str(n.get("title"))[:14] for n in roots))


def _select_nodes(spec: list, chapters: int) -> list[str]:
    ids = [str(n.get("node_id")) for n in spec]
    roots = [i for i in ids if "." not in i]
    if chapters and chapters > 0:
        chosen_roots = roots[:chapters]
        return [i for i in ids if i in chosen_roots or any(i.startswith(r + ".") for r in chosen_roots)]
    return ids


def poll_task(api: Api, task_id: str, desc: str, timeout_s: int) -> dict:
    started = time.monotonic()
    last = ""
    while time.monotonic() - started < timeout_s:
        t = api.get(f"/bid-wizard/writing-tasks/{task_id}")
        st = t.get("status")
        if st != last:
            log(f"{desc}: status={st} ({time.monotonic()-started:.0f}s)")
            last = st
        if st in ("completed", "failed", "cancelled"):
            if st != "completed":
                die(f"{desc} ended {st}: {t.get('error_message')} summary={t.get('summary')}")
            return t
        time.sleep(20)
    die(f"{desc}: TIMEOUT {timeout_s}s")


def dump_sections(api: Api, tag: str) -> list[dict]:
    """按跨任务最新口径取章节内容，落盘并返回带 content 的清单。"""
    wid = _state["wizard_id"]
    latest = api.get(f"/bid-wizard/wizards/{wid}/sections/latest")
    out_dir = _run_dir / f"sections-{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for row in latest:
        node_id = row.get("node_id")
        task_id = row.get("task_id")
        if not task_id:
            continue
        detail = api.get(f"/bid-wizard/writing-tasks/{task_id}/sections/{node_id}", timeout=120)
        content = detail.get("content") or ""
        safe_title = re.sub(r"[\\/:*?\"<>|]", "_", str(detail.get("title") or ""))[:30]
        # 服务端 content 自带标题行（{'#'*level} title）；再拼「# node_id title」会产生连排双标题
        # （第 3 轮质量轮：评估三轮均误报为产品缺陷，实为本 dump 产物的观察伪影）——
        # content 已以标题行开头时原样落盘，文件名已含 node_id 与标题可定位
        body_text = content if re.match(r"^\s*#{1,6}\s+\S", content) else (
            f"# {node_id} {detail.get('title')}\n\n{content}")
        (out_dir / f"{node_id.replace('.', '_')}-{safe_title}.md").write_text(body_text, encoding="utf-8")
        out.append({
            "node_id": node_id, "title": detail.get("title"),
            "status": detail.get("status"), "word_count": detail.get("word_count"),
            "content": content, "task_id": task_id,
        })
    log(f"dumped {len(out)} latest sections -> {out_dir}")
    return out


def compute_metrics(sections: list[dict], spec: list, style_card: dict | None) -> dict:
    from backend.agent.bid_doc_checks import _AI_FLAVOR_MARKERS, draft_checks

    by_id = {str(n.get("node_id")): n for n in spec}
    joined = "\n\n".join(s["content"] or "" for s in sections)
    m: dict = {}
    m["section_count"] = len(sections)
    m["not_generated"] = sum(1 for s in sections if s.get("status") not in ("generated", "written"))
    m["total_words"] = sum(int(s.get("word_count") or 0) for s in sections)

    ai_hits = {}
    for marker in _AI_FLAVOR_MARKERS:
        c = joined.count(marker)
        if c:
            ai_hits[marker] = c
    m["ai_flavor_hits"] = ai_hits
    m["ai_flavor_total"] = sum(ai_hits.values())

    marketing = {w: joined.count(w) for w in _MARKETING_WORDS if joined.count(w)}
    m["marketing_words"] = marketing
    m["marketing_total"] = sum(marketing.values())
    chars = len(re.sub(r"\s", "", joined)) or 1
    m["marketing_per_1k"] = round(m["marketing_total"] * 1000 / chars, 2)

    ratio_rows = []
    for s in sections:
        node = by_id.get(s["node_id"]) or {}
        planned = int(node.get("article_count") or 2) * int(node.get("text_count") or 400)
        actual = int(s.get("word_count") or 0)
        if planned:
            ratio_rows.append({
                "node_id": s["node_id"], "title": s.get("title"),
                "planned": planned, "actual": actual, "ratio": round(actual / planned, 2),
            })
    # 与服务端 draft_checks 的 is_leaf 同口径（第 2 轮质量轮：父章按全稿约定只写章首导语，
    # 按子节规划篇幅判短是误报——2.2 审读压缩为三至五句总述后合法地只有数百字）
    all_ids = [str(n.get("node_id") or "") for n in spec]
    def _is_leaf(nid: str) -> bool:
        return not any(other != nid and other.startswith(nid + ".") for other in all_ids)
    m["short_sections"] = [r for r in ratio_rows if r["ratio"] < 0.5 and _is_leaf(r["node_id"])]
    m["overlong_sections"] = [r for r in ratio_rows if r["ratio"] > 2.0 and _is_leaf(r["node_id"])]
    m["word_ratio_rows"] = ratio_rows

    m["draft_checks"] = draft_checks(
        [{"node_id": s["node_id"], "title": s.get("title"),
          "content": s["content"] or "", "word_count": s.get("word_count"),
          "article_count": (by_id.get(s["node_id"]) or {}).get("article_count"),
          "text_count": (by_id.get(s["node_id"]) or {}).get("text_count")} for s in sections],
        style_card=style_card,
    )
    m["draft_checks_error_count"] = sum(1 for i in m["draft_checks"] if i.get("severity") == "error")
    return m


def gate(metrics: dict, doc_checks: list | None) -> dict:
    g = {
        "no_unfinished": metrics["not_generated"] == 0,
        "no_ai_flavor": metrics["ai_flavor_total"] == 0,
        "no_check_errors": metrics["draft_checks_error_count"] == 0,
        "marketing_ok": metrics["marketing_per_1k"] <= 2.0,
        "short_ok": len(metrics["short_sections"]) == 0,
    }
    if isinstance(doc_checks, list):
        g["server_docchecks_no_error"] = not any(
            str(i.get("severity") or "").lower() == "error" for i in doc_checks)
    g["overall"] = all(v for k, v in g.items() if k != "overall")
    return g


def _get_spec(api: Api) -> list:
    w = api.get(f"/bid-wizard/wizards/{_state['wizard_id']}")
    spec = w.get("spec") or []
    if not spec:
        die("wizard has no spec")
    (_run_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return spec


def phase_write(api: Api, chapters: int, nodes_override: str | None = None) -> None:
    wid = _state["wizard_id"]
    spec = _get_spec(api)
    if nodes_override:
        node_ids = [n.strip() for n in nodes_override.split(",") if n.strip()]
    else:
        node_ids = _select_nodes(spec, chapters)
    log(f"write task: {len(node_ids)} nodes selected ({'nodes=' + nodes_override if nodes_override else 'chapters=' + str(chapters or 'ALL')})")
    task = api.post(f"/bid-wizard/wizards/{wid}/writing-tasks",
                    json={"node_ids": node_ids}, timeout=120)
    _state["write_task_id"] = task["id"]
    save_state()
    t = poll_task(api, task["id"], f"write {task['id'][:8]}", timeout_s=5400)
    summary = t.get("summary") or {}
    log(f"write summary keys: {sorted(summary)}")
    (_run_dir / "write_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _state["write_summary_docchecks"] = summary.get("doc_checks")
    save_state()


def phase_review(api: Api) -> None:
    wid = _state["wizard_id"]
    tid = _state.get("write_task_id")
    if not tid:
        die("no write task; run --phase write first")
    est = api.get(f"/bid-wizard/writing-tasks/{tid}/review-estimate")
    log(f"review estimate: {est}")
    task = api.post(f"/bid-wizard/writing-tasks/{tid}/review", timeout=120)
    _state["review_task_id"] = task["id"]
    save_state()
    t = poll_task(api, task["id"], f"review {task['id'][:8]}", timeout_s=5400)
    summary = t.get("summary") or {}
    (_run_dir / "review_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _state["review_summary_docchecks"] = summary.get("doc_checks")
    save_state()
    log(f"review summary keys: {sorted(summary)}")


def phase_report(api: Api, stage: str) -> None:
    spec = _get_spec(api)
    w = api.get(f"/bid-wizard/wizards/{_state['wizard_id']}")
    style_card = (w.get("requirements") or {}).get("style_card")
    sections = dump_sections(api, tag=stage)
    if not sections:
        die("no sections to report on")
    metrics = compute_metrics(sections, spec, style_card)
    # server 机检取「该向导最新任务」summary 的复算结果（write/review/单章重生成后都成立）
    latest_task = api.get(f"/bid-wizard/writing-tasks/latest?wizard_id={_state['wizard_id']}")
    doc_checks = (latest_task.get("summary") or {}).get("doc_checks")
    log(f"server doc_checks from latest task {latest_task.get('id', '')[:8]}: {len(doc_checks) if isinstance(doc_checks, list) else 0} items")
    g = gate(metrics, doc_checks if isinstance(doc_checks, list) else None)
    report = {
        "run": _run_dir.name, "stage": stage, "generated_at": datetime.now().isoformat(),
        "wizard_id": _state.get("wizard_id"),
        "write_task_id": _state.get("write_task_id"),
        "review_task_id": _state.get("review_task_id"),
        "metrics": metrics, "gate": g,
    }
    (_run_dir / f"quality_report_{stage}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    compact = {
        "stage": stage, "sections": metrics["section_count"],
        "words": metrics["total_words"],
        "ai_flavor_total": metrics["ai_flavor_total"],
        "ai_flavor_hits": metrics["ai_flavor_hits"],
        "marketing_per_1k": metrics["marketing_per_1k"],
        "draft_check_issues": len(metrics["draft_checks"]),
        "draft_check_errors": metrics["draft_checks_error_count"],
        "short": len(metrics["short_sections"]),
        "overlong": len(metrics["overlong_sections"]),
        "gate": g,
    }
    print("METRICS_JSON " + json.dumps(compact, ensure_ascii=False), flush=True)
    (_run_dir / f"gate_{stage}.json").write_text(
        json.dumps(compact, ensure_ascii=False, indent=1), encoding="utf-8")
    if not g["overall"]:
        print(f"GATE: FAIL -> see {_run_dir / f'quality_report_{stage}.json'}", flush=True)
    else:
        print("GATE: PASS", flush=True)


PHASES = ["prep", "analyze", "materials", "questionnaire", "capability", "spec", "write", "review"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--reuse-state", default=None,
                    help="本 run 无 state 时，从该 run 复制向导状态（迭代轮复用同一向导）")
    ap.add_argument("--phase", default="all",
                    help="all 或逗号分隔：prep,analyze,materials,questionnaire,capability,spec,write,review,report-write,report-review")
    ap.add_argument("--chapters", type=int, default=3,
                    help="撰写选前 N 个一级章（0=全部）")
    ap.add_argument("--nodes", default=None,
                    help="逗号分隔 node_ids，直接指定撰写节点（单章重生成）")
    args = ap.parse_args()

    load_state(args.run, reuse=args.reuse_state)
    api = Api()
    phases = PHASES if args.phase == "all" else [p.strip() for p in args.phase.split(",")]
    for ph in phases:
        t0 = time.monotonic()
        log(f"===== phase {ph} =====")
        if ph == "prep":
            phase_prep(api)
        elif ph == "analyze":
            phase_analyze(api)
        elif ph == "materials":
            phase_materials(api)
        elif ph == "questionnaire":
            phase_questionnaire(api)
        elif ph == "capability":
            phase_capability(api)
        elif ph == "spec":
            phase_spec(api)
        elif ph == "write":
            phase_write(api, args.chapters, nodes_override=args.nodes)
            phase_report(api, "write")
        elif ph == "review":
            phase_review(api)
            phase_report(api, "review")
        elif ph == "report-write":
            phase_report(api, "write")
        elif ph == "report-review":
            phase_report(api, "review")
        else:
            die(f"unknown phase {ph}")
        log(f"===== phase {ph} done in {time.monotonic()-t0:.0f}s =====")
    save_state()


if __name__ == "__main__":
    main()
