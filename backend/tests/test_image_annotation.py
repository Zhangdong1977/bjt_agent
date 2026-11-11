"""图片预识别（doc 25 号方案一）单测：词表打标 / store / 工具输出注入。

词表用例取自 2026-11-11 生产事故标书的真实 OCR 文本形态
（image_97=北京衡川营业执照、image_98=执业证书、审计报告首页等）。
"""

import pytest

from backend.agent.tools.doc_search import DocSearchTool
from backend.services.image_annotation import (
    classify_ocr_text,
    clear_annotation_store,
    format_annotation_display,
    get_annotation_display,
    set_doc_annotations,
)


class TestClassifyOcrText:
    """词表打标——用例取自生产事故标书的真实图片形态。"""

    def test_firm_business_license(self):
        """image_97 形态：事务所营业执照（事务所名+营业执照同页）。"""
        text = "北京衡川会计师事务所（普通合伙）\n营业执照（副本）\n统一社会信用代码 91110108MAC3UHXE6X\n名称：北京衡川会计师事务所（普通合伙）"
        label, summary = classify_ocr_text(text)
        assert label == "会计师事务所营业执照"
        assert "北京衡川" in summary

    def test_firm_practice_certificate(self):
        """image_98 形态：会计师事务所执业证书。"""
        text = "会计师事务所执业证书\n京财会许可[2023]0017号"
        label, _ = classify_ocr_text(text)
        assert label == "会计师事务所执业证书"

    def test_bidder_business_license_not_firm(self):
        """投标人自己的营业执照不撞事务所组合标签。"""
        text = "营业执照\n统一社会信用代码 91610302MA6XA3P697\n名称：陕西中信泽建设工程有限公司\n经营范围：建筑工程施工总承包"
        label, _ = classify_ocr_text(text)
        assert label == "营业执照"

    def test_audit_report_cover_beats_firm_fallback(self):
        """审计报告首页（事务所名+审计报告同页）应命中「审计报告」，
        而非排最后的「会计师事务所文件」兜底。"""
        text = "审计报告\n北京中经宏伟会计师事务所有限责任公司\n我们认为，后附的财务报表在所有重大方面公允反映"
        label, _ = classify_ocr_text(text)
        assert label == "审计报告"

    def test_firm_name_alone_falls_back(self):
        """仅事务所名无其他特征词 → 兜底标签。"""
        label, _ = classify_ocr_text("北京衡川会计师事务所（普通合伙）关于决算报表的专项说明正文若干文字")
        assert label == "会计师事务所文件"

    def test_financial_statement_page(self):
        label, _ = classify_ocr_text("资产负债表\n编制单位：陕西中信泽建设工程有限公司\n2025年12月31日")
        assert label == "财务报表"

    def test_regulator_screenshot(self):
        label, _ = classify_ocr_text("注册会计师行业统一监管平台\n审计报告查验结果\n报告编码：京26P00DY295")
        assert label == "查询/公示截图"

    def test_empty_text(self):
        assert classify_ocr_text(None) == (None, None)
        assert classify_ocr_text("") == (None, None)
        assert classify_ocr_text("\n \n") == (None, None)

    def test_summary_skips_numeric_lines(self):
        """摘要跳过纯数字/极短行，取首个有信息量行。"""
        text = "2025\n12,345.67\n（以下无正文）附注说明部分\n北京衡川"
        _, summary = classify_ocr_text(text)
        assert "附注" in summary

    def test_seal_section_stripped(self):
        """印章小节不参与打标：正文只有「营业执照」，若 strip 失效会被
        印章小节里的「会计师事务所」带偏到兜底标签。"""
        text = "营业执照\n【印章检测】\n检测到圆形章：北京衡川会计师事务所"
        label, _ = classify_ocr_text(text)
        assert label == "营业执照"

    def test_display_format(self):
        assert format_annotation_display("营业执照", "陕西中信泽") == "营业执照（陕西中信泽）"
        assert format_annotation_display(None, "某文件首页") == "某文件首页"
        assert format_annotation_display(None, None) == ""


class TestAnnotationStore:
    """任务内 store：文档隔离 / 未标注返回空串 / 清空。"""

    def setup_method(self):
        clear_annotation_store()

    def teardown_method(self):
        clear_annotation_store()

    def test_set_get_isolated_by_doc(self):
        set_doc_annotations("bid.docx", {"image_97.png": "会计师事务所营业执照（北京衡川…）"})
        set_doc_annotations("tender.docx", {"image_1.png": "招标公告（某部车场…）"})

        assert get_annotation_display("bid.docx", "image_97.png") == "会计师事务所营业执照（北京衡川…）"
        # 同名图片在另一文档下不串扰
        assert get_annotation_display("tender.docx", "image_97.png") == ""
        assert get_annotation_display("bid.docx", "image_1.png") == ""
        assert get_annotation_display(None, "image_97.png") == ""

    def test_clear(self):
        set_doc_annotations("bid.docx", {"image_97.png": "x"})
        clear_annotation_store()
        assert get_annotation_display("bid.docx", "image_97.png") == ""


class TestDocSearchAnnotationInjection:
    """doc_search 渲染层注入预识别标签。"""

    def setup_method(self):
        clear_annotation_store()

    def teardown_method(self):
        clear_annotation_store()

    @pytest.mark.asyncio
    async def test_render_contains_annotation(self, tmp_path):
        doc = tmp_path / "bid.md"
        doc.write_text(
            "资质材料 ![扫描件](_images/image_97.png)\n", encoding="utf-8"
        )
        set_doc_annotations(
            "bid.md",
            {"image_97.png": "会计师事务所营业执照（北京衡川…）"},
        )
        tool = DocSearchTool(tender_docs=[], bid_docs=[("bid.md", str(doc))])

        result = await tool.execute(文档类型="bid", query="资质材料")

        assert result.success is True
        assert "〔会计师事务所营业执照（北京衡川…）〕" in result.content

    @pytest.mark.asyncio
    async def test_no_annotation_no_marker(self, tmp_path):
        """无标注时维持既有输出形态（无 〔〕 标记）。"""
        doc = tmp_path / "bid.md"
        doc.write_text("资质材料 ![扫描件](_images/image_97.png)\n", encoding="utf-8")
        tool = DocSearchTool(tender_docs=[], bid_docs=[("bid.md", str(doc))])

        result = await tool.execute(文档类型="bid", query="资质材料")

        assert result.success is True
        assert "〔" not in result.content
        assert "_images/image_97.png" in result.content
