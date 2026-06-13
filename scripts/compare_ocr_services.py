#!/usr/bin/env python3
"""对比百度云 OCR 与 MiniMax MCP understand_image 在真实标书/应标书图片上的效果。

用项目真实文档图片（招标书表格、应标书身份证）分别调用两个服务，采集
成功/失败、错误码（重点 1026 敏感内容）、输出文本、耗时，并按"关键 token
命中率"给出客观准确度信号，产出 docs/ocr_comparison_report.md 供人工评估
"百度 OCR 是否可替代/设为默认"。

用法:
    conda activate py311
    python scripts/compare_ocr_services.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 保证 backend / mini_agent 可导入
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Mini-Agent"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

from backend.config import get_settings  # noqa: E402
from backend.agent.tools.baidu_ocr import BaiduOcrTool  # noqa: E402

# 注入 MINIMAX 环境变量供 MCP ${VAR} 展开
_settings = get_settings()
os.environ.setdefault("MINIMAX_API_KEY", _settings.minimax_api_key)
os.environ.setdefault("MINIMAX_API_HOST", _settings.minimax_api_host)

# ---------------------------------------------------------------------------
# 测试图片与已知真值（用于 token 命中率核对）
# ---------------------------------------------------------------------------

_TENDER_IMG = (
    ROOT
    / "workspace/31ade8e4-91f8-45f2-8414-effbfbffe8b6/01e0ab24-97da-484f-9b41-f04ae37718a7/tender"
    / "河南省人影水资源保障项目人工影响天气作业音视频调度系统招标项目（定稿）_20260525190446_images"
    / "image_8.jpeg"
)
_BID_IMG = (
    ROOT
    / "workspace/31ade8e4-91f8-45f2-8414-effbfbffe8b6/3f782b66-ece9-4c45-9f34-2f01c1c8b244/bid"
    / "迪维勒普投标文件_20260525154320_images"
    / "image_1.jpeg"
)

IMAGES = [
    {
        "label": "招标书表格 image_8.jpeg",
        "path": _TENDER_IMG,
        "prompts": ["提取表格中所有产品类别编码与对应的环保标准号"],
        "key_tokens": [
            "A090101", "A100203", "A100307", "A100309",
            "A10020301", "A10020302", "A10020303",
            "HJ410", "HJ571", "HJ/T297", "HJ455",
            "复印纸", "人造板", "建筑陶瓷制品", "建筑防水卷材",
        ],
    },
    {
        "label": "应标书身份证 image_1.jpeg",
        "path": _BID_IMG,
        # 第2条 prompt 含「身份证」字样，用于验证 MiniMax 是否触发 1026
        "prompts": ["识别证件上的文字信息", "识别这张身份证的信息"],
        "key_tokens": [
            "岳志军", "410105197704282751", "河南省中牟县",
            "大孟镇", "草场村", "1977", "汉",
        ],
    },
]

REPORT_PATH = ROOT / "docs" / "ocr_comparison_report.md"


# ---------------------------------------------------------------------------
# 调用封装
# ---------------------------------------------------------------------------


async def run_baidu(prompt: str, image_path: Path) -> dict:
    tool = BaiduOcrTool()
    t0 = time.perf_counter()
    res = await tool.execute(prompt=prompt, image_source=str(image_path))
    dt = time.perf_counter() - t0
    return {
        "success": res.success,
        "content": res.content or "",
        "error": res.error or "",
        "elapsed": dt,
        "is_1026": False,
    }


async def run_minimax(prompt: str, image_path: Path, tool) -> dict:
    if tool is None:
        return {"success": False, "content": "", "error": "(MiniMax 未加载)", "elapsed": 0.0, "is_1026": False}
    t0 = time.perf_counter()
    try:
        res = await tool.execute(prompt=prompt, image_source=str(image_path))
        err = res.error or ""
        content = res.content or ""
        # MiniMax MCP 会把 1026 敏感内容错误放进 content（success 仍为 True），故两者都查
        blob = f"{err} {content}"
        return {
            "success": res.success,
            "content": content,
            "error": err,
            "elapsed": time.perf_counter() - t0,
            "is_1026": "1026" in blob,
        }
    except Exception as e:
        msg = f"{e}"
        return {
            "success": False,
            "content": "",
            "error": msg,
            "elapsed": time.perf_counter() - t0,
            "is_1026": "1026" in msg,
        }


def token_hits(content: str, tokens: list[str]) -> tuple[list[str], int, int]:
    found = [t for t in tokens if t in (content or "")]
    return found, len(found), len(tokens)


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def _truncate(text: str, n: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + f"\n…(已截断，共 {len(text)} 字符)"


def render_report(rows: list[dict], minimax_load_error: str | None) -> str:
    lines: list[str] = []
    lines.append("# 百度云 OCR vs MiniMax MCP 图像理解 对比评估报告\n")
    lines.append("> 自动生成自 `scripts/compare_ocr_services.py`。百度 OCR 为纯文字识别；"
                 "MiniMax `understand_image` 为 VLM 语义理解。\n")

    if minimax_load_error:
        lines.append(f"> ⚠️ MiniMax MCP 加载失败，以下 MiniMax 列为空：`{minimax_load_error}`\n")

    # 汇总表
    lines.append("## 汇总\n")
    lines.append("| 图片 | Prompt | 服务 | 成功 | 关键 token 命中 | 耗时(s) | 1026 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        for svc_key, svc_name in (("minimax", "MiniMax"), ("baidu", "百度OCR")):
            d = r[svc_key]
            found, hit, total = token_hits(d["content"], r["key_tokens"])
            ok = "✅" if d["success"] else "❌"
            hit_str = f"{hit}/{total}"
            is1026 = "⚠️是" if d["is_1026"] else "—"
            prompt_short = (r["prompt"][:18] + "…") if len(r["prompt"]) > 18 else r["prompt"]
            lines.append(
                f"| {r['label']} | {prompt_short} | {svc_name} | {ok} | {hit_str} | {d['elapsed']:.1f} | {is1026} |"
            )

    # 逐图详情
    lines.append("\n## 详情\n")
    for r in rows:
        lines.append(f"### {r['label']}")
        lines.append(f"- 文件：`{r['path']}`\n")
        for svc_key, svc_name in (("minimax", "MiniMax MCP (VLM)"), ("baidu", "百度 OCR (accurate_basic)")):
            d = r[svc_key]
            found, hit, total = token_hits(d["content"], r["key_tokens"])
            lines.append(f"#### {svc_name}  ·  prompt=`{r['prompt']}`")
            lines.append(f"- 成功：{d['success']}  |  耗时：{d['elapsed']:.2f}s  |  1026：{d['is_1026']}")
            lines.append(f"- 关键 token 命中 {hit}/{total}：{', '.join(found) if found else '（无）'}")
            if not d["success"]:
                lines.append(f"- ❌ 错误：{d['error']}")
            else:
                lines.append("- 输出：")
                lines.append("```text")
                lines.append(_truncate(d["content"]))
                lines.append("```")
            lines.append("")
        lines.append("---\n")

    lines.append("## 结论建议\n")
    lines.append("- 百度 OCR 为纯文字识别，在「精确编码/字段提取」与「无敏感内容拦截」上预期优于 MiniMax。")
    lines.append("- MiniMax 为 VLM，在「语义解释（表格含义/证件类型/水印）」上仍有优势。")
    lines.append("- 重点核对：① 表格编码 `A10020301`/`HJ/T297` 是否一字不差；"
                 "② 身份证号 `410105197704282751` 是否精确；"
                 "③ MiniMax 在「身份证」prompt 下是否触发 1026（百度应稳定通过）。")
    lines.append("- 若百度在完整性/准确性/可靠性达标，可设 `IMAGE_UNDERSTANDING_PROVIDER=baidu` 为默认；"
                 "否则保留 `minimax`，按需手动切换。")
    return "\n".join(lines) + "\n"


def print_summary(rows: list[dict]) -> None:
    print("\n" + "=" * 70)
    print("对比摘要")
    print("=" * 70)
    for r in rows:
        print(f"\n[{r['label']}] prompt={r['prompt']!r}")
        for svc_key, svc_name in (("minimax", "MiniMax"), ("baidu", "百度OCR")):
            d = r[svc_key]
            _, hit, total = token_hits(d["content"], r["key_tokens"])
            tag = "OK" if d["success"] else f"FAIL({d['is_1026'] and '1026' or d['error'][:30]})"
            print(f"  - {svc_name:8s} {tag:34s} 命中 {hit}/{total}  {d['elapsed']:.1f}s")
    print()


# ---------------------------------------------------------------------------


async def main() -> None:
    minimax_tool = None
    minimax_load_error: str | None = None
    cleanup = None
    try:
        from mini_agent.tools.mcp_loader import (
            cleanup_mcp_connections,
            load_mcp_tools_async,
        )

        mcp_cfg = ROOT / "backend" / "mcp.json"
        tools = await load_mcp_tools_async(str(mcp_cfg))
        cleanup = cleanup_mcp_connections
        tmap = {t.name: t for t in tools}
        minimax_tool = tmap.get("understand_image")
        if minimax_tool is None:
            minimax_load_error = f"understand_image 不在 MCP 工具中: {list(tmap)}"
        else:
            print(f"[INFO] MiniMax MCP understand_image 已加载（工具集：{list(tmap)}）")
    except Exception as e:
        minimax_load_error = f"{type(e).__name__}: {e}"
        print(f"[WARN] MiniMax MCP 加载失败：{minimax_load_error}")

    rows: list[dict] = []
    for img in IMAGES:
        if not img["path"].exists():
            print(f"[WARN] 图片不存在，跳过：{img['path']}")
            continue
        for prompt in img["prompts"]:
            print(f"[RUN] {img['label']} | prompt={prompt!r}")
            baidu = await run_baidu(prompt, img["path"])
            print(f"        百度OCR: success={baidu['success']} hit-? {baidu['elapsed']:.1f}s")
            minimax = await run_minimax(prompt, img["path"], minimax_tool)
            print(
                f"        MiniMax: success={minimax['success']} 1026={minimax['is_1026']} "
                f"{minimax['elapsed']:.1f}s"
            )
            rows.append({**img, "prompt": prompt, "baidu": baidu, "minimax": minimax})

    if cleanup is not None:
        try:
            await cleanup()
        except Exception:
            pass

    if not rows:
        print("[ERROR] 无可评估结果（图片均不存在？）")
        return

    report = render_report(rows, minimax_load_error)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n[OK] 报告已写入：{REPORT_PATH}")
    print_summary(rows)


if __name__ == "__main__":
    asyncio.run(main())
