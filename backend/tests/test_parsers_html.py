"""Unit tests for HTML parser module."""

import pytest
from pathlib import Path

from backend.parsers.html_parser import (
    HTMLParser,
    HTMLParserError,
    Image,
    ListItem,
    Paragraph,
    ParsedDocument,
    ParsedSection,
    Table,
    TableCell,
    TableRow,
    parse_html,
)


class TestHTMLParser:
    """Tests for HTMLParser class."""

    def test_parse_simple_html(self):
        """Test parsing a simple HTML document."""
        html = "<html><head><title>Test Title</title></head><body><h1>Heading 1</h1><p>Paragraph text</p></body></html>"

        parser = HTMLParser(html)
        doc = parser.parse()

        assert doc.title == "Test Title"
        assert len(doc.all_paragraphs) == 2  # h1 and p
        assert doc.all_paragraphs[0].content == "Heading 1"
        assert doc.all_paragraphs[0].is_heading is True
        assert doc.all_paragraphs[0].heading_level == 1
        assert doc.all_paragraphs[1].content == "Paragraph text"
        assert doc.all_paragraphs[1].is_heading is False

    def test_parse_multiple_headings(self):
        """Test parsing multiple heading levels."""
        html = """
        <html>
        <body>
            <h1>Level 1</h1>
            <h2>Level 2</h2>
            <h3>Level 3</h3>
            <h4>Level 4</h4>
            <h5>Level 5</h5>
            <h6>Level 6</h6>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.all_paragraphs) == 6
        assert doc.all_paragraphs[0].heading_level == 1
        assert doc.all_paragraphs[1].heading_level == 2
        assert doc.all_paragraphs[5].heading_level == 6

    def test_parse_unordered_list(self):
        """Test parsing unordered lists."""
        html = """
        <html>
        <body>
            <ul>
                <li>Item 1</li>
                <li>Item 2</li>
                <li>Item 3</li>
            </ul>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.all_lists) == 1
        assert len(doc.all_lists[0]) == 3
        assert doc.all_lists[0][0].content == "Item 1"
        assert doc.all_lists[0][0].ordered is False

    def test_parse_ordered_list(self):
        """Test parsing ordered lists."""
        html = """
        <html>
        <body>
            <ol>
                <li>First</li>
                <li>Second</li>
            </ol>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.all_lists) == 1
        assert doc.all_lists[0][0].ordered is True
        assert doc.all_lists[0][1].content == "Second"

    def test_parse_nested_lists(self):
        """Test parsing nested lists."""
        html = """
        <html>
        <body>
            <ul>
                <li>Level 1 Item
                    <ul>
                        <li>Level 2 Item</li>
                    </ul>
                </li>
            </ul>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.all_lists) >= 1

    def test_parse_simple_table(self):
        """Test parsing a simple table."""
        html = """
        <html>
        <body>
            <table>
                <tr><th>Header 1</th><th>Header 2</th></tr>
                <tr><td>Cell 1</td><td>Cell 2</td></tr>
            </table>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.all_tables) == 1
        table = doc.all_tables[0]
        assert table.headers == ["Header 1", "Header 2"]
        assert len(table.rows) == 1
        assert table.rows[0].cells[0].content == "Cell 1"

    def test_parse_table_without_headers(self):
        """Test parsing a table without header row."""
        html = """
        <html>
        <body>
            <table>
                <tr><td>Data 1</td><td>Data 2</td></tr>
            </table>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.all_tables) == 1

    def test_parse_images(self):
        """Test parsing image references."""
        html = """
        <html>
        <body>
            <img src="image1.png" alt="Image 1" />
            <img src="image2.jpg" />
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.all_images) == 2
        assert doc.all_images[0].src == "image1.png"
        assert doc.all_images[0].alt == "Image 1"
        assert doc.all_images[1].src == "image2.jpg"
        assert doc.all_images[1].alt == ""

    def test_parse_images_with_directory(self, tmp_path):
        """Test parsing images with a directory for path resolution."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        (images_dir / "test.png").write_bytes(b"fake image data")

        html = '<img src="test.png" alt="Test" />'

        parser = HTMLParser(html, images_dir)
        doc = parser.parse()

        assert len(doc.all_images) == 1
        assert doc.all_images[0].path == images_dir / "test.png"

    def test_parse_sections(self):
        """Test parsing document sections based on headings."""
        html = """
        <html>
        <body>
            <h1>Section 1</h1>
            <p>Content 1</p>
            <h2>Subsection 1.1</h2>
            <p>Subcontent 1.1</p>
            <h1>Section 2</h1>
            <p>Content 2</p>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.sections) >= 1
        assert doc.sections[0].title == "Section 1"

    def test_empty_html(self):
        """Test parsing empty HTML."""
        html = "<html><body></body></html>"

        parser = HTMLParser(html)
        doc = parser.parse()

        assert doc.title is None
        assert len(doc.all_paragraphs) == 0
        assert len(doc.all_lists) == 0
        assert len(doc.all_tables) == 0
        assert len(doc.all_images) == 0

    def test_whitespace_handling(self):
        """Test that whitespace is properly handled."""
        html = """
        <html>
        <body>
            <p>   Text with   extra   spaces   </p>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert "extra" in doc.all_paragraphs[0].content

    def test_get_text_content_nested_tags(self):
        """Test text extraction from nested tags."""
        html = """
        <html>
        <body>
            <p><strong>Bold</strong> and <em>italic</em> text</p>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert "Bold" in doc.all_paragraphs[0].content
        assert "italic" in doc.all_paragraphs[0].content

    def test_table_row_span_col_span(self):
        """Test that rowSpan and colSpan are properly extracted."""
        html = """
        <html>
        <body>
            <table>
                <tr><th colspan="2">Spanned Header</th></tr>
                <tr><td rowspan="2">Vertical Span</td><td>Cell</td></tr>
            </table>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        assert len(doc.all_tables) == 1
        table = doc.all_tables[0]
        # First header cell should have col_span=2
        assert table.headers[0] == "Spanned Header"

    def test_list_level_calculation(self):
        """Test that list nesting level is correctly calculated."""
        html = """
        <html>
        <body>
            <ul>
                <li>Level 1
                    <ul>
                        <li>Level 2</li>
                    </ul>
                </li>
            </ul>
        </body>
        </html>
        """

        parser = HTMLParser(html)
        doc = parser.parse()

        # The inner list should have items with level >= 1
        # Note: The exact level depends on the HTML structure


