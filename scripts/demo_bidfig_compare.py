# -*- coding: utf-8 -*-
"""demo_bidfig_compare.py — bidfig 原语图 vs mermaid 现行链路 同题对比页。

用途：原型验证的观测材料。左侧为服务端 bidfig 确定性渲染的 SVG；右侧为
同一份数据按现行产品链路（LLM 出 mermaid 源码 → 前端 chartAssets 渲染，
含 prepareGanttForRender 同款预处理：剥 title + 按跨度注入 tickInterval，
title 以题注形式补回，等价于 composeGanttHeader）的呈现。

运行：python scripts/demo_bidfig_compare.py
产物：scripts/demo-out/bidfig-compare.html + bidfig-compare.png（Chrome 截图）
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.bidfig import render_spec  # noqa: E402

OUT = ROOT / "scripts" / "demo-out"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

GANTT_SPEC = {
    "kind": "gantt", "title": "项目实施进度计划（150 天定制开发）",
    "phases": [{"name": "阶段一 标品部署"}, {"name": "阶段二 定制开发"}, {"name": "阶段三 验收移交"}],
    "tasks": [
        {"name": "环境准备与标品私有化部署", "phase": 0, "start": "2026-11-02", "end": "2026-12-10"},
        {"name": "系统对接联调（OA/统一身份/知识库）", "phase": 0, "start": "2026-11-16", "end": "2026-12-24"},
        {"name": "需求调研与详细设计", "phase": 1, "start": "2026-12-25", "end": "2027-01-31"},
        {"name": "定制开发与分批测试", "phase": 1, "start": "2027-02-01", "end": "2027-04-20"},
        {"name": "试运行与性能调优", "phase": 2, "start": "2027-04-21", "end": "2027-05-20"},
        {"name": "验收培训与文档移交", "phase": 2, "start": "2027-05-21", "end": "2027-05-27"},
    ],
    "milestones": [
        {"name": "M1 标品上线", "date": "2026-12-10"},
        {"name": "M2 定制开发完成", "date": "2027-04-20"},
        {"name": "M3 项目验收", "date": "2027-05-27"},
    ],
}

ARCH_SPEC = {
    "kind": "arch", "title": "系统总体架构",
    "layers": [
        {"name": "展现层", "modules": [
            {"name": "标书编制客户端", "sub": ["Word 插件", "Web 端"]},
            {"name": "运营管理后台", "sub": ["配置", "监控"]},
        ]},
        {"name": "应用服务层", "modules": [
            {"name": "标书生成服务", "sub": ["大纲编排", "逐节生成"]},
            {"name": "标书审查服务", "sub": ["规则检查", "AI 复核"]},
            {"name": "知识资产管理", "sub": ["素材沉淀", "检索复用"]},
        ]},
        {"name": "AI 能力层", "modules": [
            {"name": "大模型推理网关", "sub": ["在线受控", "可切换"]},
            {"name": "文档解析引擎", "sub": ["docx/pdf", "压缩包"]},
        ]},
        {"name": "数据与基础层", "modules": [
            {"name": "PostgreSQL"}, {"name": "Redis"}, {"name": "NFS 工作区"},
        ]},
    ],
    "side": [
        {"name": "安全体系", "items": ["传输加密", "权限审计", "数据隔离"]},
        {"name": "运维体系", "items": ["日志监控", "备份恢复"]},
    ],
}

SHORT_SPEC = {
    "kind": "gantt", "title": "短期冲刺排期（8 天）",
    "tasks": [
        {"name": "招标文件解析与要素确认", "start": "2026-11-02", "end": "2026-11-04"},
        {"name": "技术方案章节编制与内审", "start": "2026-11-04", "end": "2026-11-07"},
        {"name": "商务标与装订递交", "start": "2026-11-08", "end": "2026-11-09"},
    ],
    "milestones": [{"name": "递交截止", "date": "2026-11-09"}],
}

MERMAID_GANTT = """gantt
    title {title}
    dateFormat YYYY-MM-DD
    axisFormat %Y-%m
    section 阶段一 标品部署
    环境准备与标品私有化部署 :a1, 2026-11-02, 2026-12-10
    系统对接联调（OA/统一身份/知识库） :a2, 2026-11-16, 2026-12-24
    section 阶段二 定制开发
    需求调研与详细设计 :b1, 2026-12-25, 2027-01-31
    定制开发与分批测试 :b2, 2027-02-01, 2027-04-20
    section 阶段三 验收移交
    试运行与性能调优 :c1, 2027-04-21, 2027-05-20
    验收培训与文档移交 :c2, 2027-05-21, 2027-05-27
    M1 标品上线 :milestone, 2026-12-10, 0d
    M2 定制开发完成 :milestone, 2027-04-20, 0d
    M3 项目验收 :milestone, 2027-05-27, 0d
