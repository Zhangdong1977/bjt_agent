"""Document search tool for querying tender and bid documents."""

import re
from pathlib import Path
from typing import Optional

from mini_agent.tools.base import Tool as BaseTool, ToolResult

# Default chunk size for large documents
DEFAULT_CHUNK_SIZE = 8000  # characters
MAX_LINES_PER_QUERY = 100


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

            # If full content requested, return truncated content
            if full_content:
                content = "\n".join(lines)
                if len(content) > self.chunk_size * 3:
                    content = self._chunk_content(content, chunk)
                    if chunk > 0:
                        content = f"[... Chunk {chunk} ...]\n{content}"
                return ToolResult(
                    success=True,
                    content=content,
                    data={"line_count": len(lines), "chunk": chunk},
                )

            # If query provided, search by keyword
            if query:
                matches = self._search_by_keyword(lines, query)

                if not matches:
                    return ToolResult(
                        success=True,
                        content=f"No content matching '{query}' found in {doc_type} document.",
                        data={"query": query, "matches": 0},
                    )

                # Format results with context
                formatted = [f"Found {len(matches)} matches for '{query}':\n"]
                for m in matches[:MAX_LINES_PER_QUERY]:
                    formatted.append(f"Line {m['line_number']}: {m['line_content']}")
                    if m.get('context_before'):
                        formatted.append(f"  <- {m['context_before']}")
                    if m.get('context_after'):
                        formatted.append(f"  -> {m['context_after']}")
                    formatted.append("")

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
            return ToolResult(
                success=True,
                content=f"{doc_type.capitalize()} document loaded: {len(lines)} lines",
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
