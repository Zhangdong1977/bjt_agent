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
OCR_MAX_RETRIES = 3
OCR_RETRY_BASE_DELAY_SECONDS = 1.0
RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


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

        # Fallback for documents without markdown headings: split by blank lines
        # into virtual sections so get_section_images can still find images.
        if not sections:
            lines = self.markdown_lines
            current_start = 1
            section_idx = 0
            _MIN_SECTION_LINES = 5
            for i in range(len(lines)):
                if lines[i].strip() == "" and (i + 1) >= current_start + _MIN_SECTION_LINES:
                    section_idx += 1
                    sections.append(SectionInfo(
                        section_id=f"s{section_idx}",
                        title=f"段落 {section_idx}",
                        level=1,
                        start_line=current_start,
                        end_line=i + 1,
                    ))
                    current_start = i + 2
            if current_start <= len(lines):
                section_idx += 1
                sections.append(SectionInfo(
                    section_id=f"s{section_idx}",
                    title=f"段落 {section_idx}",
                    level=1,
                    start_line=current_start,
                    end_line=len(lines),
                ))

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


def _create_shared_loaders(
    tender_docs: list[tuple[str, str]],
    bid_docs: list[tuple[str, str]],
) -> dict[str, dict[str, StructureDataLoader]]:
    """Create shared StructureDataLoader instances for all documents.

    Args:
        tender_docs: [(filename, parsed_md_path), ...]
        bid_docs: [(filename, parsed_md_path), ...]

    Returns:
        {doc_type: {doc_name: StructureDataLoader}}
    """
    result: dict[str, dict[str, StructureDataLoader]] = {}
    for doc_type, docs in [("tender", tender_docs), ("bid", bid_docs)]:
        result[doc_type] = {
            name: StructureDataLoader(path) for name, path in docs
        }
    return result


class DocumentTocTool(BaseTool):
    """获取招标书或应标书的章节目录结构。"""

    def __init__(self, loaders: dict[str, dict[str, StructureDataLoader]]):
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
- "doc_name": 可选，指定某个文件的目录。不指定则显示该类型所有文件的目录

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
                "doc_name": {
                    "type": "string",
                    "description": "可选，指定某个文件的目录。不指定则显示该类型所有文件的目录",
                },
            },
            "required": ["doc_type"],
        }

    async def execute(self, doc_type: str = None, doc_name: str = None, **kwargs) -> ToolResult:
        if not doc_type:
            return ToolResult(success=False, content="", error="缺少参数 doc_type")

        try:
            doc_loaders = self._loaders.get(doc_type)
            if not doc_loaders:
                return ToolResult(success=False, content="", error=f"未知文档类型: {doc_type}")

            if doc_name:
                if doc_name not in doc_loaders:
                    available = "、".join(f"\"{n}\"" for n in doc_loaders.keys())
                    return ToolResult(success=False, content="", error=f"未找到文档 \"{doc_name}\"。可用文档：{available}")
                targets = {doc_name: doc_loaders[doc_name]}
            else:
                targets = doc_loaders

            doc_label = "招标书" if doc_type == "tender" else "应标书"
            all_sections_data = []
            toc_lines = []

            for name, loader in targets.items():
                sections = loader.get_toc()
                if len(targets) > 1:
                    toc_lines.append(f"{'='*10} {name} {'='*10}")

                if not sections:
                    toc_lines.append(f"（无章节标题结构）\n")
                    continue

                toc_lines.append(f"目录结构（共 {len(sections)} 个章节）：\n")
                for sec in sections:
                    indent = "  " * (sec.level - 1)
                    children_note = f" ({sec.children_count} 个子章节)" if sec.children_count > 0 else ""
                    toc_lines.append(f"{indent}* [{sec.section_id}] {'#' * sec.level} {sec.title}{children_note}")

                all_sections_data.extend([
                    {
                        "section_id": s.section_id,
                        "title": s.title,
                        "level": s.level,
                        "children_count": s.children_count,
                        **({"source_doc": name} if len(targets) > 1 else {}),
                    }
                    for s in sections
                ])
                toc_lines.append("")

            if not all_sections_data:
                return ToolResult(success=True, content=f"{doc_label}无章节标题结构", data={"sections": []})

            header = f"{doc_label}"
            if len(targets) > 1:
                header += f"（共 {len(targets)} 份文件）"
            result_content = f"{header}\n\n" + "\n".join(toc_lines)

            return ToolResult(
                success=True,
                content=result_content,
                data={"sections": all_sections_data},
            )

        except Exception as e:
            logger.error(f"DocumentTocTool error: {e}")
            return ToolResult(success=False, content="", error=str(e))


