"""Markitdown converter module for DOCX/DOC/PDF/XLSX to Markdown conversion."""

import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# markdownify treats images inside table cells / headings as inline decorations
# and by default (keep_inline_images_in=[]) drops them, keeping only the alt
# text.  Bid documents routinely wrap certificate scans and verification
# screenshots in (borderless) tables; losing those refs made the review agent
# see empty sections and report missing evidence.  Whitelist the tags mammoth
# uses around cell images so `![alt](path)` refs survive inside tables.
#
# markdownify checks only the <img>'s DIRECT parent tag.  mammoth wraps an
# image in <strong>/<em> when its DOCX run is bold/italic — certificate scans
# in bold section rows therefore lost their refs and the review agent reported
# "扫描件缺失，仅文件名占位文字" for documents that actually contained the
# scans (2026-10-19 false-positive incident, review task 110fa702).  Whitelist
# every inline wrapper mammoth emits; _repair_dropped_image_refs below is the
# safety net for any wrapper still missing from this list.
_KEEP_INLINE_IMAGES_IN = [
    "p", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u", "a", "span", "li", "blockquote",
]

# 1x1 transparent PNG substituted for DOCX images whose part is missing or
# unreadable inside the archive (e.g. bid-authoring tools emitting image
# relationships with Target="../NULL").
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

# XLSX 输出上限：标书里的报价清单表动辄上千行，而审查流程对解析产物无
# 分段截断（全文进 LLM），全量转换会使 token 失控并易触发解析软超时。
# 超出部分在 Markdown 中以省略提示标注，保留可追溯性。可通过
# MarkitdownConverter 构造参数（来自 Settings，环境变量可覆盖）调整。
XLSX_MAX_ROWS_PER_SHEET = 300
XLSX_MAX_COLS_PER_SHEET = 64


def _xlsx_is_empty_row(row_values) -> bool:
    """A row with only None/blank cells: skipped entirely in sheet output."""
    return all(
        value is None or (isinstance(value, str) and not value.strip())
        for value in row_values
    )


def _xlsx_cell_text(value) -> str:
    """Render one cell value as Markdown-table-safe text.

    ``data_only=True`` makes formula cells carry their Excel-cached result;
    uncached formulas and merged-cell non-anchor cells read back as None.
    """
    import datetime as dt
    import math

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float):
        # 金额/数量列以整数为主：1.0 显示为 1 更贴近 Excel 展示，也省 token
        if math.isfinite(value) and value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return str(value)
    text = str(value)
    # 竖线会破坏 Markdown 表格结构；单元格内换行替换为 <br> 保留多行语义
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return text


class MarkitdownConversionError(Exception):
    """Raised when markitdown conversion fails."""
    pass


@dataclass
class ImageInfo:
    """Image information extracted from document."""
    filename: str
    data: bytes


@dataclass
class ConversionResult:
    """Result of document conversion."""
    markdown_content: str
    images: list[ImageInfo]
    page_count: Optional[int] = None
    # 解析质量信号（DOCX 路径填充）：mammoth warning 条数、盲区结构统计。
    # 由 document_parser 持久化到 documents.source_metadata 供巡检/运营查询；
    # 不进 markdown，对审查 agent 不可见。
    parse_quality: Optional[dict] = None


