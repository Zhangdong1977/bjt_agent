"""HTML structure parser using BeautifulSoup4."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


@dataclass
class TableCell:
    """Represents a table cell."""

    content: str
    is_header: bool = False
    row_span: int = 1
    col_span: int = 1


@dataclass
class TableRow:
    """Represents a table row."""

    cells: list[TableCell]


@dataclass
class Table:
    """Represents a parsed table."""

    headers: list[str]
    rows: list[TableRow]

    def to_markdown_rows(self) -> list[list[str]]:
        """Convert table to markdown format rows."""
        result = [self.headers]
        for row in self.rows:
            result.append([cell.content for cell in row.cells])
        return result


@dataclass
class ListItem:
    """Represents a list item."""

    content: str
    level: int = 0
    ordered: bool = False


@dataclass
class Image:
    """Represents an image reference."""

    src: str
    alt: str = ""
    path: Optional[Path] = None


@dataclass
class Paragraph:
    """Represents a paragraph of text."""

    content: str
    is_heading: bool = False
    heading_level: int = 0  # 1-6 for h1-h6


@dataclass
class ParsedSection:
    """Represents a parsed document section."""

    title: Optional[str] = None
    paragraphs: list[Paragraph] = field(default_factory=list)
    lists: list[list[ListItem]] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    subsections: list["ParsedSection"] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """Represents a fully parsed HTML document."""

    title: Optional[str] = None
    sections: list[ParsedSection] = field(default_factory=list)
    all_paragraphs: list[Paragraph] = field(default_factory=list)
    all_lists: list[list[ListItem]] = field(default_factory=list)
    all_tables: list[Table] = field(default_factory=list)
    all_images: list[Image] = field(default_factory=list)


class HTMLParserError(Exception):
    """Raised when HTML parsing fails."""

    pass


class HTMLParser:
    """Parser for HTML documents using BeautifulSoup4.

    Extracts structural elements including headings, paragraphs, lists,
    tables, and images.
    """

    def __init__(self, html: str, images_dir: Optional[Path] = None):
        """Initialize the HTML parser.

        Args:
            html: HTML content as string
            images_dir: Optional directory path for resolving relative image paths
        """
        self.html = html
        self.images_dir = images_dir
        try:
            # Use html.parser for broad compatibility, lxml can be added as optional dependency
            self.soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            raise HTMLParserError(f"Failed to parse HTML: {e}")

    def parse(self) -> ParsedDocument:
        """Parse the HTML document and extract all structural elements.

        Returns:
            ParsedDocument containing all extracted elements
        """
        doc = ParsedDocument()

        # Extract document title
        title_tag = self.soup.find("title")
        if title_tag:
            doc.title = title_tag.get_text(strip=True)

        # Extract all elements in order
        doc.all_paragraphs = self._extract_paragraphs()
        doc.all_lists = self._extract_lists()
        doc.all_tables = self._extract_tables()
        doc.all_images = self._extract_images()

        # Extract sections based on heading hierarchy
        doc.sections = self._extract_sections()

        return doc

    def _extract_paragraphs(self) -> list[Paragraph]:
        """Extract all paragraphs and headings."""
        paragraphs = []

        for tag in self.soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
            text = self._get_text_content(tag)
            if not text.strip():
                continue

            if tag.name.startswith("h") and len(tag.name) == 2:
                level = int(tag.name[1])
                paragraphs.append(Paragraph(content=text, is_heading=True, heading_level=level))
            elif tag.name == "p":
                paragraphs.append(Paragraph(content=text))

        return paragraphs

    def _extract_lists(self) -> list[list[ListItem]]:
        """Extract all lists (ordered and unordered)."""
        all_lists = []

        for tag in self.soup.find_all(["ul", "ol"]):
            items = []
            ordered = tag.name == "ol"
            level = self._get_list_level(tag)

            for li in tag.find_all("li", recursive=False):
                text = self._get_text_content(li)
                items.append(ListItem(content=text, level=level, ordered=ordered))

            if items:
                all_lists.append(items)

        return all_lists

    def _extract_tables(self) -> list[Table]:
        """Extract all tables."""
        tables = []

        for table_tag in self.soup.find_all("table"):
            table = self._parse_table(table_tag)
            if table:
                tables.append(table)

        return tables

    def _parse_table(self, table_tag: Tag) -> Optional[Table]:
        """Parse a single table element."""
        headers = []
        rows = []

        # Find header row (usually in th tags)
        header_row = table_tag.find("tr")
        if header_row:
            for th in header_row.find_all(["th", "td"]):
                headers.append(self._get_text_content(th).strip())

        if not headers:
            # If no header row found, look for any row with th tags
            th_rows = table_tag.find_all("tr")
            for tr in th_rows:
                ths = tr.find_all("th")
                if ths:
                    headers = [self._get_text_content(th).strip() for th in ths]
                    break

        # Find all data rows
        for tr in table_tag.find_all("tr"):
            # Skip the header row if it was processed
            if tr.find("th") and not tr.find("td"):
                continue

            cells = []
            for td in tr.find_all(["td", "th"]):
                is_header = td.name == "th"
                cell = TableCell(
                    content=self._get_text_content(td).strip(),
                    is_header=is_header,
                    row_span=int(td.get("rowspan", 1)),
                    col_span=int(td.get("colspan", 1)),
                )
                cells.append(cell)

            if cells:
                rows.append(TableRow(cells=cells))

        if not headers and not rows:
            return None

        return Table(headers=headers, rows=rows)

    def _extract_images(self) -> list[Image]:
        """Extract all image references."""
        images = []

        for img in self.soup.find_all("img"):
            src = img.get("src", "")
            alt = img.get("alt", "")

            if not src:
                continue

            # Resolve relative paths if images_dir is provided
            image_path = None
            if self.images_dir and not src.startswith(("http://", "https://", "/")):
                image_path = self.images_dir / src
                if not image_path.exists():
                    image_path = None

            images.append(Image(src=src, alt=alt, path=image_path))

        return images

    def _extract_sections(self) -> list[ParsedSection]:
        """Extract sections based on heading hierarchy."""
        sections = []
        current_section: Optional[ParsedSection] = None
        current_heading_level = 0

        for element in self.soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table"]):
            if element.name.startswith("h") and len(element.name) == 2:
                level = int(element.name[1])
                text = self._get_text_content(element)

                if level <= current_heading_level or current_section is None:
                    # New top-level section
                    if current_section and current_section.title:
                        sections.append(current_section)
                    current_section = ParsedSection(title=text)
                    current_heading_level = level
                else:
                    # Subsection
                    if current_section is None:
                        current_section = ParsedSection(title=text)
                    else:
                        subsection = ParsedSection(title=text)
                        current_section.subsections.append(subsection)
                        current_heading_level = level

            elif element.name == "p" and current_section is not None:
                text = self._get_text_content(element)
                if text.strip():
                    current_section.paragraphs.append(Paragraph(content=text))

        if current_section and current_section.title:
            sections.append(current_section)

        return sections

    def _get_text_content(self, tag: Tag) -> str:
        """Get text content from a tag, including nested tags."""
        return tag.get_text(separator=" ", strip=True)

    def _get_list_level(self, tag: Tag) -> int:
        """Calculate the nesting level of a list."""
        level = 0
        parent = tag.parent
        while parent:
            if parent.name in ["ul", "ol"]:
                level += 1
            parent = parent.parent
        return level


def parse_html(html: str, images_dir: Optional[Path] = None) -> ParsedDocument:
    """Parse HTML content and extract structural elements.

    Args:
        html: HTML content as string
        images_dir: Optional directory path for resolving relative image paths

    Returns:
        ParsedDocument containing all extracted elements

    Raises:
        HTMLParserError: If parsing fails
    """
    parser = HTMLParser(html, images_dir)
    return parser.parse()


def copy_images_to_output(images: list[Image], output_dir: Path, images_dir: Optional[Path] = None) -> list[Image]:
    """Copy images to the output directory and update their paths.

    Args:
        images: List of Image objects from parsed document
        output_dir: Target directory for copied images
        images_dir: Source directory containing the images (from LibreOffice extraction)

    Returns:
        List of Image objects with updated paths pointing to the output directory
    """
    import shutil

    output_images_dir = output_dir / "images"
    output_images_dir.mkdir(parents=True, exist_ok=True)

    updated_images = []
    for img in images:
        src_path = img.path if img.path else (images_dir / img.src if images_dir else Path(img.src))

        if src_path and src_path.exists():
            try:
                # Copy image to output directory
                dest_path = output_images_dir / img.src
                shutil.copy2(src_path, dest_path)
                updated_images.append(Image(src=str(dest_path), alt=img.alt, path=dest_path))
            except Exception as e:
                logger.warning(f"Failed to copy image {img.src}: {e}")
                updated_images.append(img)
        else:
            # Keep original if source doesn't exist
            updated_images.append(img)

    return updated_images
