"""Markdown 图片引用含半角括号路径的回归测试。

文件名带 "(1)""(2)" 等括号后缀时，图片引用路径会包含成对括号；旧的
``([^)]+)`` 在路径内第一个 ``)`` 处截断 src，导致证据块图片哈希缺失、
段落文本残留路径碎片。见 backend/utils/markdown_images.py。
"""

import hashlib
from pathlib import Path

from backend.agent.tools import doc_search, structure_tools
from backend.services import duplicate_candidates
from backend.services.document_artifacts import build_evidence_blocks
from backend.tasks.document_parser import _embed_image_descriptions_in_md
from backend.utils.markdown_images import IMAGE_REF_RE

PAREN_DIR = "好猫重庆项目10.20(3)(2)_20261015231142_images"


def test_image_ref_re_captures_path_with_balanced_parens():
    md = f"![证书]({PAREN_DIR}/image_1.png)"
    match = IMAGE_REF_RE.search(md)
    assert match is not None
    assert match.group(1) == "证书"
    assert match.group(2) == f"{PAREN_DIR}/image_1.png"


def test_image_ref_re_handles_multiple_refs_on_one_line():
    md = f"![a](x.png)中间文字![b]({PAREN_DIR}/image_2.jpeg)"
    refs = [(m.group(1), m.group(2)) for m in IMAGE_REF_RE.finditer(md)]
    assert refs == [("a", "x.png"), ("b", f"{PAREN_DIR}/image_2.jpeg")]


def test_image_ref_re_falls_back_to_legacy_truncation_for_unbalanced_parens():
    md = "![a](file(1.png)"
    match = IMAGE_REF_RE.search(md)
    assert match is not None
    assert match.group(2) == "file(1.png"


def test_image_ref_re_sub_removes_whole_reference():
    md = f"证明材料：![证书]({PAREN_DIR}/image_1.png)（详见附件）"
    assert IMAGE_REF_RE.sub("", md) == "证明材料：（详见附件）"


def test_all_consumers_share_the_fixed_pattern():
    assert structure_tools._IMAGE_REF_RE is IMAGE_REF_RE
    assert doc_search.IMAGE_REF_RE is IMAGE_REF_RE


def test_build_evidence_blocks_hashes_images_behind_paren_dirs(tmp_path: Path):
    images_dir = tmp_path / PAREN_DIR
    images_dir.mkdir()
    payload = b"fake-image-bytes"
    (images_dir / "image_1.png").write_bytes(payload)

    markdown = (
        "# 业绩证明\n\n"
        f"证明材料如下：![证书]({PAREN_DIR}/image_1.png)\n"
    )
    blocks = build_evidence_blocks(
        markdown,
        document_id="doc-paren",
        document_role="bid",
        images_dir=images_dir,
        source_path=tmp_path / "好猫重庆项目10.20(3)(2).docx",
    )

    paragraphs = [b for b in blocks if b.content_type == "paragraph"]
    images = [b for b in blocks if b.content_type == "image"]

    assert len(images) == 1
    assert images[0].image_sha256 == hashlib.sha256(payload).hexdigest()
    assert images[0].image_path == "image_1.png"
    # 段落文本不得残留被截断的路径碎片
    assert paragraphs and all("_images/" not in (p.raw_text or "") for p in paragraphs)
    assert paragraphs[0].raw_text == "证明材料如下："


def test_embed_image_descriptions_matches_paren_paths():
    md = "![证书](投标文件(1)_images/image_2.png)\n"
    result = _embed_image_descriptions_in_md(md, {"image_2.png": "某公司营业执照"})
    assert "图片内容: 某公司营业执照" in result
    # 原引用行保持原样
    assert "![证书](投标文件(1)_images/image_2.png)" in result


def test_duplicate_candidates_strips_paren_image_refs():
    raw = f"质保期 3 年 ![图]({PAREN_DIR}/image_1.png) 详见附件"
    stripped = duplicate_candidates._MARKDOWN_IMAGE_RE.sub("", raw)
    assert stripped == "质保期 3 年  详见附件"


# ---- alt 含半角方括号（2026-10-21 发现，存量 42 份中招）----
#
# alt 来自原文档 wp:docPr/@descr，转换器现按 CommonMark 反斜杠转义后产出；
# IMAGE_REF_RE 的 alt 模式必须容忍 ``\]`` 等转义序列，否则 alt 的 ``]``
# 处截断、把 alt 片段误当 src（证据哈希缺/agent 取图失败/预览改写错位）。


def test_image_ref_re_tolerates_escaped_bracket_alt():
    md = f"证明材料：![证书\\[批注\\](第3版)]({PAREN_DIR}/image_1.png)"
    match = IMAGE_REF_RE.search(md)
    assert match is not None
    assert match.group(2) == f"{PAREN_DIR}/image_1.png"
    assert "批注" in match.group(1)


def test_image_ref_re_sub_removes_ref_with_escaped_alt():
    md = f"前文 ![证书\\[批注\\]]({PAREN_DIR}/image_1.png) 后文"
    assert IMAGE_REF_RE.sub("", md) == "前文  后文"


def test_image_ref_re_multiple_refs_with_escaped_alt_stay_separate():
    md = f"![a\\[1\\]](x.png) 文本 ![b](y.png)"
    refs = [(m.group(1), m.group(2)) for m in IMAGE_REF_RE.finditer(md)]
    assert refs == [("a\\[1\\]", "x.png"), ("b", "y.png")]


def test_image_ref_re_legacy_unescaped_bracket_still_truncates():
    """历史产物（未转义 alt）保持旧截断语义：只保证新产物正确，不猜歧义。"""
    md = "![证书[批注](第3版)](dir/image_1.png)"
    match = IMAGE_REF_RE.search(md)
    assert match is not None
    # 与 2026-10-21 实测的旧行为一致：alt 在首个 ] 处截断、src 误取 "第3版"
    assert match.group(1) == "证书[批注"
    assert match.group(2) == "第3版"
