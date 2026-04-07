"""Document search tool for querying tender and bid documents."""

import re
from html import unescape
from pathlib import Path
from typing import Optional

from mini_agent.tools.base import Tool as BaseTool, ToolResult

# Default chunk size for large documents
DEFAULT_CHUNK_SIZE = 8000  # characters
MAX_LINES_PER_QUERY = 100

# Constants for _extract_summary
_MIN_LINE_LENGTH = 5
_MAX_LINE_TRUNCATE = 150
_MAX_LINES_PER_CATEGORY = 3
_FALLBACK_LINE_TRUNCATE = 100
_FALLBACK_LINE_COUNT = 5

def strip_html_tags(text: str) -> str:
    """去除HTML标签，保留纯文本"""
    if not text:
        return ""
    # 先unescape HTML实体
    text = unescape(text)
    # 去除<script>和<style>标签及其内容
    text = re.sub(r'<\s*(script|style)[^>]*>.*?<\s*/\1\s*>', '', text, flags=re.DOTALL|re.IGNORECASE)
    # 去除所有HTML标签
    text = re.sub(r'<[^>]+>', '', text)
    # 清理多余空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def smart_truncate(text: str, max_length: int = 150) -> str:
    """智能截断，优先保留完整句子"""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    # 在句号、逗号处截断
    truncated = text[:max_length]
    for sep in ['。', '，', '. ', ', ']:
        idx = truncated.rfind(sep)
        if idx > max_length * 0.6:
            return truncated[:idx+1]
    return truncated + '...'


# Precompiled category patterns for performance
_CATEGORY_PATTERNS = {
    "技术": (re.compile(r"技术|Python|Vue|FastAPI|开发", re.IGNORECASE), "🛠️"),
    "工期": (re.compile(r"工期|时间|交付|完成", re.IGNORECASE), "⏱️"),
    "预算": (re.compile(r"预算|价格|万|元|费用|成本", re.IGNORECASE), "💰"),
    "资质": (re.compile(r"资质|证书|认证|ISO|CMMI", re.IGNORECASE), "📋"),
}