class DirectFileImageHandler:
    """Writes DOCX images directly to disk, bypassing base64 encoding.

    Instead of letting mammoth base64-encode images into data URIs, this handler
    reads image bytes from the DOCX and writes them directly to the target directory,
    returning lightweight file-path references for the HTML/Markdown output.
    """

    def __init__(self, images_dir: Path, images_dir_name: str):
        self._images_dir = images_dir
        self._images_dir_name = images_dir_name
        self._counter = 0
        self.images: list[ImageInfo] = []
        self.failed_images = 0
        # mammoth exposes the DOCX alt text (wp:docPr/@descr) as image.alt_text;
        # kept per output filename so _repair_dropped_image_refs can splice
        # refs back into the position where markdownify dropped them.
        self.alt_by_filename: dict[str, str] = {}

    def __call__(self, image):
        self._counter += 1
        try:
            alt_text = str(getattr(image, "alt_text", "") or "")
        except Exception:
            alt_text = ""
        content_type = str(image.content_type or "").lower()
        ext = {
            "image/jpeg": "jpeg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/bmp": "bmp",
            "image/tiff": "tiff",
            "image/svg+xml": "svg",
            "image/jp2": "jp2",
            "image/jpx": "jpx",
        }.get(content_type, content_type.partition("/")[2] or "png")
        ext = ext.split("+", 1)[0]
        filename = f"image_{self._counter}.{ext}"

        self._images_dir.mkdir(parents=True, exist_ok=True)
        try:
            with image.open() as image_bytes:
                data = image_bytes.read()
        except Exception as e:
            # Some authoring tools emit image relationships pointing at parts
            # that don't exist in the archive (Target="../NULL"); mammoth
            # surfaces that as KeyError from zipfile.  One broken image must
            # not fail the whole conversion: substitute a 1x1 placeholder so
            # markdown refs, files on disk and the images list stay consistent.
            logger.warning(
                "DOCX image %s could not be read (%s); substituting 1x1 placeholder",
                filename,
                e,
            )
            self.failed_images += 1
            filename = f"image_{self._counter}.png"
            data = _PLACEHOLDER_PNG

        dest = self._images_dir / filename
        dest.write_bytes(data)

        self.images.append(ImageInfo(filename=filename, data=data))
        self.alt_by_filename[filename] = alt_text
        # mammoth's img_element lets handler attrs override its own alt, and
        # markdownify copies the attribute verbatim — emitting the escaped alt
        # here covers every whitelisted-parent ref in one place (see
        # _escape_alt_text).
        return {
            "src": f"{self._images_dir_name}/{filename}",
            "alt": _escape_alt_text(alt_text),
        }


def _escaped_pattern(text: str) -> str:
    """Regex matching ``text`` whether or not markdownify escaped its specials."""
    return "".join(r"\\?" + re.escape(ch) for ch in text)


def _escape_alt_text(alt: str) -> str:
    """Escape alt text so the emitted ``![alt](src)`` ref stays one ref.

    markdownify passes the HTML alt attribute through **verbatim** (convert_img
    does no markdown escaping), so a raw ``[``/``]`` from the document author's
    ``wp:docPr/@descr`` lands unescaped inside the alt — IMAGE_REF_RE then
    truncates at the first ``]`` and every consumer (evidence hashing, agent
    image lookup, frontend preview rewrite) mis-parses the ref (2026-10-21
    finding; 2.2% of production stock affected).  Backslash-escape brackets
    per CommonMark; newlines would break the single-line ref and collapse to
    spaces.
    """
    alt = alt.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return re.sub(r"[\r\n]+", " ", alt)


def _repair_dropped_image_refs(
    markdown: str, handler: DirectFileImageHandler, images_dir_name: str
) -> str:
    """Return markdown in which every materialized image is referenced.

    markdownify's ``keep_inline_images_in`` check looks only at the <img>'s
    direct parent tag, so an image inside any non-whitelisted wrapper loses
    its ``![alt](path)`` ref while the file stays on disk (see
    _KEEP_INLINE_IMAGES_IN).  That silently removed review evidence once
    (2026-10-19 incident); never let it happen silently again:

    * collect materialized images the markdown no longer references;
    * when the surviving alt text matches exactly one position, splice the
      ref back in place, restoring positional context for the review agent;
    * otherwise append a trailing repair section so the image stays visible;
    * always log a warning so the drop is observable in parse logs.
    """
    unreferenced = [
        img.filename
        for img in handler.images
        if f"{images_dir_name}/{img.filename}" not in markdown
    ]
    if not unreferenced:
        return markdown

    unrepaired: list[str] = []
    for filename in unreferenced:
        alt = handler.alt_by_filename.get(filename, "")
        ref = f"![{_escape_alt_text(alt)}]({images_dir_name}/{filename})"
        repaired = False
        if alt:
            pattern = _escaped_pattern(alt)
            matches = list(re.finditer(pattern, markdown))
            # exactly one hit, and it is not the alt of an already-present ref
            if len(matches) == 1 and markdown[matches[0].start() - 2 : matches[0].start()] != "![":
                markdown = re.sub(pattern, lambda _m, _ref=ref: _ref, markdown, count=1)
                repaired = True
        if not repaired:
            unrepaired.append(filename)

    logger.warning(
        "DOCX conversion dropped %s inline image ref(s) (non-whitelisted parent "
        "tag); %s spliced back in place, %s appended in repair section: %s",
        len(unreferenced),
        len(unreferenced) - len(unrepaired),
        len(unrepaired),
        ", ".join(unreferenced),
    )

    if unrepaired:
        lines = [
            "",
            "> ⚠️ 解析兜底：以下 "
            f"{len(unrepaired)} 张嵌入图片位于特殊格式段落，正文原位引用在转换中丢失，"
            "现补录于此（图片文件均已正常提取，内容真实存在于原文档，审查时须一并进行核验）：",
            ">",
        ]
        for filename in unrepaired:
            alt = handler.alt_by_filename.get(filename, "")
            lines.append(f"> ![{_escape_alt_text(alt)}]({images_dir_name}/{filename})")
        markdown += "\n" + "\n".join(lines)
    return markdown


