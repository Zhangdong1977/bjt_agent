"""Document search tool for querying tender and bid documents."""

from pathlib import Path

from mini_agent.tools.base import Tool as BaseTool, ToolResult


class DocSearchTool(BaseTool):
    """Tool for searching and reading tender/bid document content."""

    def __init__(self, tender_doc_path: str, bid_doc_path: str):
        """Initialize the document search tool.

        Args:
            tender_doc_path: Path to the parsed tender markdown file
            bid_doc_path: Path to the parsed bid markdown file
        """
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        super().__init__()

    @property
    def name(self) -> str:
        return "search_tender_doc"

    @property
    def description(self) -> str:
        return "Search and read tender/bid document content. Input should be a JSON object with 'doc_type' ('tender' or 'bid') and optional 'query' for filtering."

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
                    "description": "Optional query string to filter content",
                },
            },
            "required": ["doc_type"],
        }

    async def execute(self, doc_type: str, query: str = None) -> ToolResult:
        """Execute the document search.

        Args:
            doc_type: 'tender' or 'bid'
            query: Optional query string to filter lines

        Returns:
            ToolResult with the document content
        """
        try:
            doc_path = Path(self.tender_doc_path if doc_type == "tender" else self.bid_doc_path)

            if not doc_path.exists():
                return ToolResult(
                    success=False,
                    content="",
                    error=f"Document not found: {doc_path}",
                )

            content = doc_path.read_text(encoding="utf-8")

            if query:
                # Simple keyword search
                lines = content.split("\n")
                matches = []
                for i, line in enumerate(lines):
                    if query.lower() in line.lower():
                        matches.append(f"Line {i + 1}: {line}")

                if matches:
                    content = "\n".join(matches[:50])  # Limit to 50 matches
                else:
                    content = "No matching content found."

            return ToolResult(success=True, content=content)

        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))
