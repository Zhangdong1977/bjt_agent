# -*- coding: utf-8 -*-
"""e2e_bidfig_compare.py — 真实 LLM E2E：同一大纲双任务对比 mermaid vs bidfig 图表管线。

流程：登录（JWT 直签）→ 建项目+上传招标 PDF → 顺序跑两个生成任务
（M=默认 mermaid；B=options.fig_mode=bidfig，同大纲同分析）→ 读 workspace
原始 section md 统计图表块 → mermaid 块经 Chrome headless 真实渲染判定成功率
（复刻线上 gantt 预处理）→ bidfig 块经 render_spec 校验/渲染 → API assembled
出口核验（bidfig-svg/降级占位）→ 写报告 scripts/demo-out/e2e-bidfig-report.md。

前置：VPN 连通；本地栈已起（仓库根 `scripts/bjt.ps1 start`）。
用法：backend/ 目录下 PYTHONIOENCODING=utf-8 PYTHONPATH=<仓库根> python ../scripts/e2e_bidfig_compare.py
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from e2e_bid_generation import BASE, ensure_user, make_token, make_tender_pdf, wait_status  # noqa: E402
from demo_bidfig_compare import CHROME, prepare_gantt  # noqa: E402

from backend.agent.bid_draft_agent import extract_mermaid_fences  # noqa: E402
from backend.agent.bidfig import extract_bidfig_fences, render_spec  # noqa: E402
from backend.agent.bidfig.fences import INVALID_PLACEHOLDER  # noqa: E402
from backend.config import get_settings  # noqa: E402

OUTLINE = [
    {"title": "系统总体架构设计", "level": 1, "article_count": 2, "text_count": 400,
     "requirement": "说明系统分层结构与各层职责，需配系统总体架构图"},
    {"title": "项目实施进度计划", "level": 1, "article_count": 2, "text_count": 350,
     "requirement": "给出实施阶段划分、任务与里程碑，需配进度横道图（甘特图）"},
    {"title": "项目实施与服务流程", "level": 1, "article_count": 2, "text_count": 350,
     "requirement": "说明实施与服务各阶段步骤，需配实施/服务流程图"},
    {"title": "系统对接与交互设计", "level": 1, "article_count": 2, "text_count": 350,
     "requirement": "说明与既有系统及各参与方的交互过程，需配交互时序图"},
]
ANALYSIS = {
    "basic": {"project_name": "智慧园区信息化项目（图表对比联调）"},
    "tender_requirements": ["园区综合管理平台", "物联感知网络", "运维保障方案"],
    "scoring_criteria": ["技术方案40分", "价格30分"],
    "rejection_items": ["未签字盖章否决"],
}
TIMEOUT_S = 900


def fence_payload(block: str) -> str:
    """剥掉围栏首尾行，取正文。"""
    lines = block.strip().split("\n")
    return "\n".join(lines[1:-1]).strip() if len(lines) >= 3 else ""


def run_task(session, headers, project_id, document_id, tag, options):
    body = {
        "project_id": project_id,
        "tender_document_id": document_id,
        "analysis": ANALYSIS,
        "outline": OUTLINE,
    }
    if options:
        body["options"] = options
    resp = session.post(f"{BASE}/bid-draft/tasks", json=body, headers=headers, timeout=60)
    assert resp.status_code == 201, f"[{tag}] create failed: {resp.status_code} {resp.text[:200]}"
    task_id = resp.json()["id"]
    print(f"[{tag}] task={task_id} created", flush=True)
    final = wait_status(session, f"{BASE}/bid-draft/tasks/{task_id}",
                        {"completed", "failed", "cancelled"}, TIMEOUT_S, headers)
    assert final.get("status") == "completed", f"[{tag}] status={final.get('status')} err={final.get('error_message')}"
    # 任务 completed ≠ 章节全部成功（fail-soft 设计），必须核 bid_draft_sections.status
    secs = session.get(f"{BASE}/bid-draft/tasks/{task_id}/sections", headers=headers, timeout=60)
    metas = secs.json() if secs.status_code == 200 else []
    bad = [m for m in metas if m.get("status") != "generated"]
    assert len(metas) == len(OUTLINE) and not bad, \
        f"[{tag}] sections={[(m.get('node_id'), m.get('status')) for m in metas]}"
    if options and options.get("fig_mode") == "bidfig":
        print(f"[{tag}] summary={json.dumps(final.get('summary') or {}, ensure_ascii=False)[:600]}", flush=True)
    return task_id


def raw_sections(task_id: str) -> str:
    ws = Path(get_settings().workspace_path) / "bid-draft" / task_id / "sections"
    files = sorted(ws.glob("*.md"))
    assert files, f"sections 目录为空: {ws}"
    return "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def analyze(task_id: str, tag: str):
    raw = raw_sections(task_id)
    result = {"tag": tag, "task_id": task_id, "mermaid": [], "bidfig": []}
    for fence in extract_mermaid_fences(raw):
        payload = fence_payload(raw[fence["start"]:fence["end"]])
        prepared, _ = prepare_gantt(payload) if payload.startswith("gantt") else (payload, "")
        result["mermaid"].append(prepared)
    lines = raw.split("\n")
    for fence in extract_bidfig_fences(raw):
        payload = "\n".join(lines[fence["start"] + 1:fence["end"]]).strip()
        try:
            render_spec(json.loads(payload))
            result["bidfig"].append(("ok", payload))
        except Exception as exc:  # noqa: BLE001
            result["bidfig"].append(("err", f"{exc}"))
    return result


def mermaid_render_check(blocks: dict[str, str]) -> dict[str, str]:
    """Chrome headless 里逐块 mermaid.render，返回 id → OK/ERR。"""
    out_dir = HERE / "demo-out"
    out_dir.mkdir(exist_ok=True)
    js = out_dir / "mermaid.min.js"
    if not js.exists():
        import shutil

        shutil.copy(HERE.parent / "frontend" / "node_modules" / "mermaid" / "dist" / "mermaid.min.js", js)
    blocks_json = json.dumps(
        [{"id": k, "code": v} for k, v in blocks.items()], ensure_ascii=False
    ).replace("<", "\\u003c")
    page = out_dir / "e2e-mermaid-check.html"
    page.write_text(
        """<!doctype html><meta charset="utf-8">