# ---- DOCX 盲区结构探测与廉价恢复（2026-10-21 评估，见 doc/workspace/21 号 §八）----
#
# mammoth 只读 document.xml 正文：SmartArt/chart 的 graphicData 分支不产生
# "unrecognised element" warning（零可观测丢弃）、mc:Choice-only 形状整块消失、
# 页眉/页脚 part 完全不读、m:oMath 丢弃仅留日志 warning。这些结构里的审查
# 材料会"无声消失"，agent 只能误判「未提供」。生产存量实测频率（1915 份）：
# SmartArt 22、页眉图 291、公式 15、Choice-only 2、chart 0。
# 对策：字节级探测 + md 文末标注（agent 据此改判「待确认」而非「未提供」），
# 并对 SmartArt/公式文字、页眉图片做有上限的廉价恢复。

_BLIND_SPOT_MAX_ITEMS = 120
_BLIND_SPOT_MAX_CHARS = 2000
_BLIND_SPOT_MAX_MATH = 60
_BLIND_SPOT_MAX_HEADER_IMAGES = 20

# graphicData 的 uri 属性值要整串锚定：只匹配子串会把根元素上的
# xmlns 命名空间声明（如 xmlns:c=".../chart"）也计入，造成误报。
_DIAGRAM_URI_RE = re.compile(
    rb'uri="http://schemas\.openxmlformats\.org/drawingml/2006/diagram"'
)
_CHART_URI_RE = re.compile(
    rb'uri="http://schemas\.openxmlformats\.org/drawingml/2006/chart"'
)

_NOTE_HEADER = (
    "> ⚠️ 解析覆盖提示：原文档含有以下当前解析器不能完整提取的结构。"
    "审查时若涉及对应材料，请标记「待确认（需人工核验原文档）」，"
    "不得仅因解析产物中缺失而判定「未提供」："
)


def _xml_unescape(text: str) -> str:
    for entity, char in (
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&apos;", "'"),
        ("&amp;", "&"),
    ):
        text = text.replace(entity, char)
    return text


def _collect_xml_texts(
    xml_bytes: bytes, tag: str, limit_items: int, limit_chars: int
) -> tuple[list[str], int]:
    """Extract the texts of ``tag`` elements (a:t of SmartArt, m:t of formulas)."""
    pattern = re.compile(
        rf"<{re.escape(tag)}(?:\s[^>]*)?>(.*?)</{re.escape(tag)}\s*>".encode(), re.S
    )
    texts: list[str] = []
    total_chars = 0
    omitted = 0
    for match in pattern.finditer(xml_bytes):
        text = _xml_unescape(match.group(1).decode("utf-8", "replace")).strip()
        if not text:
            continue
        if len(texts) >= limit_items or total_chars + len(text) > limit_chars:
            omitted += 1
            continue
        texts.append(text)
        total_chars += len(text)
    return texts, omitted


def _format_recovered_texts(texts: list[str], omitted: int) -> str:
    joined = "、".join(texts)
    if omitted:
        joined += f"…（其余 {omitted} 条省略）"
    return joined


