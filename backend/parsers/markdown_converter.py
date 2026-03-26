"""Markdown converter for parsed HTML document structures."""

import logging
from pathlib import Path
from typing import Optional

from .html_parser import (
    Image,
    ListItem,
    ParsedDocument,
    ParsedSection,
    Table,
    TableRow,
)

logger = logging.getLogger(__name__)


class MarkdownConversionError(Exception):
    """Raised when markdown conversion fails."""

    pass


class MarkdownConverter:
    """Converts parsed HTML structures to Markdown format.

    Transforms headings, paragraphs, lists, tables, and images
    into properly formatted Markdown.
    """

    def __init__(self, images_base_path: Optional[str] = None):
        """Initialize the converter.

        Args:
            images_base_path: Base path to use for image references in Markdown.
                             If None, relative paths from the HTML are used.
        """
        self.images_base_path = images_base_path

    def convert_document(self, doc: ParsedDocument) -> str:
        """Convert a parsed document to Markdown.

        Args:
            doc: ParsedDocument from HTMLParser

        Returns:
            Markdown-formatted string
        """
        parts = []

        # Add title if available
        if doc.title:
            parts.append(f"# {doc.title}\n")

        # Convert sections
        if doc.sections:
            for section in doc.sections:
                parts.append(self._convert_section(section, base_level=2))
            # Also include flat elements that weren't captured in sections
            for lst in doc.all_lists:
                parts.append(self._convert_list(lst))
            for table in doc.all_tables:
                parts.append(self._convert_table(table))
            for img in doc.all_images:
                parts.append(self._convert_image(img))
        else:
            # If no sections, convert flat elements
            for para in doc.all_paragraphs:
                parts.append(self._convert_paragraph(para))

            for lst in doc.all_lists:
                parts.append(self._convert_list(lst))

            for table in doc.all_tables:
                parts.append(self._convert_table(table))

            for img in doc.all_images:
                parts.append(self._convert_image(img))

        return "\n\n".join(parts)

    def _convert_section(self, section: ParsedSection, base_level: int = 2) -> str:
        """Convert a section to Markdown.

        Args:
            section: ParsedSection to convert
            base_level: Base heading level (2 for sections, increments for subsections)
        """
        parts = []

        if section.title:
            parts.append(f"{'#' * base_level} {section.title}\n")

        for para in section.paragraphs:
            parts.append(self._convert_paragraph(para))

        for lst in section.lists:
            parts.append(self._convert_list(lst))

        for table in section.tables:
            parts.append(self._convert_table(table))

        for img in section.images:
            parts.append(self._convert_image(img))

        for subsection in section.subsections:
            parts.append(self._convert_section(subsection, base_level=base_level + 1))

        return "\n\n".join(parts)

    def _convert_paragraph(self, para) -> str:
        """Convert a paragraph to Markdown."""
        if hasattr(para, "is_heading") and para.is_heading:
            prefix = "#" * para.heading_level
            return f"{prefix} {para.content}"
        return para.content

    def _convert_list(self, items: list[ListItem]) -> str:
        """Convert a list to Markdown."""
        lines = []
        current_level = 0
        current_ordered = False
        ordered_counter = 0

        for item in items:
            # Determine indentation
            indent = "  " * item.level

            # Check if list type changed
            if item.level != current_level or item.ordered != current_ordered:
                if lines and current_level > 0:
                    # Close previous nested list
                    pass
                current_level = item.level
                current_ordered = item.ordered
                ordered_counter = 0 if item.ordered else -1

            ordered_counter += 1
            if item.ordered:
                lines.append(f"{indent}{ordered_counter}. {item.content}")
            else:
                lines.append(f"{indent}- {item.content}")

        return "\n".join(lines)

    def _convert_table(self, table: Table) -> str:
        """Convert a table to Markdown."""
        if not table.headers and not table.rows:
            return ""

        lines = []

        # Header row
        header_line = "| " + " | ".join(table.headers) + " |"
        lines.append(header_line)

        # Separator row
        separator = "| " + " | ".join(["---"] * len(table.headers)) + " |"
        lines.append(separator)

        # Data rows
        for row in table.rows:
            row_cells = []
            for cell in row.cells:
                row_cells.append(cell.content)
            lines.append("| " + " | ".join(row_cells) + " |")

        return "\n".join(lines)

    def _convert_image(self, img: Image) -> str:
        """Convert an image to Markdown."""
        alt = img.alt or "image"
        src = img.src

        # Use absolute path or base path if provided
        if self.images_base_path:
            if img.path and img.path.exists():
                src = str(img.path)
            else:
                # Try to construct path relative to base
                src = f"{self.images_base_path}/{img.src}"

        return f"![{alt}]({src})"


def document_to_markdown(doc: ParsedDocument, images_base_path: Optional[str] = None) -> str:
    """Convert a parsed document to Markdown.

    Args:
        doc: ParsedDocument from HTMLParser
        images_base_path: Optional base path for image references

    Returns:
        Markdown-formatted string
    """
    converter = MarkdownConverter(images_base_path)
    return converter.convert_document(doc)


def html_to_markdown(html: str, images_dir: Optional[Path] = None) -> str:
    """Convert HTML string directly to Markdown.

    This is a convenience function that combines HTMLParser and MarkdownConverter.

    Args:
        html: HTML content string
        images_dir: Optional directory for resolving image paths

    Returns:
        Markdown-formatted string
    """
    from .html_parser import parse_html

    doc = parse_html(html, images_dir)
    return document_to_markdown(doc)
