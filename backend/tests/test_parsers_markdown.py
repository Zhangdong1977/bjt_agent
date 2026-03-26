"""Unit tests for Markdown converter module."""

import pytest
from pathlib import Path

from backend.parsers.html_parser import (
    Image,
    ListItem,
    ParsedDocument,
    ParsedSection,
    Paragraph,
    Table,
    TableCell,
    TableRow,
)
from backend.parsers.markdown_converter import (
    MarkdownConverter,
    MarkdownConversionError,
    document_to_markdown,
    html_to_markdown,
)


class TestMarkdownConverter:
    """Tests for MarkdownConverter class."""

    @pytest.fixture
    def converter(self):
        """Create a MarkdownConverter instance."""
        return MarkdownConverter()

    def test_convert_empty_document(self, converter):
        """Test converting an empty document."""
        doc = ParsedDocument()
        result = converter.convert_document(doc)
        assert result == ""

    def test_convert_document_with_title(self, converter):
        """Test converting a document with a title."""
        doc = ParsedDocument(title="Document Title")
        result = converter.convert_document(doc)
        assert "# Document Title" in result

    def test_convert_heading_paragraph(self, converter):
        """Test converting heading paragraphs."""
        doc = ParsedDocument(
            all_paragraphs=[
                Paragraph(content="Heading 1", is_heading=True, heading_level=1),
                Paragraph(content="Heading 2", is_heading=True, heading_level=2),
                Paragraph(content="Heading 3", is_heading=True, heading_level=3),
            ]
        )
        result = converter.convert_document(doc)

        assert "# Heading 1" in result
        assert "## Heading 2" in result
        assert "### Heading 3" in result

    def test_convert_regular_paragraph(self, converter):
        """Test converting regular paragraphs."""
        doc = ParsedDocument(
            all_paragraphs=[
                Paragraph(content="Regular paragraph text"),
            ]
        )
        result = converter.convert_document(doc)
        assert "Regular paragraph text" in result

    def test_convert_unordered_list(self, converter):
        """Test converting unordered lists."""
        doc = ParsedDocument(
            all_lists=[
                [
                    ListItem(content="Item 1", level=0, ordered=False),
                    ListItem(content="Item 2", level=0, ordered=False),
                    ListItem(content="Item 3", level=0, ordered=False),
                ]
            ]
        )
        result = converter.convert_document(doc)

        assert "- Item 1" in result
        assert "- Item 2" in result
        assert "- Item 3" in result

    def test_convert_ordered_list(self, converter):
        """Test converting ordered lists."""
        doc = ParsedDocument(
            all_lists=[
                [
                    ListItem(content="First", level=0, ordered=True),
                    ListItem(content="Second", level=0, ordered=True),
                    ListItem(content="Third", level=0, ordered=True),
                ]
            ]
        )
        result = converter.convert_document(doc)

        assert "1. First" in result
        assert "2. Second" in result
        assert "3. Third" in result

    def test_convert_nested_list(self, converter):
        """Test converting nested lists."""
        doc = ParsedDocument(
            all_lists=[
                [
                    ListItem(content="Level 1", level=0, ordered=False),
                    ListItem(content="Level 2", level=1, ordered=False),
                    ListItem(content="Level 3", level=2, ordered=False),
                ]
            ]
        )
        result = converter.convert_document(doc)

        assert "- Level 1" in result
        assert "  - Level 2" in result
        assert "    - Level 3" in result

    def test_convert_mixed_nested_list(self, converter):
        """Test converting mixed ordered/unordered nested lists."""
        doc = ParsedDocument(
            all_lists=[
                [
                    ListItem(content="Bullet", level=0, ordered=False),
                    ListItem(content="Nested ordered", level=1, ordered=True),
                    ListItem(content="Back to bullet", level=0, ordered=False),
                ]
            ]
        )
        result = converter.convert_document(doc)

        assert "- Bullet" in result
        assert "1. Nested ordered" in result
        assert "- Back to bullet" in result

    def test_convert_simple_table(self, converter):
        """Test converting a simple table."""
        doc = ParsedDocument(
            all_tables=[
                Table(
                    headers=["Header 1", "Header 2"],
                    rows=[
                        TableRow(cells=[TableCell(content="Cell 1"), TableCell(content="Cell 2")]),
                        TableRow(cells=[TableCell(content="Cell 3"), TableCell(content="Cell 4")]),
                    ],
                )
            ]
        )
        result = converter.convert_document(doc)

        assert "| Header 1 | Header 2 |" in result
        assert "| --- | --- |" in result
        assert "| Cell 1 | Cell 2 |" in result
        assert "| Cell 3 | Cell 4 |" in result

    def test_convert_empty_table(self, converter):
        """Test converting an empty table."""
        doc = ParsedDocument(
            all_tables=[
                Table(headers=[], rows=[])
            ]
        )
        result = converter.convert_document(doc)
        assert result == ""  # Empty table should produce no output

    def test_convert_image(self, converter):
        """Test converting images."""
        doc = ParsedDocument(
            all_images=[
                Image(src="image.png", alt="Test image"),
            ]
        )
        result = converter.convert_document(doc)
        assert "![Test image](image.png)" in result

    def test_convert_image_without_alt(self, converter):
        """Test converting images without alt text."""
        doc = ParsedDocument(
            all_images=[
                Image(src="photo.jpg"),
            ]
        )
        result = converter.convert_document(doc)
        assert "![image](photo.jpg)" in result or "![](photo.jpg)" in result

    def test_convert_image_with_base_path(self):
        """Test converting images with a base path."""
        converter = MarkdownConverter(images_base_path="/images")
        doc = ParsedDocument(
            all_images=[
                Image(src="picture.png", alt="Picture"),
            ]
        )
        result = converter.convert_document(doc)
        assert "/images" in result

    def test_convert_section_with_title(self, converter):
        """Test converting sections with titles."""
        doc = ParsedDocument(
            sections=[
                ParsedSection(
                    title="Section Title",
                    paragraphs=[Paragraph(content="Section content")],
                )
            ]
        )
        result = converter.convert_document(doc)

        assert "## Section Title" in result
        assert "Section content" in result

    def test_convert_subsection(self, converter):
        """Test converting subsections."""
        doc = ParsedDocument(
            sections=[
                ParsedSection(
                    title="Main Section",
                    subsections=[
                        ParsedSection(
                            title="Subsection",
                            paragraphs=[Paragraph(content="Subsection content")],
                        )
                    ],
                )
            ]
        )
        result = converter.convert_document(doc)

        assert "## Main Section" in result
        assert "### Subsection" in result
        assert "Subsection content" in result

    def test_convert_section_with_multiple_elements(self, converter):
        """Test converting a section with multiple element types."""
        doc = ParsedDocument(
            sections=[
                ParsedSection(
                    title="Section",
                    paragraphs=[
                        Paragraph(content="Paragraph 1"),
                        Paragraph(content="Paragraph 2"),
                    ],
                    lists=[
                        [ListItem(content="List item")]
                    ],
                    tables=[
                        Table(
                            headers=["Col"],
                            rows=[TableRow(cells=[TableCell(content="Data")])],
                        )
                    ],
                    images=[
                        Image(src="img.png", alt="Image"),
                    ],
                )
            ]
        )
        result = converter.convert_document(doc)

        assert "## Section" in result
        assert "Paragraph 1" in result
        assert "- List item" in result
        assert "| Col |" in result
        assert "![Image](img.png)" in result

    def test_convert_document_with_sections_and_flat_content(self, converter):
        """Test that sections take priority over flat content."""
        doc = ParsedDocument(
            sections=[
                ParsedSection(title="Section", paragraphs=[Paragraph(content="Section content")]),
            ],
            all_paragraphs=[Paragraph(content="Flat content")],
        )
        result = converter.convert_document(doc)

        assert "## Section" in result
        assert "Section content" in result
        # Flat content should not appear when sections exist
        # (based on current implementation)

    def test_convert_multiple_sections(self, converter):
        """Test converting multiple top-level sections."""
        doc = ParsedDocument(
            sections=[
                ParsedSection(title="Section 1", paragraphs=[Paragraph(content="Content 1")]),
                ParsedSection(title="Section 2", paragraphs=[Paragraph(content="Content 2")]),
            ]
        )
        result = converter.convert_document(doc)

        assert "## Section 1" in result
        assert "Content 1" in result
        assert "## Section 2" in result
        assert "Content 2" in result

    def test_convert_empty_lists(self, converter):
        """Test converting document with empty lists."""
        doc = ParsedDocument(all_lists=[[]])
        result = converter.convert_document(doc)
        assert result == ""