def _recover_part_images(
    zf,
    part_name: str,
    images_dir: Path,
    images_dir_name: str,
    images: list[ImageInfo],
    used_targets: set[str],
) -> tuple[list[str], int]:
    """Materialize images referenced by a header/footer part.

    mammoth never reads those parts, so their images (company letterheads and,
    in some templates, seal scans) vanish without any trace.  Resolve the
    part's image relationships directly and write the media bytes out.
    """
    import posixpath

    rels_name = "word/_rels/" + part_name.split("/", 1)[1] + ".rels"
    try:
        rels_xml = zf.read(rels_name)
    except KeyError:
        return [], 0

    image_targets: dict[str, str] = {}
    for element in re.finditer(rb"<Relationship\b[^>]*>", rels_xml):
        element_xml = element.group(0)
        rid = re.search(rb'Id="([^"]+)"', element_xml)
        rel_type = re.search(rb'Type="([^"]+)"', element_xml)
        target = re.search(rb'Target="([^"]+)"', element_xml)
        if rid and rel_type and target and rel_type.group(1).endswith(b"/image"):
            image_targets[rid.group(1).decode()] = target.group(1).decode()

    try:
        content = zf.read(part_name)
    except KeyError:
        return [], 0
    referenced_rids = set()
    for attr in (b"r:embed", b"r:link", b"r:id"):
        for match in re.finditer(attr + rb'="([^"]+)"', content):
            referenced_rids.add(match.group(1).decode())

    refs: list[str] = []
    skipped = 0
    for rid in referenced_rids:
        target = image_targets.get(rid)
        if not target:
            continue
        part_target = posixpath.normpath(posixpath.join("word", target))
        if part_target in used_targets:
            continue
        if len(used_targets) >= _BLIND_SPOT_MAX_HEADER_IMAGES:
            skipped += 1
            continue
        try:
            data = zf.read(part_target)
        except KeyError:
            continue
        ext = target.rsplit(".", 1)[-1].lower() if "." in target.rsplit("/", 1)[-1] else "png"
        filename = f"headerimg_{len(used_targets) + 1}.{ext}"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / filename).write_bytes(data)
        used_targets.add(part_target)
        images.append(ImageInfo(filename=filename, data=data))
        refs.append(f"![页眉/页脚图片 {filename}]({images_dir_name}/{filename})")
    return refs, skipped


def _scan_docx_blind_spots(
    zf, images_dir: Path, images_dir_name: str, images: list[ImageInfo]
) -> tuple[str, dict]:
    """Detect mammoth-unreadable structures; recover text/images where cheap.

    Returns ``(markdown_notes, quality_summary)`` — byte-level scans only, no
    XML parse (deterministic on real-world files; validated against the
    production stock scan of 2026-10-21).
    """
    try:
        document_xml = zf.read("word/document.xml")
    except KeyError:
        return "", {}

    lines: list[str] = []
    summary: dict[str, int] = {}

    smartart = len(_DIAGRAM_URI_RE.findall(document_xml))
    chart = len(_CHART_URI_RE.findall(document_xml))
    choice_only = document_xml.count(b"<mc:AlternateContent") - document_xml.count(
        b"<mc:Fallback"
    )
    math_count = document_xml.count(b"<m:oMath")

    if smartart:
        texts: list[str] = []
        omitted = 0
        for name in zf.namelist():
            if name.startswith("word/diagrams/") and name.rsplit("/", 1)[-1].startswith("data"):
                part_texts, part_omitted = _collect_xml_texts(
                    zf.read(name), "a:t", _BLIND_SPOT_MAX_ITEMS, _BLIND_SPOT_MAX_CHARS
                )
                texts.extend(part_texts)
                omitted += part_omitted
        seen: set[str] = set()
        texts = [t for t in texts if not (t in seen or seen.add(t))]
        entry = f"SmartArt 图示 {smartart} 处"
        if texts:
            entry += f"（图内文字已补录、结构丢失：{_format_recovered_texts(texts, omitted)}）"
        else:
            entry += "（内容未能提取）"
        lines.append(entry)
        summary["smartart"] = smartart

    if chart:
        lines.append(f"图表对象 {chart} 处（图表数据未能提取，如涉及报价/业绩数据请人工核验原文档）")
        summary["chart"] = chart

    if choice_only > 0:
        lines.append(
            f"仅现代格式(Choice)的形状/文本框 {choice_only} 处"
            "（该部分文字或图片未能提取）"
        )
        summary["choice_only_shapes"] = choice_only

    if math_count:
        texts, omitted = _collect_xml_texts(
            document_xml, "m:t", _BLIND_SPOT_MAX_MATH, _BLIND_SPOT_MAX_CHARS
        )
        entry = f"数学公式 {math_count} 处"
        if texts:
            entry += f"（公式文字已补录、排版丢失：{_format_recovered_texts(texts, omitted)}）"
        else:
            entry += "（内容未能提取）"
        lines.append(entry)
        summary["math"] = math_count

    recovered_refs: list[str] = []
    part_names = [
        name
        for name in zf.namelist()
        if re.match(r"word/(?:header|footer)\d*\.xml$", name)
    ]
    if part_names:
        used_targets: set[str] = set()
        skipped = 0
        for part_name in part_names:
            refs, part_skipped = _recover_part_images(
                zf, part_name, images_dir, images_dir_name, images, used_targets
            )
            recovered_refs.extend(refs)
            skipped += part_skipped
        if recovered_refs or skipped:
            entry = f"页眉/页脚图片 {len(recovered_refs)} 张（已提取，引用见文末）"
            if skipped:
                entry += f"，另有 {skipped} 张超出提取上限未提取"
            lines.append(entry)
            summary["header_footer_images"] = len(recovered_refs) + skipped

    if not lines:
        return "", summary

    note_lines = ["", _NOTE_HEADER, ">"]
    note_lines.extend(f"> - {line}" for line in lines)
    markdown_notes = "\n".join(note_lines)
    if recovered_refs:
        markdown_notes += "\n\n" + "\n\n".join(recovered_refs)
    logger.warning("DOCX blind spots detected: %s", "; ".join(lines))
    return markdown_notes, summary