"""

MERMAID_ARCH = """flowchart TB
    subgraph L1[展现层]
        A1["标书编制客户端<br/>Word 插件 / Web 端"]
        A2["运营管理后台<br/>配置 / 监控"]
    end
    subgraph L2[应用服务层]
        B1["标书生成服务<br/>大纲编排 / 逐节生成"]
        B2["标书审查服务<br/>规则检查 / AI 复核"]
        B3["知识资产管理<br/>素材沉淀 / 检索复用"]
    end
    subgraph L3[AI 能力层]
        C1["大模型推理网关<br/>在线受控 / 可切换"]
        C2["文档解析引擎<br/>docx/pdf / 压缩包"]
    end
    subgraph L4[数据与基础层]
        D1[PostgreSQL]
        D2[Redis]
        D3[NFS 工作区]
    end
    subgraph S1[安全体系]
        E1[传输加密]
        E2[权限审计]
        E3[数据隔离]
    end
    subgraph S2[运维体系]
        F1[日志监控]
        F2[备份恢复]
    end
    L1 --> L2 --> L3 --> L4
    S1 -.-> L2
    S2 -.-> L4
"""

MERMAID_SHORT = """gantt
    title {title}
    dateFormat YYYY-MM-DD
    axisFormat %m-%d
    section 冲刺任务
    招标文件解析与要素确认 :a1, 2026-11-02, 2026-11-04
    技术方案章节编制与内审 :a2, 2026-11-04, 2026-11-07
    商务标与装订递交 :a3, 2026-11-08, 2026-11-09
    递交截止 :milestone, 2026-11-09, 0d
"""


def prepare_gantt(src: str) -> tuple[str, str]:
    """复刻 chartAssets.prepareGanttForRender：剥 title（返回作题注）+ 注 tickInterval。"""
    lines = src.strip().split("\n")
    title = ""
    kept = []
    for ln in lines:
        m = re.match(r"\s*title\s+[:：]?\s*(.+?)\s*$", ln)
        if m and not title:
            title = m.group(1)
            continue
        kept.append(ln)
    dates = sorted(re.findall(r"\d{4}-\d{2}-\d{2}", src))
    if dates:
        span = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days
        interval = "1month" if span > 70 else ("1week" if span > 10 else "")
        if interval:
            idx = next((i for i, ln in enumerate(kept) if re.match(r"\s*axisFormat", ln)), 0)
            kept.insert(idx + 1, "    tickInterval " + interval)
    return "\n".join(kept), title


def cell_bidfig(svg: str) -> str:
    return ('<td class="col"><div class="tag tag-bidfig">bidfig 原语（服务端确定性渲染）</div>'
            + svg + "</td>")


def cell_mermaid(src: str, title: str) -> str:
    body = src.replace("&", "&amp;").replace("<", "&lt;")
    cap = ('<div class="figtitle">%s</div>' % title) if title else ""
    return ('<td class="col"><div class="tag tag-mm">mermaid 现行链路（前端渲染）</div>'
            + cap + '<div class="mermaid">' + body + "</div></td>")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    shutil.copy(ROOT / "frontend" / "node_modules" / "mermaid" / "dist" / "mermaid.min.js",
                OUT / "mermaid.min.js")

    rows = []
    cases = [
        ("案例 A｜项目实施进度横道图（3 阶段 / 6 任务 / 3 里程碑）", GANTT_SPEC, MERMAID_GANTT.format(title=GANTT_SPEC["title"])),
        ("案例 B｜系统总体架构分层图（4 层 / 8 模块 / 2 侧栏）", ARCH_SPEC, MERMAID_ARCH),
        ("案例 C｜8 天短跨度排期（刻度挤叠边界场景）", SHORT_SPEC, MERMAID_SHORT.format(title=SHORT_SPEC["title"])),
    ]
    for heading, spec, mm in cases:
        mm_prepared, mm_title = prepare_gantt(mm) if mm.startswith("gantt") else (mm, "")
        rows.append(
            "<tr><td colspan='2' class='case'>%s</td></tr>"
            "<tr>%s%s</tr>" % (heading, cell_bidfig(render_spec(spec)), cell_mermaid(mm_prepared, mm_title))
        )

    html = """<!doctype html><html><head><meta charset="utf-8">
