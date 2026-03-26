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
        return """Search enterprise knowledge base for relevant policies, regulations, and historical cases.

Input should be a JSON object with:
- 'query': Search query string (required)
- 'limit': Maximum number of results to return, defaults to 5

Returns relevant knowledge base entries with source information."""

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

        if not settings.rag_memory_service_url:
            return ToolResult(
                success=False,
                content="",
                error="RAG memory service URL not configured",
            )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.rag_memory_service_url}/api/search",
                    json={"query": query, "limit": limit},
                )

                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])

                    if not results:
                        return ToolResult(
                            success=True,
                            content="No relevant knowledge base entries found for this query.",
                            data={"results": [], "query": query},
                        )

                    # Format results for better readability
                    formatted_results = []
                    for i, r in enumerate(results):
                        source = r.get("source", "Unknown source")
                        snippet = r.get("snippet", "")
                        score = r.get("score", 0)
                        formatted_results.append(
                            f"[{i + 1}] Source: {source} (relevance: {score:.2f})\n"
                            f"    Content: {snippet}"
                        )

                    content = "Knowledge Base Results:\n\n" + "\n\n".join(formatted_results)

                    return ToolResult(
                        success=True,
                        content=content,
                        data={
                            "results": results,
                            "count": len(results),
                            "query": query,
                        },
                    )
                elif response.status_code == 404:
                    return ToolResult(
                        success=True,
                        content="Knowledge base endpoint not found. Please check RAG service configuration.",
                        data={"error": "endpoint_not_found"},
                    )
                else:
                    return ToolResult(
                        success=False,
                        content="",
                        error=f"RAG service error: {response.status_code} - {response.text[:200]}",
                    )

        except httpx.ConnectError:
            return ToolResult(
                success=False,
                content="",
                error="Could not connect to RAG memory service. Please ensure the service is running.",
            )
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                content="",
                error="RAG memory service request timed out",
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))
