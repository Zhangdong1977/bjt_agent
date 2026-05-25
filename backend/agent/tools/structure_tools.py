"""Structured document tools for bid review agent.

Provides 4 tools for structured document access:
- get_document_toc: Document table of contents
- get_section_content: Section text content
- get_section_images: Images under a section
- get_image_ocr: OCR recognition of an image
"""

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx

from backend.agent.tools.base import ToolResult
from mini_agent.tools.base import Tool as BaseTool

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class SectionInfo:
    section_id: str
    title: str
    level: int
    start_line: int
    end_line: int = 0
    page_no: Optional[int] = None
    children_count: int = 0


class StructureDataLoader:
    """Loads and caches document structure data from parsed Markdown."""

    def __init__(self, parsed_md_path: str):
        self.parsed_md_path = Path(parsed_md_path)
        self._markdown_content: Optional[str] = None
        self._markdown_lines: Optional[list[str]] = None
        self._sections: Optional[list[SectionInfo]] = None

    @property
    def markdown_content(self) -> str:
        if self._markdown_content is None:
            if not self.parsed_md_path.exists():
                raise FileNotFoundError(f"Parsed markdown not found: {self.parsed_md_path}")
            self._markdown_content = self.parsed_md_path.read_text(encoding="utf-8")
        return self._markdown_content

    @property
    def markdown_lines(self) -> list[str]:
        if self._markdown_lines is None:
            self._markdown_lines = self.markdown_content.split("\n")
        return self._markdown_lines

    def get_toc(self) -> list[SectionInfo]:
        if self._sections is not None:
            return self._sections

        lines = self.markdown_lines
        sections: list[SectionInfo] = []

        for i, line in enumerate(lines):
            match = _HEADING_RE.match(line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                sections.append(SectionInfo(
                    section_id=f"s{len(sections) + 1}",
                    title=title,
                    level=level,
                    start_line=i + 1,
                ))

        for idx, sec in enumerate(sections):
            sec.end_line = sections[idx + 1].start_line - 1 if idx + 1 < len(sections) else len(lines)

        for idx, sec in enumerate(sections):
            sec.children_count = sum(
                1 for j in range(idx + 1, len(sections))
                if sections[j].level > sec.level
                and all(sections[k].level > sec.level for k in range(idx + 1, j + 1))
            )
            # Simpler: count immediate children only
            count = 0
            for j in range(idx + 1, len(sections)):
                if sections[j].level > sec.level:
                    count += 1
                else:
                    break
            sec.children_count = count

        self._sections = sections
        return sections

    def get_section_content(self, section_id: str, include_subsections: bool = True) -> Optional[str]:
        sections = self.get_toc()
        target = None
        for sec in sections:
            if sec.section_id == section_id:
                target = sec
                break

        if target is None:
            return None

        lines = self.markdown_lines
        start = target.start_line - 1
        end = target.end_line

        if not include_subsections:
            for sec in sections:
                if sec.start_line > target.start_line and sec.level <= target.level:
                    end = sec.start_line - 1
                    break

        content_lines = lines[start:end]
        if content_lines and _HEADING_RE.match(content_lines[0]):
            content_lines = content_lines[1:]

        return "\n".join(content_lines).strip()

    def get_section_images(self, section_id: str) -> list[dict]:
        content = self.get_section_content(section_id, include_subsections=True)
        if not content:
            return []

        images = []
        for match in _IMAGE_REF_RE.finditer(content):
            images.append({
                "image_id": f"img_{len(images) + 1}",
                "alt_text": match.group(1),
                "filename": Path(match.group(2)).name,
                "path": match.group(2),
            })
        return images

    def get_image_full_path(self, relative_path: str) -> Path:
        return self.parsed_md_path.parent / relative_path


def _create_shared_loaders(tender_md_path: str, bid_md_path: str) -> dict[str, StructureDataLoader]:
    return {
        "tender": StructureDataLoader(tender_md_path),
        "bid": StructureDataLoader(bid_md_path),
    }


class DocumentTocTool(BaseTool):
    """获取招标书或应标书的章节目录结构。"""

    def __init__(self, loaders: dict[str, StructureDataLoader]):
        self._loaders = loaders

    @property
    def name(self) -> str:
        return "get_document_toc"

    @property
    def description(self) -> str:
        return """【文档目录工具】获取招标书或应标书的章节目录结构，返回所有标题及其层级关系。

建议审核流程的第一步先调用此工具了解文档结构，然后再按章节逐步审查。

参数说明：
- "doc_type": "tender"（招标书）或 "bid"（应标书），必填

返回：章节列表，包含章节ID、标题、层级、子章节数量。"""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["tender", "bid"],
                    "description": "文档类型：tender=招标书，bid=应标书",
                },
            },
            "required": ["doc_type"],
        }

    async def execute(self, doc_type: str = None, **kwargs) -> ToolResult:
        if not doc_type:
            return ToolResult(success=False, content="", error="缺少参数 doc_type")

        try:
            loader = self._loaders.get(doc_type)
            if not loader:
                return ToolResult(success=False, content="", error=f"未知文档类型: {doc_type}")

            sections = loader.get_toc()
            if not sections:
                doc_label = "招标书" if doc_type == "tender" else "应标书"
                return ToolResult(success=True, content=f"{doc_label}无章节标题结构", data={"sections": []})

            doc_label = "招标书" if doc_type == "tender" else "应标书"
            toc_lines = [f"{doc_label} 目录结构（共 {len(sections)} 个章节）：\n"]

            for sec in sections:
                indent = "  " * (sec.level - 1)
                children_note = f" ({sec.children_count} 个子章节)" if sec.children_count > 0 else ""
                toc_lines.append(f"{indent}* [{sec.section_id}] {'#' * sec.level} {sec.title}{children_note}")

            return ToolResult(
                success=True,
                content="\n".join(toc_lines),
                data={
                    "sections": [
                        {"section_id": s.section_id, "title": s.title, "level": s.level, "children_count": s.children_count}
                        for s in sections
                    ],
                },
            )

        except Exception as e:
            logger.error(f"DocumentTocTool error: {e}")
            return ToolResult(success=False, content="", error=str(e))


