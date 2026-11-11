"""doc_search 连排图片可见性回归测试。

背景（2026-11-11 审查误报事故，doc/workspace/25 号）：生产投标书一行连排
28 张扫描件（image_73~image_100，约 2400 字符），旧实现对 LLM 双重隐藏：

1. 命中行 ``line_content`` 被 ``smart_truncate(..., 400)`` 截断，只露出前 4 张；
2. 渲染层 image_refs 只显示第 1 张 path，其余仅一行「(还有 N 张图片)」。

导致子 agent 断言「未附会计师事务所营业执照」（实际 image_97/98 就是执照
与执业证书）。本测试锁定修复行为：行内全部图片的 path 必须对 LLM 可见。
"""

import pytest

from backend.agent.tools.doc_search import DocSearchTool

# 复刻生产案例：alt 为无语义原始文件名（浏览器重复下载后缀），28 张连排
_RUN_FIRST, _RUN_LAST = 73, 100  # image_73 ~ image_100
_RUN_IMAGES = [
    (f"陕西中信泽建设工程有限公司25(1)_{i - _RUN_FIRST + 25}", f"_images/image_{i}.png")
    for i in range(_RUN_FIRST, _RUN_LAST + 1)
]
_RUN_LINE_PREFIX = "3.2025年财务审计报告附证明材料扫描件如下："


def _make_run_line() -> str:
    return _RUN_LINE_PREFIX + "".join(
        f"![{alt}]({src})" for alt, src in _RUN_IMAGES
    )


@pytest.fixture
def search_tool(tmp_path):
    """构造含 28 图连排行的投标书文档与搜索工具。"""
    lines = [
        "# 投标文件",
        "（八）财务状况证明材料",
        "本节提供近三年审计报告及事务所资质证明（财务审计报告）。",
        _make_run_line(),
        "（九）其他材料",
    ]
    doc_path = tmp_path / "bid.md"
    doc_path.write_text("\n".join(lines), encoding="utf-8")
    return DocSearchTool(
        tender_docs=[("招标书.md", str(tmp_path / "tender.md"))],
        bid_docs=[("投标书.md", str(doc_path))],
    )


class TestHitLineImageVisibility:
    """命中连排行本身时，28 张图全部可见。"""

    @pytest.mark.asyncio
    async def test_all_run_images_visible_in_render(self, search_tool):
        """渲染输出必须列出全部 28 张图片的 path，不再出现「还有N张」省略。"""
        result = await search_tool.execute(文档类型="bid", query="扫描件")

        assert result.success is True
        for _, src in _RUN_IMAGES:
            assert src in result.content, f"图片 {src} 未在渲染输出中出现"
        assert "还有" not in result.content
        assert f"共 {len(_RUN_IMAGES)} 张" in result.content

    @pytest.mark.asyncio
    async def test_hidden_images_summarized_in_line_content(self, search_tool):
        """命中行文本被截断时，末尾必须补全被隐藏图片的清单。"""
        result = await search_tool.execute(文档类型="bid", query="扫描件")

        assert result.success is True
        line_content = result.data["results"][0]["line_content"]
        # 行前缀可见，但 400 截断必然隐藏靠后的图 → 摘要必须补全
        assert _RUN_LINE_PREFIX.strip("：") in line_content
        assert f"共 {len(_RUN_IMAGES)} 张图片" in line_content
        for _, src in _RUN_IMAGES[4:]:  # 第 5 张起为截断隐藏对象
            assert src in line_content, f"图片 {src} 未进入截断摘要"

    @pytest.mark.asyncio
    async def test_alt_text_displayed_for_meaningful_alt(self, tmp_path):
        """有语义 alt 时渲染层应展示 alt（截长到 30 字符）。"""
        doc = tmp_path / "b2.md"
        doc.write_text(
            "资质证明 ![营业执照扫描件](_images/license.png)\n", encoding="utf-8"
        )
        tool = DocSearchTool(tender_docs=[], bid_docs=[("b2.md", str(doc))])

        result = await tool.execute(文档类型="bid", query="资质")

        assert result.success is True
        assert "_images/license.png" in result.content
        assert "营业执照扫描件" in result.content

    @pytest.mark.asyncio
    async def test_short_image_line_no_summary_tail(self, tmp_path):
        """短行（截断未隐藏图片）不应出现摘要尾巴——老行为保持不变。"""
        doc = tmp_path / "b3.md"
        doc.write_text(
            "证书页 ![证书第一页](_images/p1.png) ![证书第二页](_images/p2.png)\n",
            encoding="utf-8",
        )
        tool = DocSearchTool(tender_docs=[], bid_docs=[("b3.md", str(doc))])

        result = await tool.execute(文档类型="bid", query="证书页")

        assert result.success is True
        line_content = result.data["results"][0]["line_content"]
        assert "因文本截断未显示" not in line_content
        assert "_images/p1.png" in result.content
        assert "_images/p2.png" in result.content


class TestNearbyLineImageVisibility:
    """命中行无图、连排行在邻近窗口内时，全部图片可见且标注行号。"""

    @pytest.mark.asyncio
    async def test_nearby_run_images_visible_with_line_number(self, search_tool):
        """搜索「财务审计报告」命中第 3 行（无图），第 4 行的 28 张图应全部可见。"""
        result = await search_tool.execute(文档类型="bid", query="财务审计报告")

        assert result.success is True
        for _, src in _RUN_IMAGES:
            assert src in result.content, f"邻近图片 {src} 未在渲染输出中出现"
        # 邻近图片须标注来源行号，LLM 才能定位
        assert "位于第4行" in result.content

    @pytest.mark.asyncio
    async def test_context_line_exposes_hidden_images(self, search_tool):
        """上下文行（默认窗口5行）内的连排行截断后，隐藏图片也必须补全。"""
        result = await search_tool.execute(文档类型="bid", query="财务审计报告")

        assert result.success is True
        context_after = result.data["results"][0]["context_after"]
        assert f"共 {len(_RUN_IMAGES)} 张图片" in context_after
        assert "_images/image_100.png" in context_after  # 连排行最后一张


class TestSingleImageRegression:
    """单图老场景不回归。"""

    @pytest.mark.asyncio
    async def test_single_image_line(self, tmp_path):
        doc = tmp_path / "b4.md"
        doc.write_text(
            "营业执照页 ![营业执照](_images/yyzz.png)\n", encoding="utf-8"
        )
        tool = DocSearchTool(tender_docs=[], bid_docs=[("b4.md", str(doc))])

        result = await tool.execute(文档类型="bid", query="营业执照页")

        assert result.success is True
        assert "_images/yyzz.png" in result.content
        assert "共 1 张" in result.content