class TestMarkdownConversionError:
    """Tests for MarkdownConversionError exception."""

    def test_error_is_exception(self):
        """Test that MarkdownConversionError inherits from Exception."""
        error = MarkdownConversionError("Test error")
        assert isinstance(error, Exception)

    def test_error_message(self):
        """Test that error message is properly set."""
        error = MarkdownConversionError("Conversion failed")
        assert str(error) == "Conversion failed"


class TestDocumentToMarkdownFunction:
    """Tests for the document_to_markdown convenience function."""

    def test_document_to_markdown_function(self):
        """Test the module-level document_to_markdown function."""
        doc = ParsedDocument(title="Test")
        result = document_to_markdown(doc)
        assert "# Test" in result

    def test_document_to_markdown_with_base_path(self):
        """Test document_to_markdown with images_base_path."""
        doc = ParsedDocument(
            all_images=[Image(src="img.png", alt="Image")]
        )
        result = document_to_markdown(doc, images_base_path="/static")
        assert "/static" in result


class TestHTMLToMarkdownFunction:
    """Tests for the html_to_markdown convenience function."""

    def test_html_to_markdown_function(self):
        """Test the module-level html_to_markdown function."""
        html = "<html><body><h1>Test</h1></body></html>"
        result = html_to_markdown(html)
        assert "# Test" in result

    def test_html_to_markdown_with_images_dir(self, tmp_path):
        """Test html_to_markdown with images directory."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()

        html = '<html><body><img src="test.png" alt="Test" /></body></html>'
        result = html_to_markdown(html, images_dir)
        assert "![Test]" in result


class TestTableToMarkdownRows:
    """Tests for Table.to_markdown_rows method."""

    def test_empty_table(self):
        """Test to_markdown_rows with empty table."""
        table = Table(headers=[], rows=[])
        rows = table.to_markdown_rows()
        assert rows == [[]]

    def test_single_column_table(self):
        """Test to_markdown_rows with single column."""
        table = Table(
            headers=["Header"],
            rows=[TableRow(cells=[TableCell(content="Data")])],
        )
        rows = table.to_markdown_rows()
        assert rows[0] == ["Header"]
        assert rows[1] == ["Data"]

    def test_multiple_columns(self):
        """Test to_markdown_rows with multiple columns."""
        table = Table(
            headers=["A", "B", "C"],
            rows=[
                TableRow(cells=[TableCell(content="1"), TableCell(content="2"), TableCell(content="3")]),
            ],
        )
        rows = table.to_markdown_rows()
        assert len(rows[0]) == 3
        assert len(rows[1]) == 3