class SectionContentTool(BaseTool):
    """获取指定章节的完整内容。"""

    def __init__(self, loaders: dict[str, dict[str, StructureDataLoader]]):
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
- "doc_name": 可选，指定某个文件。不指定则从该类型所有文件中查找
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
                "doc_name": {
                    "type": "string",
                    "description": "可选，指定某个文件。不指定则从该类型所有文件中查找",
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
        doc_name: str = None,
        include_subsections: bool = True,
        **kwargs,
    ) -> ToolResult:
        if not doc_type or not section_id:
            return ToolResult(success=False, content="", error="缺少参数 doc_type 或 section_id")

        try:
            doc_loaders = self._loaders.get(doc_type)
            if not doc_loaders:
                return ToolResult(success=False, content="", error=f"未知文档类型: {doc_type}")

            if doc_name:
                if doc_name not in doc_loaders:
                    return ToolResult(success=False, content="", error=f"未找到文档: {doc_name}")
                targets = {doc_name: doc_loaders[doc_name]}
            else:
                targets = doc_loaders

            # 收集所有匹配 section_id 的文档结果（多文档下不 short-circuit）
            # 修复: 原代码 return on first match，多文档下会丢失内容
            results = []
            for name, loader in targets.items():
                sections = loader.get_toc()
                section_title = ""
                for sec in sections:
                    if sec.section_id == section_id:
                        section_title = sec.title
                        break

                content = loader.get_section_content(section_id, include_subsections)
                if content is None:
                    continue  # 该 doc 没有此章节

                results.append({
                    "name": name,
                    "title": section_title,
                    "content": content,
                })

            if not results:
                return ToolResult(success=False, content="", error=f"未找到章节: {section_id}")

            doc_label = "招标书" if doc_type == "tender" else "应标书"

            # 单文档：保持原行为（向后兼容，data 字段用 source_doc 单数）
            if len(results) == 1:
                r = results[0]
                if not r["content"].strip():
                    return ToolResult(
                        success=True,
                        content=f"{doc_label} -- [{section_id}] {r['title']} 无文本内容",
                        data={"section_id": section_id, "content": "", "source_doc": r["name"]},
                    )
                header = f"{doc_label} -- [{section_id}] {r['title']}"
                if not include_subsections:
                    header += "（不含子章节）"
                return ToolResult(
                    success=True,
                    content=f"{header}\n\n{r['content']}",
                    data={
                        "section_id": section_id,
                        "section_title": r["title"],
                        "content": r["content"],
                        "include_subsections": include_subsections,
                        "source_doc": r["name"],
                    },
                )

            # 多文档：用分隔符串联每个 doc 的内容，data 用 sources 列表
            sections_rendered = []
            sources = []
            for r in results:
                sources.append(r["name"])
                sections_rendered.append(
                    f"### [{r['name']}] {doc_label} -- [{section_id}] {r['title']}\n\n{r['content']}"
                )
            return ToolResult(
                success=True,
                content="\n\n".join(sections_rendered),
                data={
                    "section_id": section_id,
                    "section_title": results[0]["title"],
                    "sources": sources,  # 多文档时改用 sources 列表
                    "include_subsections": include_subsections,
                },
            )

        except Exception as e:
            logger.error(f"SectionContentTool error: {e}")
            return ToolResult(success=False, content="", error=str(e))


