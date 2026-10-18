# -*- coding: utf-8 -*-
"""demo_bidfig_p4.py — P4 扩类原语（flow 流程图 / seq 时序图）样张页。

用途：新原语的视觉验收材料（render_spec 出稿 → Chrome 截图 → visual-judge）。
覆盖：典型场景（3-4 阶段实施流程、3 参与方交互）+ 边界场景（5 列长名、
6 参与方密集消息、无阶段单列、自交互回环）。
运行：python scripts/demo_bidfig_p4.py
产物：scripts/demo-out/bidfig-p4.html + bidfig-p4.png
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.bidfig import render_spec  # noqa: E402

OUT = ROOT / "scripts" / "demo-out"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

FLOW_IMPL = {
    "kind": "flow", "title": "项目实施与交付流程",
    "phases": [
        {"name": "启动准备", "steps": [
            {"name": "项目组组建与进场", "note": "中标后 3 日内"},
            {"name": "需求调研与确认", "note": "输出需求规格书"},
        ]},
        {"name": "建设实施", "steps": [
            {"name": "环境准备与标品部署"},
            {"name": "系统对接联调", "note": "OA/统一身份认证"},
            {"name": "定制开发与测试"},
        ]},
        {"name": "试运行", "steps": [
            {"name": "试运行与性能调优"},
            {"name": "用户培训", "note": "分角色开展"},
        ]},
        {"name": "验收移交", "steps": [
            {"name": "初验"},
            {"name": "终验与文档移交"},
        ]},
    ],
}

FLOW_SINGLE = {
    "kind": "flow", "title": "故障受理与处理流程",
    "steps": [
        {"name": "受理报修", "note": "7×24 热线，15 分钟内响应"},
        {"name": "故障分级", "note": "一级/二级/三级"},
        {"name": "分级派单与处理"},
        {"name": "处理结果确认", "note": "客户确认后方可关闭"},
        {"name": "回访与归档"},
    ],
}

FLOW_STRESS = {
    "kind": "flow", "title": "跨部门协同流程（长名称边界场景）",
    "phases": [
        {"name": "需求管理", "steps": [
            {"name": "业务部门需求提报与初审", "note": "按季度汇总形成需求清单并组织评审会"},
        ]},
        {"name": "方案设计", "steps": [
            {"name": "总体方案与概算编制", "note": "联合设计单位完成可研与初设"},
            {"name": "方案评审与批复"},
        ]},
        {"name": "采购执行", "steps": [{"name": "招标与合同签订"}]},
        {"name": "建设交付", "steps": [{"name": "实施与初验"}, {"name": "决算与归档"}]},
        {"name": "运营维护", "steps": [{"name": "运维与后评价"}]},
    ],
}

SEQ_INTEGRATION = {
    "kind": "seq", "title": "投标文件编制系统对接交互时序",
    "actors": ["投标人客户端", "标书生成平台", "AI 能力服务"],
    "messages": [
        {"from": 0, "to": 1, "text": "上传招标文件"},
        {"from": 1, "to": 2, "text": "发起解析与要素提取"},
        {"from": 2, "to": 1, "text": "返回结构化要素", "dash": True},
        {"from": 1, "to": 2, "text": "逐节生成请求"},
        {"from": 2, "to": 2, "text": "内容合规自检"},
        {"from": 1, "to": 0, "text": "推送成稿与修订建议", "dash": True},
    ],
}

SEQ_SIX = {
    "kind": "seq", "title": "评标过程信息交互（6 参与方边界场景）",
    "actors": ["招标人", "代理机构", "评标委员会", "监督人员", "电子交易平台", "投标人"],
    "messages": [
        {"from": 0, "to": 1, "text": "发布招标公告"},
        {"from": 5, "to": 4, "text": "递交投标文件"},
        {"from": 1, "to": 4, "text": "开标签到与解密指令"},
        {"from": 4, "to": 2, "text": "推送投标文件"},
        {"from": 2, "to": 2, "text": "独立评审打分"},
        {"from": 3, "to": 4, "text": "全程监督记录", "dash": True},
        {"from": 2, "to": 1, "text": "提交评标报告", "dash": True},
    ],
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    cases = [
        ("flow · 4 阶段实施与交付流程（典型）", FLOW_IMPL),
        ("flow · 无阶段单列应急流程（典型）", FLOW_SINGLE),
        ("flow · 5 列长名称（边界）", FLOW_STRESS),
        ("seq · 3 参与方系统对接（含自交互/虚线返回）", SEQ_INTEGRATION),
        ("seq · 6 参与方评标交互（边界）", SEQ_SIX),
    ]
    blocks = []
    for heading, spec in cases:
        svg = render_spec(spec)
        blocks.append('<div class="case">%s</div><div class="fig">%s</div>' % (heading, svg))

    html = """<!doctype html><html><head><meta charset="utf-8">
<style>
body{font-family:"Microsoft YaHei",Arial,sans-serif;margin:16px;background:#fff}
h1{font-size:18px}
.case{background:#1f2937;color:#fff;font-weight:700;padding:8px 12px;font-size:14px;margin-top:18px}
.fig{border:1px solid #ddd;padding:10px}
svg{max-width:100%;height:auto}
</style></head><body>
<h1>bidfig P4 扩类原语样张 — flow 流程图 / seq 时序图（2026-10-18）</h1>
""" + "".join(blocks) + "</body></html>"
    page = OUT / "bidfig-p4.html"
    page.write_text(html, encoding="utf-8")
    print("html ->", page)

    png = OUT / "bidfig-p4.png"
    subprocess.run(
        [CHROME, "--headless=new", "--screenshot=" + str(png), "--window-size=1200,4200",
         "--hide-scrollbars", "--virtual-time-budget=8000", page.as_uri()],
        check=True, timeout=180,
    )
    print("png  ->", png, png.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