class MarkitdownConverter:
    """Markitdown converter for DOCX/DOC/PDF/XLSX files to Markdown format.

    Uses the markitdown library to extract text and images from documents.
    For DOCX/DOC, images are written directly to disk via a custom mammoth image handler.
    For PDF, uses markitdown's built-in PdfConverter for text/tables and PyMuPDF for images.
    For XLSX, converts each visible worksheet to a Markdown table via openpyxl
    (cached formula values; row/column caps; embedded images materialized like DOCX).
    """

    def __init__(
        self,
        timeout: int = 300,
        xlsx_max_rows: Optional[int] = None,
        xlsx_max_cols: Optional[int] = None,
    ):
        self.timeout = timeout
        self.xlsx_max_rows = xlsx_max_rows if xlsx_max_rows is not None else XLSX_MAX_ROWS_PER_SHEET
        self.xlsx_max_cols = xlsx_max_cols if xlsx_max_cols is not None else XLSX_MAX_COLS_PER_SHEET

    def convert(self, file_path: Path, progress_callback=None, images_dir: Path = None) -> ConversionResult:
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在，请重新上传：{file_path}")

        suffix = file_path.suffix.lower()
        if suffix not in [".docx", ".doc", ".pdf", ".xlsx"]:
            raise ValueError(f"暂不支持 {suffix or '未知'} 格式，请上传 DOCX、DOC、PDF 或 XLSX 文件")

        file_size = file_path.stat().st_size
        logger.info(f"Markitdown conversion: {file_path} ({file_size / (1024 * 1024):.2f}MB)")

        if images_dir is None:
            images_dir = file_path.parent / f"{file_path.stem}_images"

        try:
            if suffix in [".docx", ".doc"]:
                return self._convert_docx(file_path, images_dir, progress_callback)
            elif suffix == ".xlsx":
                return self._convert_xlsx(file_path, images_dir, progress_callback)
            else:
                return self._convert_pdf(file_path, images_dir, progress_callback)

        except MarkitdownConversionError:
            raise
        except Exception as e:
            logger.error(f"Markitdown conversion failed: {e}")
            raise MarkitdownConversionError(f"文档转换失败：{e}")

    def _convert_docx(self, file_path: Path, images_dir: Path, progress_callback) -> ConversionResult:
        """Convert DOCX/DOC while materializing every embedded image.

        MarkItDown's DOCX converter accepts arbitrary keyword arguments but its
        current implementation does not forward ``convert_image`` to Mammoth.
        Calling it therefore produces ``data:image/...;base64`` Markdown and
        leaves the image directory empty.  Invoke Mammoth directly, then apply
        the same HTML-to-Markdown stage so image references remain lightweight
        paths backed by real files.
        """
        import mammoth
        from markdownify import markdownify

        images_dir_name = images_dir.name
        handler = DirectFileImageHandler(images_dir, images_dir_name)
        with file_path.open("rb") as source:
            html_result = mammoth.convert_to_html(
                source,
                convert_image=mammoth.images.img_element(handler),
            )
        for message in html_result.messages:
            logger.warning("Mammoth DOCX conversion warning: %s", message.message)
        markdown_content = markdownify(
            html_result.value,
            heading_style="ATX",
            bullets="-",
            keep_inline_images_in=_KEEP_INLINE_IMAGES_IN,
        ).strip()
        dropped_before_repair = sum(
            1
            for img in handler.images
            if f"{images_dir_name}/{img.filename}" not in markdown_content
        )
        markdown_content = _repair_dropped_image_refs(
            markdown_content, handler, images_dir_name
        )

        # 盲区结构探测与廉价恢复；非 zip（老 .doc 在 mammoth 阶段会响亮失败）
        # 或无盲区时返回空串/空 dict，零影响。
        blind_spot_notes, blind_spot_summary = "", {}
        try:
            import zipfile

            with zipfile.ZipFile(file_path) as zf:
                blind_spot_notes, blind_spot_summary = _scan_docx_blind_spots(
                    zf, images_dir, images_dir_name, handler.images
                )
        except zipfile.BadZipFile:
            pass
        if blind_spot_notes:
            markdown_content += "\n" + blind_spot_notes

        if progress_callback:
            progress_callback(1, 1)

        logger.info(
            "DOCX conversion successful: %s chars, %s materialized images",
            len(markdown_content),
            len(handler.images),
        )
        if handler.failed_images:
            logger.warning(
                "DOCX contained %s unreadable image(s); substituted 1x1 placeholder(s)",
                handler.failed_images,
            )

        return ConversionResult(
            markdown_content=markdown_content,
            images=handler.images,
            page_count=None,
            parse_quality={
                "mammoth_warnings": len(html_result.messages),
                "images_total": len(handler.images),
                "images_failed_placeholder": handler.failed_images,
                "dropped_refs_repaired": dropped_before_repair,
                "blind_spots": blind_spot_summary,
            },
        )

    def _convert_xlsx(self, file_path: Path, images_dir: Path, progress_callback) -> ConversionResult:
        """Convert XLSX to Markdown: one section per visible worksheet.

        - data_only=True reads cached formula results; a formula whose value was
          never cached by Excel reads back as an empty cell.
        - Output per sheet is capped at xlsx_max_rows data rows / xlsx_max_cols
          columns; omitted ranges are flagged inline so the review agent knows
          coverage is partial (review consumes the full markdown, unchunked).
        - Embedded images are materialized to images_dir under the same contract
          as DOCX — certificate screenshots inside spreadsheets are review
          evidence (see the 2026-08-18 missing-table-images incident).  Chart
          objects cannot be rendered without Excel and are only flagged.
        """
        import zipfile

        from openpyxl import load_workbook

        try:
            workbook = load_workbook(filename=str(file_path), data_only=True)
        except zipfile.BadZipFile as exc:
            # 密码保护的 xlsx 是 OLE2 加密容器而非 zip，openpyxl 同样报 BadZipFile
            raise MarkitdownConversionError(
                "Excel 文件无法读取（文件已损坏或为加密文件），请解除密码后另存为 .xlsx 重新上传"
            ) from exc

        images: list[ImageInfo] = []
        sections: list[str] = []
        hidden_sheets: list[str] = []
        sheet_names = list(workbook.sheetnames)

        for sheet_index, sheet_name in enumerate(sheet_names, start=1):
            worksheet = workbook[sheet_name]
            if worksheet.sheet_state != "visible":
                hidden_sheets.append(str(sheet_name))
                continue
            if progress_callback:
                progress_callback(sheet_index, len(sheet_names))
            sections.append(
                self._xlsx_sheet_to_markdown(worksheet, sheet_index, images_dir, images)
            )

        workbook.close()

        markdown_parts = sections or ["（Excel 文件中没有可见工作表）"]
        if hidden_sheets:
            markdown_parts.append(
                f"> 注：已跳过 {len(hidden_sheets)} 个隐藏工作表（{'、'.join(hidden_sheets)}）"
            )
        markdown_content = "\n\n".join(markdown_parts)

        if progress_callback:
            progress_callback(len(sheet_names), len(sheet_names))

        logger.info(
            "XLSX conversion successful: %s chars, %s materialized images, %s visible sheets",
            len(markdown_content),
            len(images),
            len(sections),
        )

        return ConversionResult(
            markdown_content=markdown_content,
            images=images,
            page_count=None,
        )

    def _xlsx_sheet_to_markdown(
        self,
        worksheet,
        sheet_index: int,
        images_dir: Path,
        images: list[ImageInfo],
    ) -> str:
        from openpyxl.utils import get_column_letter

        max_row = worksheet.max_row or 0
        max_col = worksheet.max_column or 0
        effective_cols = min(max_col, self.xlsx_max_cols)
        col_truncated = max_col > self.xlsx_max_cols

        table_lines: list[str] = []
        if effective_cols > 0:
            table_lines.append(
                "| 行号 | "
                + " | ".join(
                    get_column_letter(col) for col in range(1, effective_cols + 1)
                )
                + " |"
            )
            table_lines.append(
                "| " + " | ".join(["---"] * (effective_cols + 1)) + " |"
            )

        kept_rows = 0
        rows_truncated = False
        if effective_cols > 0:
            row_iterator = worksheet.iter_rows(
                min_row=1,
                max_row=max_row,
                max_col=effective_cols,
                values_only=True,
            )
            for row_index, row_values in enumerate(row_iterator, start=1):
                if _xlsx_is_empty_row(row_values):
                    # 空行不占用行数上限也不输出：格式化撑大的已用区域里空行居多
                    continue
                if kept_rows >= self.xlsx_max_rows:
                    rows_truncated = True
                    break
                kept_rows += 1
                table_lines.append(
                    "| "
                    + " | ".join([str(row_index)] + [_xlsx_cell_text(v) for v in row_values])
                    + " |"
                )

        notes: list[str] = []
        if col_truncated:
            notes.append(
                f"> ⚠️ 该工作表共 {max_col} 列，超过列数上限 {self.xlsx_max_cols}，右侧列已省略"
            )
        if rows_truncated:
            notes.append(
                f"> ⚠️ 该工作表数据行数超过行数上限 {self.xlsx_max_rows}，"
                f"仅保留前 {kept_rows} 个非空数据行，其余已省略"
            )
        chart_count = len(getattr(worksheet, "_charts", []) or [])
        if chart_count:
            notes.append(
                f"> ⚠️ 该工作表含 {chart_count} 个图表对象，当前版本未提取图表图像"
            )

        image_refs = self._extract_xlsx_images(worksheet, sheet_index, images_dir, images)

        parts = [f"## {worksheet.title}"]
        if kept_rows:
            # 表格行必须连续（空行会终止 GFM 表格），整表作为一个块参与分节 join
            parts.append("\n".join(table_lines))
        else:
            parts.append("（空工作表）")
        parts.extend(notes)
        parts.extend(image_refs)
        return "\n\n".join(parts)

    @staticmethod
    def _extract_xlsx_images(
        worksheet,
        sheet_index: int,
        images_dir: Path,
        images: list[ImageInfo],
    ) -> list[str]:
        """Materialize embedded worksheet images to disk (DOCX-equivalent contract).

        openpyxl only populates ws._images when Pillow is available; exotic
        formats are re-encoded by Image._data().  One broken image must not
        fail the sheet conversion.
        """
        image_refs: list[str] = []
        for image_index, drawing_image in enumerate(
            getattr(worksheet, "_images", []) or [], start=1
        ):
            try:
                data = drawing_image._data()
                image_format = str(getattr(drawing_image, "format", None) or "png").lower()
                ext = {"jpeg": "jpg", "jpg": "jpg"}.get(image_format, image_format)
                filename = f"sheet{sheet_index}_img{image_index}.{ext}"
                images_dir.mkdir(parents=True, exist_ok=True)
                (images_dir / filename).write_bytes(data)
                images.append(ImageInfo(filename=filename, data=data))
                row_hint = ""
                anchor_from = getattr(getattr(drawing_image, "anchor", None), "_from", None)
                if anchor_from is not None and getattr(anchor_from, "row", None) is not None:
                    row_hint = f"（约第 {anchor_from.row + 1} 行）"
                image_refs.append(
                    f"![工作表内嵌图片 sheet{sheet_index}_img{image_index} {row_hint}]"
                    f"({images_dir.name}/{filename})"
                )
            except Exception as exc:
                logger.warning(
                    "XLSX image %s of sheet %s could not be read: %s",
                    image_index,
                    sheet_index,
                    exc,
                )
        return image_refs

    def _convert_pdf(self, file_path: Path, images_dir: Path, progress_callback=None) -> ConversionResult:
        """Convert PDF file to Markdown using PyMuPDF page-by-page extraction.

        Extracts text and images per page, inserting image references inline
        after each page's text content.
        """
        import fitz

        images_dir.mkdir(parents=True, exist_ok=True)
        images_dir_name = images_dir.name

        page_parts: list[str] = []
        all_images: list[ImageInfo] = []

        doc = fitz.open(str(file_path))
        if doc.is_encrypted:
            doc.close()
            raise MarkitdownConversionError("PDF 文件已加密，请解除密码后重新上传")

        total_pages = len(doc)
        logger.info(f"PDF fitz conversion: {file_path.name}, {total_pages} pages")

        for page_num in range(total_pages):
            page = doc[page_num]
            page_text = page.get_text().strip()
            page_image_refs: list[str] = []

            try:
                for img_index, img in enumerate(page.get_images()):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        if len(image_bytes) > 10 * 1024 * 1024:
                            continue
                        filename = f"page_{page_num + 1}_img_{img_index + 1}.{image_ext}"
                        (images_dir / filename).write_bytes(image_bytes)
                        all_images.append(ImageInfo(filename=filename, data=image_bytes))
                        page_image_refs.append(f"![{filename}]({images_dir_name}/{filename})")
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num + 1}: {e}")
            except Exception as e:
                logger.warning(f"Failed to get images from page {page_num + 1}: {e}")

            if page_text:
                page_parts.append(page_text)
            page_parts.extend(page_image_refs)

            if progress_callback:
                progress_callback(page_num + 1, total_pages)

        doc.close()

        markdown_content = "\n\n".join(page_parts)
        logger.info(f"PDF fitz conversion result: {len(markdown_content)} chars, {len(all_images)} images, {total_pages} pages")

        return ConversionResult(
            markdown_content=markdown_content,
            images=all_images,
            page_count=total_pages,
        )