<script src="mermaid.min.js"></script>
<pre id="out">pending</pre>
<script>
mermaid.initialize({startOnLoad:false, theme:"default", securityLevel:"strict",
  fontFamily: '"Microsoft YaHei", "PingFang SC", Arial, sans-serif',
  flowchart:{useMaxWidth:false,curve:"basis"}, gantt:{useMaxWidth:false,topAxis:true}});
const blocks = """ + blocks_json + """;
(async () => {
  const out = []; let seq = 0;
  for (const b of blocks) {
    try { await mermaid.render("e2e-" + (++seq) + "-" + Date.now(), b.code); out.push(b.id + " OK"); }
    catch (e) { out.push(b.id + " ERR " + String(e).slice(0, 100)); }
  }
  document.getElementById("out").textContent = out.join("\\n");
})();
</script>""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [CHROME, "--headless=new", "--dump-dom", "--virtual-time-budget=20000", page.as_uri()],
        capture_output=True, text=True, timeout=180, check=True,
    )
    m = re.search(r'<pre id="out">([^<]*)</pre>', proc.stdout)
    verdicts = {}
    for line in (m.group(1) if m else "").split("\n"):
        line = line.strip()
        if " " in line:
            bid, verdict = line.split(" ", 1)
            verdicts[bid] = verdict
    return verdicts