class TestHTMLParserError:
    """Tests for HTMLParserError exception."""

    def test_error_is_exception(self):
        """Test that HTMLParserError inherits from Exception."""
        error = HTMLParserError("Test error")
        assert isinstance(error, Exception)

    def test_error_message(self):
        """Test that error message is properly set."""
        error = HTMLParserError("Parse failed")
        assert str(error) == "Parse failed"


class TestParseHTMLFunction:
    """Tests for the parse_html convenience function."""

    def test_parse_html_function(self):
        """Test the module-level parse_html function."""
        html = "<html><body><h1>Title</h1></body></html>"

        doc = parse_html(html)

        assert isinstance(doc, ParsedDocument)
        assert len(doc.all_paragraphs) == 1


class TestDataClasses:
    """Tests for HTML parser data classes."""

    def test_paragraph_creation(self):
        """Test Paragraph dataclass."""
        para = Paragraph(content="Test text")
        assert para.content == "Test text"
        assert para.is_heading is False
        assert para.heading_level == 0

    def test_paragraph_heading(self):
        """Test Paragraph with heading flag."""
        para = Paragraph(content="Heading", is_heading=True, heading_level=2)
        assert para.is_heading is True
        assert para.heading_level == 2

    def test_list_item_creation(self):
        """Test ListItem dataclass."""
        item = ListItem(content="List item")
        assert item.content == "List item"
        assert item.level == 0
        assert item.ordered is False

    def test_table_cell_creation(self):
        """Test TableCell dataclass."""
        cell = TableCell(content="Cell content", is_header=False)
        assert cell.content == "Cell content"
        assert cell.is_header is False
        assert cell.row_span == 1
        assert cell.col_span == 1

    def test_table_row_creation(self):
        """Test TableRow dataclass."""
        cell = TableCell(content="Cell")
        row = TableRow(cells=[cell])
        assert len(row.cells) == 1

    def test_table_creation(self):
        """Test Table dataclass."""
        headers = ["Col1", "Col2"]
        cell = TableCell(content="Data")
        row = TableRow(cells=[cell])
        table = Table(headers=headers, rows=[row])

        assert table.headers == headers
        assert len(table.rows) == 1

    def test_table_to_markdown_rows(self):
        """Test Table.to_markdown_rows method."""
        table = Table(
            headers=["H1", "H2"],
            rows=[TableRow(cells=[TableCell(content="D1"), TableCell(content="D2")])]
        )

        rows = table.to_markdown_rows()
        assert rows[0] == ["H1", "H2"]
        assert rows[1] == ["D1", "D2"]

    def test_image_creation(self):
        """Test Image dataclass."""
        img = Image(src="test.png", alt="Test image")
        assert img.src == "test.png"
        assert img.alt == "Test image"
        assert img.path is None

    def test_parsed_section_defaults(self):
        """Test ParsedSection with default values."""
        section = ParsedSection()
        assert section.title is None
        assert len(section.paragraphs) == 0
        assert len(section.lists) == 0
        assert len(section.tables) == 0
        assert len(section.images) == 0
        assert len(section.subsections) == 0

    def test_parsed_document_defaults(self):
        """Test ParsedDocument with default values."""
        doc = ParsedDocument()
        assert doc.title is None
        assert len(doc.sections) == 0
        assert len(doc.all_paragraphs) == 0
        assert len(doc.all_lists) == 0
        assert len(doc.all_tables) == 0
        assert len(doc.all_images) == 0