class SectionImagesTool(BaseTool):
    """获取指定章节下的所有图片列表。"""

    def __init__(self, loaders: dict[str, dict[str, StructureDataLoader]]):
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
- "doc_name": 可选，指定某个文件。不指定则从该类型所有文件中查找

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
                "doc_name": {
                    "type": "string",
                    "description": "可选，指定某个文件。不指定则从该类型所有文件中查找",
                },
            },
            "required": ["doc_type", "section_id"],
        }

    async def execute(self, doc_type: str = None, section_id: str = None, doc_name: str = None, **kwargs) -> ToolResult:
        if not doc_type or not section_id:
            return ToolResult(success=False, content="", error="缺少参数 doc_type 或 section_id")

        try:
            doc_loaders = self._loaders.get(doc_type)
            if not doc_loaders:
                return ToolResult(success=False, content="", error=f"未知文档类型: {doc_type}")

            if doc_name:
                if doc_name not in doc_loaders:
                    return ToolResult(success=False, content="", error=f"未找到文档: {doc_name}")
                targets = {doc_name: doc_loaders[doc_name]}
            else:
                targets = doc_loaders

            all_images = []
            result_lines = []
            doc_label = "招标书" if doc_type == "tender" else "应标书"

            for name, loader in targets.items():
                images = loader.get_section_images(section_id)
                if images:
                    source = f" [{name}]" if len(targets) > 1 else ""
                    result_lines.append(f"{doc_label}{source}章节 [{section_id}] 下有 {len(images)} 张图片：")
                    for img in images:
                        result_lines.append(f"  * [{img['image_id']}] {img['filename']}")
                        if len(targets) > 1:
                            img["source_doc"] = name
                    all_images.extend(images)

            if not all_images:
                return ToolResult(
                    success=True,
                    content=f"{doc_label}章节 [{section_id}] 下无图片",
                    data={"section_id": section_id, "images": []},
                )

            return ToolResult(
                success=True,
                content="\n".join(result_lines),
                data={"section_id": section_id, "images": all_images},
            )

        except Exception as e:
            logger.error(f"SectionImagesTool error: {e}")
            return ToolResult(success=False, content="", error=str(e))


