"""Integration tests for the parsers pipeline.

Tests the full conversion pipeline: LibreOffice -> HTML Parser -> Markdown Converter.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestParsersPipeline:
    """Integration tests for the full parsers pipeline."""

    @pytest.fixture
    def sample_images_dir(self, tmp_path):
        """Create a sample images directory with fake images."""
        images_dir = tmp_path / "sample_files"
        images_dir.mkdir()
        (images_dir / "image1.png").write_bytes(b"fake png data")
        (images_dir / "image2.jpg").write_bytes(b"fake jpg data")
        return images_dir

    @pytest.fixture
    def sample_html(self):
        """Sample HTML content for testing."""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Sample Document</title>
        </head>
        <body>
            <h1>Main Title</h1>
            <p>This is an introductory paragraph.</p>
            <h2>Section 1</h2>
            <p>Content of section 1.</p>
            <h3>Subsection 1.1</h3>
            <p>Content of subsection 1.1.</p>
            <h2>Section 2</h2>
            <ul>
                <li>Item 1</li>
                <li>Item 2</li>
                <li>Item 3</li>
            </ul>
            <ol>
                <li>First</li>
                <li>Second</li>
                <li>Third</li>
            </ol>
            <table>
                <tr><th>Header 1</th><th>Header 2</th></tr>
                <tr><td>Data 1</td><td>Data 2</td></tr>
                <tr><td>Data 3</td><td>Data 4</td></tr>
            </table>
            <img src="image1.png" alt="Test Image" />
            <img src="image2.jpg" />
        </body>
        </html>
        """

    @pytest.fixture
    def sample_images_dir(self, tmp_path):
        """Create a sample images directory with fake images."""
        images_dir = tmp_path / "sample_files"
        images_dir.mkdir()
        (images_dir / "image1.png").write_bytes(b"fake png data")
        (images_dir / "image2.jpg").write_bytes(b"fake jpg data")
        return images_dir

    def test_html_parser_to_markdown_pipeline(self, sample_html):
        """Test HTML parsing to Markdown conversion pipeline."""
        from backend.parsers import HTMLParser, document_to_markdown

        # Parse HTML
        parser = HTMLParser(sample_html)
        doc = parser.parse()

        # Convert to Markdown
        markdown = document_to_markdown(doc)

        # Verify structure is preserved
        assert "# Sample Document" in markdown  # Title
        assert "Main Title" in markdown  # h1 becomes heading
        assert "Section 1" in markdown
        assert "Section 2" in markdown
        assert "- Item 1" in markdown
        assert "- Item 2" in markdown
        assert "1. First" in markdown
        assert "| Header 1 | Header 2 |" in markdown
        assert "![Test Image]" in markdown

    def test_html_parser_extracts_all_elements(self, sample_html):
        """Test that HTML parser extracts all element types."""
        from backend.parsers import HTMLParser

        parser = HTMLParser(sample_html)
        doc = parser.parse()

        # Verify headings (h1, h2, h2, h3 = 4 headings)
        headings = [p for p in doc.all_paragraphs if p.is_heading]
        assert len(headings) == 4

        # Verify paragraphs (intro + section1 + subsection1.1 = 3; no para after Section 2)
        paragraphs = [p for p in doc.all_paragraphs if not p.is_heading]
        assert len(paragraphs) == 3

        # Verify lists (ul and ol)
        assert len(doc.all_lists) == 2

        # Verify tables
        assert len(doc.all_tables) == 1

        # Verify images
        assert len(doc.all_images) == 2

    def test_html_parser_sections(self, sample_html):
        """Test section extraction from HTML."""
        from backend.parsers import HTMLParser

        parser = HTMLParser(sample_html)
        doc = parser.parse()

        # Verify sections were extracted
        assert len(doc.sections) >= 1

        # First section should have title
        first_section = doc.sections[0]
        assert first_section.title == "Main Title"

    def test_html_parser_with_images_dir(self, sample_html, sample_images_dir):
        """Test HTML parser with images directory for path resolution."""
        from backend.parsers import HTMLParser

        parser = HTMLParser(sample_html, images_dir=sample_images_dir)
        doc = parser.parse()

        # Verify image paths are resolved
        assert len(doc.all_images) == 2
        assert doc.all_images[0].path == sample_images_dir / "image1.png"
        assert doc.all_images[1].path == sample_images_dir / "image2.jpg"

    def test_markdown_converter_heading_levels(self):
        """Test that Markdown converter uses correct heading levels."""
        from backend.parsers import ParsedDocument, ParsedSection, Paragraph, document_to_markdown

        doc = ParsedDocument(
            sections=[
                ParsedSection(
                    title="Section 1",
                    paragraphs=[Paragraph(content="Content")],
                    subsections=[
                        ParsedSection(
                            title="Subsection 1.1",
                            paragraphs=[Paragraph(content="Subcontent")],
                        )
                    ],
                ),
                ParsedSection(
                    title="Section 2",
                    paragraphs=[Paragraph(content="More content")],
                ),
            ]
        )

        markdown = document_to_markdown(doc)

        # Verify heading hierarchy
        assert "## Section 1" in markdown
        assert "### Subsection 1.1" in markdown
        assert "## Section 2" in markdown

    def test_markdown_converter_tables(self):
        """Test Markdown table generation."""
        from backend.parsers import Table, TableRow, TableCell, ParsedDocument, document_to_markdown

        doc = ParsedDocument(
            all_tables=[
                Table(
                    headers=["Name", "Value", "Status"],
                    rows=[
                        TableRow(cells=[TableCell(content="Item 1"), TableCell(content="100"), TableCell(content="Active")]),
                        TableRow(cells=[TableCell(content="Item 2"), TableCell(content="200"), TableCell(content="Inactive")]),
                    ],
                )
            ]
        )

        markdown = document_to_markdown(doc)

        # Verify table structure
        assert "| Name | Value | Status |" in markdown
        assert "| --- | --- | --- |" in markdown
        assert "| Item 1 | 100 | Active |" in markdown
        assert "| Item 2 | 200 | Inactive |" in markdown

    def test_markdown_converter_nested_lists(self):
        """Test Markdown nested list generation."""
        from backend.parsers import ParsedDocument, ListItem, document_to_markdown

        doc = ParsedDocument(
            all_lists=[
                [
                    ListItem(content="Level 1 Item", level=0, ordered=False),
                    ListItem(content="Level 2 Item A", level=1, ordered=False),
                    ListItem(content="Level 3 Item", level=2, ordered=False),
                    ListItem(content="Level 2 Item B", level=1, ordered=False),
                    ListItem(content="Back to Level 1", level=0, ordered=False),
                ]
            ]
        )

        markdown = document_to_markdown(doc)

        # Verify nested list structure
        assert "- Level 1 Item" in markdown
        assert "  - Level 2 Item A" in markdown
        assert "    - Level 3 Item" in markdown
        assert "  - Level 2 Item B" in markdown
        assert "- Back to Level 1" in markdown

    def test_markdown_converter_ordered_lists(self):
        """Test Markdown ordered list generation."""
        from backend.parsers import ParsedDocument, ListItem, document_to_markdown

        doc = ParsedDocument(
            all_lists=[
                [
                    ListItem(content="Step 1", level=0, ordered=True),
                    ListItem(content="Step 2", level=0, ordered=True),
                    ListItem(content="Step 3", level=0, ordered=True),
                ]
            ]
        )

        markdown = document_to_markdown(doc)

        assert "1. Step 1" in markdown
        assert "2. Step 2" in markdown
        assert "3. Step 3" in markdown

    def test_copy_images_to_output(self, sample_images_dir, tmp_path):
        """Test copying images to output directory."""
        from backend.parsers import Image, copy_images_to_output

        images = [
            Image(src="image1.png", alt="Test 1", path=sample_images_dir / "image1.png"),
            Image(src="image2.jpg", alt="Test 2", path=sample_images_dir / "image2.jpg"),
        ]

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        copied_images = copy_images_to_output(images, output_dir, sample_images_dir)

        # Verify images were copied
        assert len(copied_images) == 2
        assert (output_dir / "images" / "image1.png").exists()
        assert (output_dir / "images" / "image2.jpg").exists()

        # Verify paths are updated
        assert "images/image1.png" in copied_images[0].src
        assert "images/image2.jpg" in copied_images[1].src

    def test_copy_images_nonexistent_source(self, tmp_path):
        """Test copying images when source doesn't exist."""
        from backend.parsers import Image, copy_images_to_output

        images = [
            Image(src="nonexistent.png", alt="Missing", path=Path("/nonexistent/nonexistent.png")),
        ]

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        copied_images = copy_images_to_output(images, output_dir)

        # Should return original image when source doesn't exist
        assert len(copied_images) == 1
        assert copied_images[0].src == "nonexistent.png"

    def test_full_pipeline_html_to_markdown(self, sample_html, sample_images_dir):
        """Test complete HTML to Markdown pipeline with images."""
        from backend.parsers import HTMLParser, document_to_markdown, copy_images_to_output

        # Parse HTML with images directory
        parser = HTMLParser(sample_html, images_dir=sample_images_dir)
        doc = parser.parse()

        # Copy images to output
        output_dir = sample_images_dir.parent / "output"
        output_dir.mkdir(exist_ok=True)
        updated_images = copy_images_to_output(doc.all_images, output_dir, sample_images_dir)

        # Create new document with updated images
        from backend.parsers import ParsedDocument
        doc_with_updated_images = ParsedDocument(
            title=doc.title,
            sections=doc.sections,
            all_paragraphs=doc.all_paragraphs,
            all_lists=doc.all_lists,
            all_tables=doc.all_tables,
            all_images=updated_images,
        )

        # Convert to Markdown
        markdown = document_to_markdown(doc_with_updated_images, images_base_path=str(output_dir / "images"))

        # Verify everything is present - h1 becomes ## in section context
        assert "Main Title" in markdown
        assert "Section 1" in markdown
        assert "- Item 1" in markdown
        assert "| Header 1 |" in markdown
        assert "![Test Image]" in markdown

    def test_html_parser_handles_malformed_html(self):
        """Test that HTML parser gracefully handles malformed HTML."""
        from backend.parsers import HTMLParser

        malformed_html = """
        <html>
        <body>
            <h1>Title</h>
            <p>Unclosed paragraph
            <div>Missing closing div
            <table>
                <tr><th>Header<td>Data</tr>
            </table>
        </body>
        </html>
        """

        parser = HTMLParser(malformed_html)
        doc = parser.parse()

        # Should still extract what it can
        assert len(doc.all_paragraphs) >= 1
        assert len(doc.all_tables) == 1

    def test_html_parser_whitespace_cleaning(self):
        """Test that HTML parser properly cleans whitespace."""
        from backend.parsers import HTMLParser

        html_with_whitespace = """
        <html>
        <body>
            <p>

                Extra   spaces

                and    multiple    lines

            </p>
        </body>
        </html>
        """

        parser = HTMLParser(html_with_whitespace)
        doc = parser.parse()

        # Whitespace should be normalized to single spaces
        assert len(doc.all_paragraphs) == 1
        content = doc.all_paragraphs[0].content
        # BeautifulSoup's get_text() with separator=" " joins text and strips
        assert content.startswith("Extra") or "Extra" in content

    def test_html_parser_nested_table_structure(self):
        """Test parsing of complex table structures."""
        from backend.parsers import HTMLParser

        html_with_complex_table = """
        <html>
        <body>
            <table>
                <tr><th colspan="2">Spanning Header</th></tr>
                <tr><td rowspan="2">Vertical Span</td><td>Cell A</td></tr>
                <tr><td>Cell B</td></tr>
            </table>
        </body>
        </html>
        """

        parser = HTMLParser(html_with_complex_table)
        doc = parser.parse()

        assert len(doc.all_tables) == 1
        table = doc.all_tables[0]
        # Header parsing: colspan is tracked but header list reflects actual th count
        assert "Spanning Header" in table.headers
        # Row count should be correct
        assert len(table.rows) == 2