<style>
body{font-family:"Microsoft YaHei",Arial,sans-serif;margin:16px;background:#fff}
h1{font-size:18px} p.note{color:#666;font-size:12px;max-width:1400px}
table{border-collapse:collapse;width:100%}
td.case{background:#1f2937;color:#fff;font-weight:700;padding:8px 12px;font-size:14px}
td.col{width:50%;vertical-align:top;border:1px solid #ddd;padding:10px;background:#fff}
.tag{font-size:12px;font-weight:700;margin-bottom:6px;padding:2px 8px;border-radius:4px;display:inline-block}
.tag-bidfig{background:#DBEAFE;color:#1D4ED8}.tag-mm{background:#FEF3C7;color:#B45309}
svg{max-width:100%;height:auto}
.figtitle{font-weight:700;font-size:15px;text-align:center;margin:2px 0 6px}
</style></head><body>
<h1>bidfig 原语图 vs mermaid 现行链路 — 同题对比（2026-10-18 原型）</h1>
<p class="note">左列为 LLM 输出 JSON、服务端 backend/agent/bidfig 确定性渲染的 SVG（良构性/版式由代码保证）；
右列为同一数据的 mermaid 源码按现行产品链路渲染（含既有 gantt 预处理：剥题注入刻度）。
关注点：刻度可读性、中文标签完整性、版式一致性、层结构与图例的可读性。</p>
<table>""" + "".join(rows) + """</table>
<script src="mermaid.min.js"></script>
<script>
mermaid.initialize({
  startOnLoad: true, theme: "default", securityLevel: "strict",
  fontFamily: '"Microsoft YaHei", "PingFang SC", Arial, sans-serif',
  flowchart: { useMaxWidth: false, curve: "basis" },
  gantt: { useMaxWidth: false, topAxis: true },
});
// 复刻产品 chartAssets.stripGanttBottomAxis：gantt 开 topAxis 后底轴仍在，
// 渲染后删除 translateY 较大的 g.grid，保证与线上呈现一致（时间轴只在上部）。
setTimeout(function () {
  document.querySelectorAll("svg").forEach(function (svg) {
    var grids = svg.querySelectorAll("g.grid");
    if (grids.length !== 2) return;
    var ys = [];
    grids.forEach(function (g) {
      var m = (g.getAttribute("transform") || "").match(/translate\\(\\s*[\\d.]+\\s*,\\s*([\\d.]+)\\s*\\)/);
      if (m) ys.push({ el: g, y: parseFloat(m[1]) });
    });
    if (ys.length === 2) {
      (ys[0].y > ys[1].y ? ys[0].el : ys[1].el).remove();
    }
  });
}, 600);
</script></body></html>"""
    page = OUT / "bidfig-compare.html"
    page.write_text(html, encoding="utf-8")
    print("html ->", page)

    png = OUT / "bidfig-compare.png"
    subprocess.run(
        [CHROME, "--headless=new", "--screenshot=" + str(png), "--window-size=1500,3400",
         "--hide-scrollbars", "--virtual-time-budget=8000", page.as_uri()],
        check=True, timeout=180,
    )
    print("png  ->", png, png.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
