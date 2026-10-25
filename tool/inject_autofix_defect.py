# -*- coding: utf-8 -*-
"""质检自动修复闭环 E2E 验证：往最长叶子章节末尾注入机检可查缺陷。

缺陷注入到 >REVIEW_CHAPTER_MAX_CHARS(6000) 的章节末尾——Reader 分批只看前 6000 字
（content[:6000]），看不见缺陷；机检扫全文必然报 error → auto_fix 轮必然触发。

注入内容（两类 error）：
- 表格单元格「（请补充）」 → chart_placeholder
- 正文「详见 4.9.9 节」     → invalid_outline_ref（大纲无该编号）

用法：python tool/inject_autofix_defect.py --run autofix1
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent

# 缺陷必须落在章节 6,000 字（Reader 分批窗口 REVIEW_CHAPTER_MAX_CHARS）以外：
# 章节不够长时先垫一段良性参数表（不触发任何机检），把缺陷顶出 Reader 视野，
# 保证清零动作只能出自 auto_fix 轮而不是 Reader 修订。
# 垫表内容与目标章节主题（招标需求理解与逐条响应）同域，Reader 不会当成无关附录
DEFECT_TABLE_ROWS = [
    ("完全响应", "功能/指标可直接满足", "响应栏写明实现方式与对应章节号"),
    ("部分响应", "需结合采购人环境确认", "注明待确认项与需求确认阶段的固化动作"),
    ("承诺响应", "属建设期交付内容", "给出交付形态、阶段与时限锚点"),
    ("偏离项", "与招标要求存在差异", "逐项说明差异内容、原因与补偿措施"),
    ("引用响应", "由既有章节主述", "只写章节号引用、不重复展开"),
    ("条目编号", "沿用 R-01 至 R-29", "与招标文件需求编号一一对应、不重不漏"),
    ("条目粒度", "一条需求一条记录", "复合需求拆分为多行、主编号加后缀"),
    ("覆盖核查", "条目覆盖率 100%", "无对应章节的条目在大纲补章后再标响应"),
    ("变更联动", "招标澄清/补遗触发", "澄清文件发布后 1 个工作日内更新响应表"),
    ("版本标识", "响应表页脚标注版本与日期", "每次更新递增版本号并留档"),
    ("签署确认", "投标前由编制负责人复核", "复核记录随投标文件归档"),
    ("评审入口", "评标办法对应评分点", "每条响应可回溯到评分办法的得分点"),
]

DEFECT_PAD2_ROWS = [
    ("需求台账同步", "与响应表同源维护", "台账字段含编号、需求原文、响应口径、责任章节与状态"),
    ("状态流转", "待响应/已响应/已复核/已定稿", "状态变更留操作人与时间戳"),
    ("复核抽检", "编制负责人按 20% 抽检", "抽检不通过整章退回重编"),
    ("口径冲突处理", "以招标文件原文为准", "无法判定时按对采购人更严格者执行并留说明"),
    ("章节引用规则", "引用已有主述章节", "跨章机制一处主述、他处只写引用，不重复成文"),
    ("表格编号规则", "表 X-Y 按章编号", "X 为一级章号，编号与表题一致"),
    ("交付时间锚点", "统一采用合同签订（T0）后 X 日内", "引用招标绝对日期时同时写明相对周期口径"),
    ("占位符口径", "句内「（请补充）」仅限待投标人补充项", "表格单元格与图注不写占位"),
    ("响应表导出", "随技术标定稿一并导出", "导出版本与正文章节版本一致性核对"),
    ("历史版本留档", "每次定稿版本留存", "供评标澄清阶段快速回溯"),
    ("评标澄清响应", "澄清通知 2 小时内定位影响面", "24 小时内出具变更说明与修订稿"),
    ("废标项核对", "对照★条款清单逐项核对", "任何条目不得与★条款冲突"),
    ("商务/技术边界", "商务项不在本表响应", "报价与商务条款由商务标响应"),
    ("评分点映射", "响应条目关联评分办法条目", "便于评标专家按得分点索引"),
    ("完整性自检", "定稿前机检条目数与状态", "存在未定稿条目时禁止导出"),
    ("责任人标注", "每条目标注编制责任岗位", "编制、复核双签"),
    ("归档口径", "随投标文件电子档归档", "含响应表、台账与版本记录"),
    ("多包件口径", "按包件分别建表", "包件间共用需求引用主包件响应"),
    ("联合体口径", "牵头方统一汇总响应", "分工事项注明承担方与接口"),
    ("偏离汇总", "偏离项单列汇总表", "与逐条响应表页码互相索引"),
    ("需求优先级", "按招标文件标注优先级", "未标注时按★/重要/一般三级归类"),
    ("响应深度", "关键指标给数值与依据", "定性描述仅用于管理类需求"),
    ("技术依据引用", "引用我方方案章节与素材", "无素材支撑的不写「已实现」"),
    ("确认会议纪要", "需求确认阶段形成纪要", "纪要与响应表版本绑定"),
    ("模板库联动", "响应口径沉淀入模板库", "同类项目复用时人工复核更新"),
    ("评标索引页", "卷首附评分点索引页", "得分点-条目-页码三列对照"),
    ("澄清期锁定", "开标前 24 小时锁定版本", "锁定后仅允许按澄清文件修订"),
    ("异常回退", "修订引入新问题时回退上一版本", "回退动作双人复核并记录原因"),
]


def _defect_block() -> str:
    pad_rows = "\n".join(
        "| %s | %s | %s |" % row for row in DEFECT_TABLE_ROWS
    )
    pad2_rows = "\n".join(
        "| %s | %s | %s |" % row for row in DEFECT_PAD2_ROWS
    )
    pad = (
        "\n\n### 逐条响应口径基线（表2-2）\n\n"
        "| 响应口径 | 适用情形 | 响应栏写法 |\n| --- | --- | --- |\n%s\n" % pad_rows
        + "\n\n### 响应表维护与评标口径（表2-3）\n\n"
        "| 维护事项 | 口径 | 说明 |\n| --- | --- | --- |\n%s\n" % pad2_rows
    )
    defect = (
        "\n\n| 巡检项 | 频次 | 责任人 |\n| --- | --- | --- |\n"
        "| 数据备份核查 | 每日 | （请补充） |\n"
        "| 安全策略复核 | 每季度 | （请补充） |\n\n"
        "备份链路与容灾参数汇总详见 4.9.9 节的专门说明。\n"
    )
    return pad, defect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    args = parser.parse_args()

    state = json.loads((ROOT / "tool/e2e-reports" / args.run / "state.json").read_text("utf-8"))
    wid, tid = state["wizard_id"], state["write_task_id"]
    sections_dir = ROOT / "workspace" / "bid-wizard" / wid / "writing" / tid / "sections"
    files = sorted(sections_dir.glob("*.md"))
    if not files:
        raise SystemExit(f"no section files under {sections_dir}")

    best = max(files, key=lambda p: p.stat().st_size)
    original = best.read_text(encoding="utf-8")
    if "4.9.9" in original:
        raise SystemExit(f"{best.name} already references 4.9.9; abort")
    # 正文句内「（请补充）」合法（措辞铁律⑨），只有表格行/围栏内既有占位才算脏基线
    in_fence = False
    for line in original.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if (in_fence or line.lstrip().startswith("|")) and "（请补充）" in line:
            raise SystemExit(f"{best.name} already has chart-placeholder tokens; abort")

    pad, defect = _defect_block()
    content = original + pad + defect
    defect_offset = len(original) + len(pad)
    if defect_offset <= 6_010:
        raise SystemExit(
            f"defect would land at offset {defect_offset} (<=6010) - Reader window not exceeded; abort"
        )

    best.write_text(content, encoding="utf-8")
    print(f"INJECTED node={best.stem} file={best}")
    print(f"chars {len(original)} -> {len(content)}; defect at offset {defect_offset} (>6000 Reader window)")


if __name__ == "__main__":
    main()
