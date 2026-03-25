"""RAG search tool for querying the enterprise knowledge base."""

import httpx

from mini_agent.tools.base import Tool as BaseTool, ToolResult

from backend.config import get_settings


class RAGSearchTool(BaseTool):
    """Tool for searching the enterprise knowledge base via rag_memory_service."""

    def __init__(self):
        """Initialize the RAG search tool."""
        super().__init__()

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        return "Search enterprise knowledge base for relevant information. Input should be a JSON object with 'query' string and optional 'limit' (default 5)."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for the knowledge base",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str, limit: int = 5) -> ToolResult:
        """Execute the RAG search.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            ToolResult with the search results
        """
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.rag_memory_service_url}/api/search",
                    json={"query": query, "limit": limit},
                )

                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    snippet = "\n".join([r.get("snippet", "") for r in results])
                    return ToolResult(
                        success=True,
                        content=snippet,
                        data={"results": results},
                    )
                else:
                    return ToolResult(
                        success=False,
                        content="",
                        error=f"RAG service error: {response.status_code}",
                    )

        except httpx.ConnectError:
            return ToolResult(
                success=False,
                content="",
                error="Could not connect to RAG memory service",
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))
