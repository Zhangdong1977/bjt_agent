"""Parsers module for document conversion and parsing.

This module provides a pipeline for converting DOCX/DOC files to Markdown:
1. LibreOfficeConverter - Convert DOCX/DOC to HTML using LibreOffice
2. HTMLParser - Parse HTML and extract structural elements
3. MarkdownConverter - Convert parsed structure to Markdown

Example usage:
    from backend.parsers import LibreOfficeConverter, HTMLParser, MarkdownConverter

    # Convert DOCX to HTML
    converter = LibreOfficeConverter()
    result = await converter.convert(Path("document.docx"))

    # Parse HTML structure
    parser = HTMLParser(result["text"])
    doc = parser.parse()

    # Convert to Markdown
    md_converter = MarkdownConverter()
    markdown = md_converter.convert_document(doc)
"""

from .html_parser import (
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
    copy_images_to_output,
    parse_html,
)
from .libreoffice_converter import (
    LibreOfficeConversionError,
    LibreOfficeConverter,
    convert_to_html,
)
from .markdown_converter import (
    MarkdownConversionError,
    MarkdownConverter,
    document_to_markdown,
    html_to_markdown,
)

__all__ = [
    # LibreOffice converter
    "LibreOfficeConverter",
    "LibreOfficeConversionError",
    "convert_to_html",
    # HTML parser
    "HTMLParser",
    "HTMLParserError",
    "parse_html",
    "copy_images_to_output",
    "ParsedDocument",
    "ParsedSection",
    "Paragraph",
    "ListItem",
    "Table",
    "TableRow",
    "TableCell",
    "Image",
    # Markdown converter
    "MarkdownConverter",
    "MarkdownConversionError",
    "document_to_markdown",
    "html_to_markdown",
]
