"""Tests for embedding image descriptions into markdown."""

import pytest
from backend.tasks.document_parser import _embed_image_descriptions_in_md


class TestEmbedImageDescriptions:
    """Test _embed_image_descriptions_in_md function."""

    def test_single_image_withdescription(self):
        """Single image with description should embed description below."""
        md = "Some text\n![image](test_images/photo.png)\nMore text"
        desc_map = {"photo.png": "A photo of a sunset"}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert result == (
            "Some text\n"
            "![image](test_images/photo.png)\n"
            "图片内容: A photo of a sunset\n"
            "More text"
        )

    def test_multiple_images_with_descriptions(self):
        """Multiple images each get their description below."""
        md = "Start\n![image](img1.png)\nMiddle\n![image](img2.jpg)\nEnd"
        desc_map = {
            "img1.png": "First image description",
            "img2.jpg": "Second image description",
        }

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert "![image](img1.png)\n图片内容: First image description" in result
        assert "![image](img2.jpg)\n图片内容: Second image description" in result

    def test_image_without_description_unchanged(self):
        """Image without matching description keeps original format."""
        md = "Text\n![image](unknown.png)\nMore"
        desc_map = {"other.png": "Some description"}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert result == "Text\n![image](unknown.png)\nMore"

    def test_empty_description_map(self):
        """Empty description map keeps all images unchanged."""
        md = "![image](photo.png)\n![image](diagram.gif)"
        desc_map = {}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert result == md

    def test_partial_descriptions(self):
        """Some images with descriptions, some without."""
        md = "![image](a.png)\n![image](b.png)\n![image](c.png)"
        desc_map = {
            "a.png": "Description A",
            "c.png": "Description C",
        }

        result = _embed_image_descriptions_in_md(md, desc_map)

        lines = result.split("\n")
        # a.png should have description
        a_idx = next(i for i, l in enumerate(lines) if "![image](a.png)" in l)
        assert lines[a_idx + 1] == "图片内容: Description A"
        # b.png should not have description (next line should be next image link)
        b_idx = next(i for i, l in enumerate(lines) if "![image](b.png)" in l)
        assert "![image](c.png)" in lines[b_idx + 1]  # Next line is c.png, not empty
        # c.png should have description
        c_idx = next(i for i, l in enumerate(lines) if "![image](c.png)" in l)
        assert lines[c_idx + 1] == "图片内容: Description C"

    def test_filename_extraction_from_path(self):
        """Extracts filename from full path for matching."""
        md = "![image](RTCMS技术规范书_20260404115542_images/page_1.png)"
        desc_map = {"page_1.png": "Page one content"}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert "图片内容: Page one content" in result

    def test_description_with_special_characters(self):
        """Descriptions with special characters are preserved."""
        md = "![image](x.png)"
        desc_map = {"x.png": "图表展示了 MCU/SFU 双模式工作流程 (含 NACK/FEC 机制)"}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert "图片内容: 图表展示了 MCU/SFU 双模式工作流程 (含 NACK/FEC 机制)" in result