def main() -> None:
    user_id = ensure_user()
    headers = {"Authorization": f"Bearer {make_token(user_id)}"}
    session = requests.Session()

    proj = session.post(f"{BASE}/projects", json={"name": "E2E图表管线对比", "project_type": "bid_draft"},
                        headers=headers, timeout=60)
    assert proj.status_code == 201, proj.text[:200]
    project_id = proj.json()["id"]
    up = session.post(f"{BASE}/projects/{project_id}/documents", params={"doc_type": "tender"},
                      files={"file": ("tender-e2e.pdf", io.BytesIO(make_tender_pdf()), "application/pdf")},
                      headers=headers, timeout=120)
    assert up.status_code == 201, up.text[:200]
    document_id = up.json()["id"]
    doc = wait_status(session, f"{BASE}/projects/{project_id}/documents/{document_id}",
                      {"parsed", "failed"}, 300, headers)
    assert doc.get("status") == "parsed", doc

    # 计费 409：同用户活跃计费任务=1，两任务必须串行
    task_m = run_task(session, headers, project_id, document_id, "M-mermaid", None)
    task_b = run_task(session, headers, project_id, document_id, "B-bidfig", {"fig_mode": "bidfig"})

    stat_m = analyze(task_m, "M")
    stat_b = analyze(task_b, "B")

    kinds: dict[str, int] = {}
    for st, info in stat_b["bidfig"]:
        if st == "ok":
            k = json.loads(info).get("kind", "?")
            kinds[k] = kinds.get(k, 0) + 1
    # 本轮验证目标：flow/seq 两类新原语被 LLM 真实采用且渲染成功
    assert {"flow", "seq"} <= set(kinds), f"B 任务 bidfig 类型分布缺 flow/seq: {kinds}"

    blocks = {}
    for i, code in enumerate(stat_m["mermaid"]):
        blocks[f"M-mm-{i + 1}"] = code
    for i, code in enumerate(stat_b["mermaid"]):
        blocks[f"B-mm-{i + 1}"] = code
    verdicts = mermaid_render_check(blocks) if blocks else {}

    def api_assembled(task_id):
        r = session.get(f"{BASE}/bid-draft/tasks/{task_id}/assembled", headers=headers, timeout=60)
        return (r.json().get("content") or "") if r.status_code == 200 else ""

    asm_m, asm_b = api_assembled(task_m), api_assembled(task_b)

    mm_m_ok = sum(1 for i in range(len(stat_m["mermaid"])) if "OK" in verdicts.get(f"M-mm-{i + 1}", "ERR"))
    mm_b_ok = sum(1 for i in range(len(stat_b["mermaid"])) if "OK" in verdicts.get(f"B-mm-{i + 1}", "ERR"))
    bf_ok = sum(1 for st, _ in stat_b["bidfig"] if st == "ok")

    lines = [
        "# E2E 图表管线对比报告（真实 LLM，同大纲双任务）",
        "",
        f"- 任务 M（默认 mermaid）：{task_m}",
        f"- 任务 B（fig_mode=bidfig）：{task_b}",
        "",
        "| 指标 | M·mermaid 管线 | B·bidfig 管线 |",
        "|---|---|---|",
        f"| mermaid 图表块 | {len(stat_m['mermaid'])} | {len(stat_b['mermaid'])}（流程类兜底） |",
        f"| mermaid 渲染成功 | {mm_m_ok}/{len(stat_m['mermaid'])} | {mm_b_ok}/{len(stat_b['mermaid'])} |",
        f"| bidfig JSON 块 | 0 | {len(stat_b['bidfig'])} |",
        f"| bidfig 校验+渲染成功 | - | {bf_ok}/{len(stat_b['bidfig'])}（类型分布 {kinds}） |",
        f"| API 出口 bidfig-svg 围栏 | 0 | {asm_b.count('```bidfig-svg')} |",
        f"| API 出口降级占位 | {asm_m.count(INVALID_PLACEHOLDER)} | {asm_b.count(INVALID_PLACEHOLDER)} |",
        "",
        "## mermaid 渲染明细",
    ]
    for bid, v in verdicts.items():
        lines.append(f"- {bid}: {v}")
    if stat_b["bidfig"]:
        lines += ["", "## bidfig 块明细"]
        for i, (st, info) in enumerate(stat_b["bidfig"], 1):
            head = json.loads(info).get("kind", "?") if st == "ok" else info[:120]
            lines.append(f"- B-bf-{i}: {st}  {head}")
    report = HERE / "demo-out" / "e2e-bidfig-report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(f"\nreport -> {report}", flush=True)


if __name__ == "__main__":
    main()