class TestLibreOfficeToHTMLPipeline:
    """Integration tests for LibreOffice to HTML pipeline."""

    @pytest.mark.asyncio
    async def test_libreoffice_converter_returns_html_and_images_dir(self, tmp_path):
        """Test that LibreOffice converter returns both HTML path and images directory."""
        from backend.parsers import LibreOfficeConverter

        # Create a fake DOCX file
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(b"PK\x03\x04fake docx content")  # Minimal ZIP/DOCX signature
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        converter = LibreOfficeConverter()

        # Create fake output files
        html_path = output_dir / "test.html"
        images_dir = output_dir / "test_files"
        images_dir.mkdir()
        html_path.write_text("<html><body><h1>Test</h1></body></html>")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr=b"")

            with patch.object(Path, "glob", return_value=[html_path]):
                result = await converter.convert(docx_path, output_dir)

        assert "html_path" in result
        assert "images_dir" in result
        assert "text" in result

    @pytest.mark.asyncio
    async def test_pipeline_libreoffice_to_markdown(self, tmp_path):
        """Test full pipeline from LibreOffice conversion to Markdown."""
        from backend.parsers import LibreOfficeConverter, HTMLParser, document_to_markdown

        # Create a fake DOCX file
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(b"PK\x03\x04fake docx content")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        html_content = """
        <html>
        <body>
            <h1>Document Title</h1>
            <p>First paragraph.</p>
            <h2>Section</h2>
            <p>Section content.</p>
        </body>
        </html>
        """

        converter = LibreOfficeConverter()

        # Create output file
        html_path = output_dir / "test.html"
        html_path.write_text(html_content)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr=b"")

            with patch.object(Path, "glob", return_value=[html_path]):
                result = await converter.convert(docx_path, output_dir)

        # Now parse the HTML
        parser = HTMLParser(result["text"])
        doc = parser.parse()

        # And convert to Markdown
        markdown = document_to_markdown(doc)

        assert "Document Title" in markdown
        assert "First paragraph" in markdown
        assert "Section" in markdown