class DocSearchTool(BaseTool):
    """Tool for searching and reading tender/bid document content."""

    def __init__(self, tender_doc_path: str, bid_doc_path: str, chunk_size: int = DEFAULT_CHUNK_SIZE):
        """Initialize the document search tool.

        Args:
            tender_doc_path: Path to the parsed tender HTML file
            bid_doc_path: Path to the parsed bid HTML file
            chunk_size: Maximum characters per chunk for large documents
        """
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        self.chunk_size = chunk_size
        # Cache for loaded documents to avoid repeated disk reads
        self._cache: dict[str, tuple[str, list[str]]] = {}
        super().__init__()

    def _load_document(self, doc_path: Path) -> tuple[str, list[str]]:
        """Load document and return (full_content, lines_list).

        Uses caching to avoid repeated disk reads.
        """
        cache_key = str(doc_path)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not doc_path.exists():
            raise FileNotFoundError(f"Document not found: {doc_path}")

        content = doc_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        self._cache[cache_key] = (content, lines)
        return content, lines

    def _find_line_around(self, lines: list[str], keyword: str, target_line: int = None) -> tuple[int, str]:
        """Find line number and content around the keyword match.

        Returns (line_number, line_content).
        """
        for i, line in enumerate(lines):
            if keyword.lower() in line.lower():
                return i + 1, line  # 1-indexed
        return -1, ""

    def _extract_summary(self, content: str) -> str:
        """Extract a structured summary from document content.

        Returns a human-friendly summary with categorized sections.
        """
        lines = content.split('\n')
        summary_parts = []

        # Find matching lines for each category using precompiled patterns
        categorized_lines = {cat: [] for cat in _CATEGORY_PATTERNS}

        for line in lines:
            line_stripped = strip_html_tags(line.strip())
            if not line_stripped or len(line_stripped) < _MIN_LINE_LENGTH:
                continue
            for cat_name, (pattern, _icon) in _CATEGORY_PATTERNS.items():
                if pattern.search(line_stripped):
                    categorized_lines[cat_name].append(line_stripped[:_MAX_LINE_TRUNCATE])
                    break

        # Build summary
        for cat_name, (pattern, icon) in _CATEGORY_PATTERNS.items():
            lines_for_cat = categorized_lines[cat_name][:(_MAX_LINES_PER_CATEGORY)]
            if lines_for_cat:
                summary_parts.append(f"\n{icon} {cat_name}要求")
                for l in lines_for_cat:
                    summary_parts.append(f"• {l}")

        if not summary_parts:
            # Fallback: first few non-empty lines
            summary_parts.append("\n📝 文档内容")
            for line in lines[:(_FALLBACK_LINE_COUNT)]:
                if line.strip():
                    summary_parts.append(f"• {strip_html_tags(line.strip())[:(_FALLBACK_LINE_TRUNCATE)]}")

        return "\n".join(summary_parts)

    def _search_by_keyword(self, lines: list[str], query: str, max_results: int = MAX_LINES_PER_QUERY) -> list[dict]:
        """Search document lines by keyword and return matches with context.

        Returns list of {line_number, line_content, context}.
        """
        results = []
        query_lower = query.lower()

        for i, line in enumerate(lines):
            if query_lower in line.lower():
                # Get some context (previous and next lines if available)
                context_before = lines[max(0, i - 1)][:100] if i > 0 else ""
                context_after = lines[min(len(lines) - 1, i + 1)][:100] if i < len(lines) - 1 else ""

                results.append({
                    "line_number": i + 1,  # 1-indexed
                    "line_content": smart_truncate(strip_html_tags(line.strip()), 200),
                    "context_before": smart_truncate(strip_html_tags(context_before.strip()), 100),
                    "context_after": smart_truncate(strip_html_tags(context_after.strip()), 100),
                })

                if len(results) >= max_results:
                    break

        return results

    def _chunk_content(self, content: str, chunk_num: int = 0) -> str:
        """Split large content into chunks and return specific chunk.

        Returns the full content if it's smaller than chunk_size.
        Returns empty string if chunk_num is out of range.
        """
        if len(content) <= self.chunk_size:
            return content

        # Split content into chunks at paragraph/sentence boundaries
        chunks = []
        start = 0
        while start < len(content):
            end = start + self.chunk_size
            if end < len(content):
                # Try to break at a paragraph or sentence boundary
                break_points = [
                    content.rfind("\n\n", start, end),
                    content.rfind("\n", start, end),
                    content.rfind(". ", start, end),
                ]
                for bp in break_points:
                    if bp > start and bp >= end - 200:  # At least 200 chars into chunk
                        end = bp + 1
                        break
            chunk = content[start:end]
            chunks.append(chunk)
            start = end

        # Return the requested chunk or empty if out of range
        if chunk_num < 0 or chunk_num >= len(chunks):
            return ""

        return chunks[chunk_num]

    @property
    def name(self) -> str:
        return "search_tender_doc"

    @property
    def description(self) -> str:
        return """Search and read tender/bid document content. Input should be a JSON object with:
- 'doc_type': 'tender' or 'bid' (required)
- 'query': Optional keyword to filter content (returns matching lines with context)
- 'chunk': Optional chunk number for large documents (0-indexed)
- 'full_content': Set to true to return entire document content

Returns matching lines with line numbers and surrounding context."""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["tender", "bid"],
                    "description": "Type of document to search",
                },
                "query": {
                    "type": "string",
                    "description": "Optional query string to filter content (keyword search)",
                },
                "chunk": {
                    "type": "integer",
                    "description": "Chunk number for large documents (0-indexed, for pagination)",
                    "default": 0,
                },
                "full_content": {
                    "type": "boolean",
                    "description": "If true, return full document content (may be truncated)",
                    "default": False,
                },
            },
            "required": ["doc_type"],
        }

    async def execute(
        self,
        doc_type: str,
        query: str = None,
        chunk: int = 0,
        full_content: bool = False,
    ) -> ToolResult:
        """Execute the document search.

        Args:
            doc_type: 'tender' or 'bid'
            query: Optional query string to filter lines
            chunk: Chunk number for pagination of large documents
            full_content: If True, return full document (may be truncated)

        Returns:
            ToolResult with the document content and metadata
        """
        try:
            doc_path = Path(self.tender_doc_path if doc_type == "tender" else self.bid_doc_path)
            _, lines = self._load_document(doc_path)

            # If full content requested, return friendly summary
            if full_content:
                full_text = "\n".join(lines)
                total_lines = len(lines)
                total_chunks = (len(full_text) + self.chunk_size - 1) // self.chunk_size if len(full_text) > self.chunk_size * 3 else 1

                if len(full_text) > self.chunk_size * 3:
                    full_text = self._chunk_content(full_text, chunk)
                    if chunk > 0:
                        full_text = f"[... Chunk {chunk} ...]\n{full_text}"
                        current_chunk_lines = len(full_text.split('\n'))
                    else:
                        current_chunk_lines = len(full_text.split('\n'))
                else:
                    current_chunk_lines = total_lines

                # Generate friendly summary
                summary = self._extract_summary(full_text)
                doc_label = "招标" if doc_type == "tender" else "投标"

                # Pagination note for chunked content
                if chunk > 0:
                    pagination_note = f"\n📄 当前第 {chunk + 1} 页，共 {total_chunks} 页"
                else:
                    pagination_note = ""

                friendly_content = f"""📄 {doc_label}书内容摘要

这份{doc_label}书共 {total_lines} 行，内容如下：

{summary}{pagination_note}

[完整文档已加载]"""

                return ToolResult(
                    success=True,
                    content=friendly_content,
                    data={
                        "line_count": total_lines,
                        "chunk": chunk,
                        "total_chunks": total_chunks,
                        "current_chunk_lines": current_chunk_lines,
                        "full_content": full_text,
                    },
                )

            # If query provided, search by keyword
            if query:
                matches = self._search_by_keyword(lines, query)

                if not matches:
                    doc_label = "招标" if doc_type == "tender" else "投标"
                    return ToolResult(
                        success=True,
                        content=f"抱歉，未在{doc_label}书中找到与\"{query}\"相关的内容。",
                        data={"query": query, "matches": 0},
                    )

                # Format results with context and citations
                doc_label = "招标" if doc_type == "tender" else "投标"
                display_matches = matches[:10]
                formatted = [f"🔍 在{doc_label}书中找到 **{len(matches)}** 处提到\"{query}\"：\n"]
                citations = []
                for i, m in enumerate(display_matches, 1):
                    # Format citation with line number and quoted content
                    formatted.append(f"{i}. {m['line_content']}")
                    if m.get('context_before'):
                        formatted.append(f"   ↳ 上文: {m['context_before']}")
                    if m.get('context_after'):
                        formatted.append(f"   ↳ 下文: {m['context_after']}")
                    # Build structured citation for data
                    citations.append({
                        "index": i,
                        "line_number": m['line_number'],
                        "quote": m['line_content'],
                        "context_before": m.get('context_before', ''),
                        "context_after": m.get('context_after', ''),
                    })

                if len(matches) > 10:
                    formatted.append(f"\n... 还有 {len(matches) - 10} 处匹配")

                return ToolResult(
                    success=True,
                    content="\n".join(formatted),
                    data={
                        "query": query,
                        "matches": len(matches),
                        "results": matches,
                        "citations": citations,
                    },
                )

            # No query, return document info
            doc_label = "招标" if doc_type == "tender" else "投标"
            return ToolResult(
                success=True,
                content=f"📄 已加载{doc_label}书，共 {len(lines)} 行。",
                data={
                    "line_count": len(lines),
                    "doc_type": doc_type,
                    "path": str(doc_path),
                },
            )

        except FileNotFoundError as e:
            return ToolResult(
                success=False,
                content="",
                error=f"Document not found: {e}",
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))

    def clear_cache(self) -> None:
        """Clear the document cache."""
        self._cache.clear()