class SectionContentTool(BaseTool):
    """获取指定章节的完整内容。"""

    def __init__(self, loaders: dict[str, StructureDataLoader]):
        self._loaders = loaders

    @property
    def name(self) -> str:
        return "get_section_content"

    @property
    def description(self) -> str:
        return """【章节内容工具】获取指定章节的完整内容，包括文本、表格、列表等。

使用 get_document_toc 获取章节ID后，用此工具读取具体章节内容进行审查。

参数说明：
- "doc_type": "tender"（招标书）或 "bid"（应标书），必填
- "section_id": 章节ID（从 get_document_toc 获取），必填
- "include_subsections": 是否包含子章节内容，默认true

返回：章节标题 + 完整文本内容（含表格和图片引用）。"""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["tender", "bid"],
                    "description": "文档类型：tender=招标书，bid=应标书",
                },
                "section_id": {
                    "type": "string",
                    "description": "章节ID（从 get_document_toc 返回的 section_id 字段获取）",
                },
                "include_subsections": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否包含子章节内容，默认true",
                },
            },
            "required": ["doc_type", "section_id"],
        }

    async def execute(
        self,
        doc_type: str = None,
        section_id: str = None,
        include_subsections: bool = True,
        **kwargs,
    ) -> ToolResult:
        if not doc_type or not section_id:
            return ToolResult(success=False, content="", error="缺少参数 doc_type 或 section_id")

        try:
            loader = self._loaders.get(doc_type)
            if not loader:
                return ToolResult(success=False, content="", error=f"未知文档类型: {doc_type}")

            sections = loader.get_toc()
            section_title = ""
            for sec in sections:
                if sec.section_id == section_id:
                    section_title = sec.title
                    break

            content = loader.get_section_content(section_id, include_subsections)
            if content is None:
                return ToolResult(success=False, content="", error=f"未找到章节: {section_id}")

            if not content.strip():
                return ToolResult(
                    success=True,
                    content=f"章节 [{section_id}] {section_title} 无文本内容",
                    data={"section_id": section_id, "content": ""},
                )

            doc_label = "招标书" if doc_type == "tender" else "应标书"
            header = f"{doc_label} -- [{section_id}] {section_title}"
            if not include_subsections:
                header += "（不含子章节）"

            return ToolResult(
                success=True,
                content=f"{header}\n\n{content}",
                data={
                    "section_id": section_id,
                    "section_title": section_title,
                    "content": content,
                    "include_subsections": include_subsections,
                },
            )

        except Exception as e:
            logger.error(f"SectionContentTool error: {e}")
            return ToolResult(success=False, content="", error=str(e))


