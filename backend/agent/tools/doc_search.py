"""Document search tool for querying tender and bid documents."""

import re
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
            tender_doc_path: Path to the parsed tender markdown file
            bid_doc_path: Path to the parsed bid markdown file
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
            line_stripped = line.strip()
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
                    summary_parts.append(f"• {line.strip()[:(_FALLBACK_LINE_TRUNCATE)]}")

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
                    "line_content": line.strip(),
                    "context_before": context_before.strip(),
                    "context_after": context_after.strip(),
                })

                if len(results) >= max_results:
                    break

        return results

    def _chunk_content(self, content: str, chunk_num: int = 0) -> str:
        """Split large content into chunks and return specific chunk."""
        if len(content) <= self.chunk_size:
            return content

        chunks = []
        start = 0
        while start < len(content):
            end = start + self.chunk_size
            if chunk_num > 0 and start < self.chunk_size * chunk_num:
                start = self.chunk_size * chunk_num
                continue
            chunk = content[start:end]
            # Try to break at a paragraph or sentence boundary
            if end < len(content):
                break_points = [
                    chunk.rfind("\n\n"),
                    chunk.rfind("\n"),
                    chunk.rfind(". "),
                ]
                for bp in break_points:
                    if bp > self.chunk_size * 0.7:  # At least 70% of chunk_size
                        end = start + bp + 1
                        chunk = content[start:end]
                        break
            chunks.append(chunk)
            start = end

        if chunk_num < len(chunks):
            return chunks[chunk_num]
        return content

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
                content = "\n".join(lines)
                summary = self._extract_summary(content)
                doc_label = "招标书" if doc_type == "tender" else "投标书"
                friendly_content = f"📄 {doc_label}内容摘要\n\n这份{doc_label}的主要内容如下：\n{summary}\n\n[完整文档已加载，共 {len(lines)} 行]"
                return ToolResult(
                    success=True,
                    content=friendly_content,
                    data={"line_count": len(lines), "chunk": chunk, "raw_summary": summary},
                )

            # If query provided, search by keyword
            if query:
                matches = self._search_by_keyword(lines, query)

                if not matches:
                    doc_label = "招标书" if doc_type == "tender" else "投标书"
                    return ToolResult(
                        success=True,
                        content=f"抱歉，未在{doc_label}中找到与\"{query}\"相关的内容。",
                        data={"query": query, "matches": 0},
                    )

                # Format results with context
                doc_label = "招标书" if doc_type == "tender" else "投标书"
                formatted = [f"🔎 在{doc_label}中找到 **{len(matches)}** 处提到\"{query}\"：\n"]
                for i, m in enumerate(matches[:MAX_LINES_PER_QUERY], 1):
                    formatted.append(f"{i}. {m['line_content']}")
                    if m.get('context_before'):
                        formatted.append(f"   <- {m['context_before']}")
                    if m.get('context_after'):
                        formatted.append(f"   -> {m['context_after']}")

                return ToolResult(
                    success=True,
                    content="\n".join(formatted),
                    data={
                        "query": query,
                        "matches": len(matches),
                        "results": matches,
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