def convert_to_markdown(file_path: Path) -> ConversionResult:
    converter = MarkitdownConverter()
    return converter.convert(file_path)


def _extract_pdf_text_with_fitz(file_path: Path) -> str:
    """Extract text from PDF using PyMuPDF as a fallback when markitdown fails."""
    import fitz

    text_parts = []
    try:
        doc = fitz.open(str(file_path))
        if doc.is_encrypted:
            doc.close()
            raise ValueError("PDF 文件已加密，请解除密码后重新上传")

        max_pages = 500
        for page_num in range(min(len(doc), max_pages)):
            page = doc[page_num]
            try:
                text = page.get_text()
                if text and text.strip():
                    text_parts.append(text.strip())
            except Exception as e:
                logger.warning(f"Failed to extract text from page {page_num + 1}: {e}")
        doc.close()
    except Exception as e:
        logger.error(f"PyMuPDF text extraction failed for {file_path}: {e}")
        raise

    return "\n\n".join(text_parts)


def _extract_pdf_images(file_path: Path, images_dir: Path) -> list[ImageInfo]:
    """Extract embedded images from PDF using PyMuPDF."""
    import fitz

    images: list[ImageInfo] = []

    try:
        doc = fitz.open(str(file_path))
        if doc.is_encrypted:
            logger.warning(f"PDF is encrypted, skipping image extraction: {file_path}")
            doc.close()
            return images

        max_pages = 500
        for page_num in range(min(len(doc), max_pages)):
            page = doc[page_num]
            try:
                for img_index, img in enumerate(page.get_images()):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        if len(image_bytes) > 10 * 1024 * 1024:
                            continue
                        filename = f"page_{page_num + 1}_img_{img_index + 1}.{image_ext}"
                        images_dir.mkdir(parents=True, exist_ok=True)
                        (images_dir / filename).write_bytes(image_bytes)
                        images.append(ImageInfo(filename=filename, data=image_bytes))
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num + 1}: {e}")
            except Exception as e:
                logger.warning(f"Failed to get images from page {page_num + 1}: {e}")

        doc.close()
    except Exception as e:
        logger.warning(f"PDF image extraction failed for {file_path}: {e}")

    return images


def _get_pdf_page_count(file_path: Path) -> Optional[int]:
    """Get PDF page count using PyMuPDF."""
    import fitz

    try:
        doc = fitz.open(str(file_path))
        count = len(doc)
        doc.close()
        return count
    except Exception as e:
        logger.warning(f"Failed to get PDF page count: {e}")
        return None