class SectionImagesTool(BaseTool):
    """获取指定章节下的所有图片列表。"""

    def __init__(self, loaders: dict[str, StructureDataLoader]):
        self._loaders = loaders

    @property
    def name(self) -> str:
        return "get_section_images"

    @property
    def description(self) -> str:
        return """【章节图片工具】获取指定章节下的所有图片列表。

在审查资质证明、技术方案等需要图片材料的内容时使用。获取图片列表后，
可使用 get_image_ocr 工具对具体图片进行OCR文字识别。

参数说明：
- "doc_type": "tender"（招标书）或 "bid"（应标书），必填
- "section_id": 章节ID（从 get_document_toc 获取），必填

返回：图片列表，包含图片ID、文件名、路径。"""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["tender", "bid"],
                    "description": "文档类型：tender=招标书，bid=应标书",
                },
                "section_id": {
                    "type": "string",
                    "description": "章节ID",
                },
            },
            "required": ["doc_type", "section_id"],
        }

    async def execute(self, doc_type: str = None, section_id: str = None, **kwargs) -> ToolResult:
        if not doc_type or not section_id:
            return ToolResult(success=False, content="", error="缺少参数 doc_type 或 section_id")

        try:
            loader = self._loaders.get(doc_type)
            if not loader:
                return ToolResult(success=False, content="", error=f"未知文档类型: {doc_type}")

            images = loader.get_section_images(section_id)
            doc_label = "招标书" if doc_type == "tender" else "应标书"
            if not images:
                return ToolResult(
                    success=True,
                    content=f"{doc_label}章节 [{section_id}] 下无图片",
                    data={"section_id": section_id, "images": []},
                )

            lines = [f"{doc_label}章节 [{section_id}] 下有 {len(images)} 张图片：\n"]
            for img in images:
                lines.append(f"  * [{img['image_id']}] {img['filename']}")

            return ToolResult(
                success=True,
                content="\n".join(lines),
                data={"section_id": section_id, "images": images},
            )

        except Exception as e:
            logger.error(f"SectionImagesTool error: {e}")
            return ToolResult(success=False, content="", error=str(e))


class ImageOcrTool(BaseTool):
    """对文档中的指定图片进行 OCR 文字识别。支持本地和远程两种模式。"""

    def __init__(self, loaders: dict[str, StructureDataLoader], ocr_service_url: str | None = None):
        self._loaders = loaders
        self._ocr_engine = None
        self._ocr_service_url = ocr_service_url or None

    @property
    def name(self) -> str:
        return "get_image_ocr"

    @property
    def description(self) -> str:
        return """【图片OCR工具】对文档中的指定图片进行OCR文字识别，提取图片中的文字内容。

当需要验证应标书中证明材料的文字内容时使用（如资质证书、业绩证明等）。

参数说明：
- "doc_type": "tender"（招标书）或 "bid"（应标书），必填
- "image_path": 图片文件路径（从 get_section_images 返回的 path 字段获取），必填

返回：图片中识别到的文字内容。"""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["tender", "bid"],
                    "description": "文档类型：tender=招标书，bid=应标书",
                },
                "image_path": {
                    "type": "string",
                    "description": "图片文件路径（从 get_section_images 返回的 path 字段获取）",
                },
            },
            "required": ["doc_type", "image_path"],
        }

    async def execute(self, doc_type: str = None, image_path: str = None, **kwargs) -> ToolResult:
        if not doc_type or not image_path:
            return ToolResult(success=False, content="", error="缺少参数 doc_type 或 image_path")

        try:
            loader = self._loaders.get(doc_type)
            if not loader:
                return ToolResult(success=False, content="", error=f"未知文档类型: {doc_type}")

            full_path = loader.get_image_full_path(image_path)
            if not full_path.exists():
                return ToolResult(success=False, content="", error=f"图片文件不存在: {full_path}")

            if self._ocr_service_url:
                ocr_text = await self._remote_ocr(full_path)
            else:
                ocr_text = await asyncio.to_thread(self._run_ocr_local, full_path)

            if not ocr_text.strip():
                return ToolResult(
                    success=True,
                    content=f"图片 {image_path} 中未识别到文字内容",
                    data={"image_path": image_path, "ocr_text": ""},
                )

            return ToolResult(
                success=True,
                content=f"图片 {image_path} OCR识别结果：\n\n{ocr_text}",
                data={"image_path": image_path, "ocr_text": ocr_text},
            )

        except Exception as e:
            logger.error(f"ImageOcrTool error: {e}")
            return ToolResult(success=False, content="", error=str(e))

    def _run_ocr_local(self, image_path: Path) -> str:
        """Synchronous local OCR using RapidOCR (run in thread pool)."""
        if self._ocr_engine is None:
            from rapidocr import RapidOCR
            self._ocr_engine = RapidOCR()

        ocr = self._ocr_engine
        output = ocr(str(image_path))
        if output.txts is None or len(output.txts) == 0:
            return ""
        return "\n".join(output.txts)

    async def _remote_ocr(self, image_path: Path) -> str:
        """Remote OCR via HTTP microservice."""
        suffix = image_path.suffix.lstrip(".") or "png"
        image_b64 = base64.b64encode(image_path.read_bytes()).decode()

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self._ocr_service_url}/api/ocr",
                    json={"image_base64": image_b64, "image_format": suffix},
                )
                response.raise_for_status()
                result = response.json()
        except httpx.ConnectError:
            raise RuntimeError(f"无法连接到 OCR 服务: {self._ocr_service_url}")
        except httpx.TimeoutException:
            raise RuntimeError("OCR 服务请求超时 (60s)")

        if not result.get("success"):
            raise RuntimeError(result.get("error", "OCR failed"))
        return result.get("ocr_text", "")