class ImageOcrTool(BaseTool):
    """对文档中的指定图片进行 OCR 文字识别。支持本地和远程两种模式。"""

    def __init__(self, loaders: dict[str, dict[str, StructureDataLoader]], ocr_service_url: str | None = None):
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
                    "description": "图片相对路径（从 get_section_images 返回的 path 字段获取）",
                },
                "doc_name": {
                    "type": "string",
                    "description": "文档文件名（多份文档同类型时必填，单份时可选）。例如：'主标文件.pdf'",
                },
            },
            "required": ["doc_type", "image_path"],
        }

    async def execute(
        self,
        doc_type: str = None,
        image_path: str = None,
        doc_name: str = None,
        **kwargs,
    ) -> ToolResult:
        if not doc_type or not image_path:
            return ToolResult(success=False, content="", error="缺少参数 doc_type 或 image_path")

        try:
            doc_loaders = self._loaders.get(doc_type)
            if not doc_loaders:
                return ToolResult(success=False, content="", error=f"未知文档类型: {doc_type}")

            # 多份同类文档时强制要求 doc_name 消歧（避免 OCR 错文件）
            if len(doc_loaders) > 1 and not doc_name:
                available = ", ".join(sorted(doc_loaders.keys()))
                return ToolResult(
                    success=False,
                    content="",
                    error=(
                        f"Multiple documents of type '{doc_type}' exist ({available}). "
                        f"doc_name is required to disambiguate which file to OCR."
                    ),
                )

            # 显式指定 doc_name
            if doc_name:
                if doc_name not in doc_loaders:
                    return ToolResult(
                        success=False,
                        content="",
                        error=f"未找到文档: {doc_name}",
                    )
                loader = doc_loaders[doc_name]
                full_path = loader.get_image_full_path(image_path)
                if not full_path.exists():
                    return ToolResult(
                        success=False,
                        content="",
                        error=f"图片文件不存在于 {doc_name}: {image_path}",
                    )
                matched_name = doc_name
            else:
                # 单文档场景：直接走该 loader
                matched_name, loader = next(iter(doc_loaders.items()))
                full_path = loader.get_image_full_path(image_path)
                if not full_path.exists():
                    return ToolResult(
                        success=False,
                        content="",
                        error=f"图片文件不存在: {image_path}",
                    )

            if self._ocr_service_url:
                ocr_text = await self._remote_ocr(full_path)
            else:
                ocr_text = await asyncio.to_thread(self._run_ocr_local, full_path)

            if not ocr_text.strip():
                return ToolResult(
                    success=True,
                    content=f"图片 [{matched_name}] {image_path} 中未识别到文字内容",
                    data={"image_path": image_path, "ocr_text": "", "source_doc": matched_name},
                )

            return ToolResult(
                success=True,
                content=f"图片 [{matched_name}] {image_path} OCR识别结果：\n\n{ocr_text}",
                data={"image_path": image_path, "ocr_text": ocr_text, "source_doc": matched_name},
            )

        except Exception as e:
            logger.error(f"ImageOcrTool error: {e}")
            return ToolResult(success=False, content="", error=str(e))

    def _run_ocr_local(self, image_path: Path) -> str:
        """Synchronous local OCR using RapidOCR (run in thread pool)."""
        if self._ocr_engine is None:
            from rapidocr import RapidOCR
            from backend.config import get_settings
            model_dir = str(get_settings().ocr_model_dir)
            self._ocr_engine = RapidOCR(params={"Global.model_root_dir": model_dir})

        ocr = self._ocr_engine
        output = ocr(str(image_path))
        if output.txts is None or len(output.txts) == 0:
            return ""
        return "\n".join(output.txts)

    async def _remote_ocr(self, image_path: Path) -> str:
        """Remote OCR via HTTP microservice, with three retries for transient failures."""
        suffix = image_path.suffix.lstrip(".") or "png"
        image_b64 = base64.b64encode(image_path.read_bytes()).decode()
        total_attempts = OCR_MAX_RETRIES + 1

        for attempt in range(1, total_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        f"{self._ocr_service_url}/api/ocr",
                        json={"image_base64": image_b64, "image_format": suffix},
                    )
                    response.raise_for_status()
                    result = response.json()

                if result.get("success"):
                    return result.get("ocr_text", "")
                failure = RuntimeError(result.get("error", "OCR failed"))
            except httpx.ConnectError:
                failure = RuntimeError(f"无法连接到 OCR 服务: {self._ocr_service_url}")
            except httpx.TimeoutException:
                failure = RuntimeError("OCR 服务请求超时 (60s)")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in RETRYABLE_HTTP_STATUS_CODES:
                    raise RuntimeError(
                        f"OCR 服务 HTTP 错误: {exc.response.status_code}"
                    ) from exc
                failure = RuntimeError(f"OCR 服务 HTTP 错误: {exc.response.status_code}")
            except (httpx.RequestError, ValueError) as exc:
                failure = RuntimeError(f"OCR 服务调用异常: {exc}")

            if attempt == total_attempts:
                raise failure

            delay = OCR_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "[ImageOcrTool] 远程 OCR 第 %d/%d 次调用失败（%s），%.1fs 后重试",
                attempt,
                total_attempts,
                failure,
                delay,
            )
            await asyncio.sleep(delay)

        raise RuntimeError("OCR 服务重试流程异常结束")
