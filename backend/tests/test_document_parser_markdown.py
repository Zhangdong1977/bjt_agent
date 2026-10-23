"""Markdown 素材解析（2026-10-23）：.md/.markdown 正文直读 + 编码回退。

素材上传放开 md 后，解析分支不做格式转换，核心风险点是编码（Windows 用户机
常见 GBK），这里覆盖直读函数的两条路径。
"""

from pathlib import Path

from backend.tasks.document_parser import _MARKDOWN_SUFFIXES, _read_markdown_text


def test_markdown_suffixes_cover_md_and_markdown():
    assert _MARKDOWN_SUFFIXES == {".md", ".markdown"}


def test_read_markdown_text_utf8(tmp_path: Path):
    file = tmp_path / "tech.md"
    file.write_text("# 技术方案\n\n正文", encoding="utf-8")
    assert _read_markdown_text(file) == "# 技术方案\n\n正文"


def test_read_markdown_text_gbk_fallback(tmp_path: Path):
    file = tmp_path / "legacy.md"
    file.write_bytes("# 中文标题\n\nGBK 编码正文".encode("gbk"))
    assert _read_markdown_text(file) == "# 中文标题\n\nGBK 编码正文"
