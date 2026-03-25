"""Bid Review Agent - extends Mini-Agent with custom tools.

This agent is responsible for comparing tender documents against bid documents
and identifying non-compliant items.
"""

import sys
from pathlib import Path

# Add Mini-Agent to path
mini_agent_path = Path(__file__).parent.parent.parent / "Mini-Agent"
if mini_agent_path.exists() and str(mini_agent_path) not in sys.path:
    sys.path.insert(0, str(mini_agent_path))

from mini_agent.agent import Agent as BaseAgent
from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider

from backend.config import get_settings
from backend.agent.tools.doc_search import DocSearchTool
from backend.agent.tools.rag_search import RAGSearchTool
from backend.agent.tools.comparator import ComparatorTool
from backend.agent.prompt import SYSTEM_PROMPT

settings = get_settings()


class BidReviewAgent(BaseAgent):
    """Bid review agent that extends Mini-Agent with domain-specific tools."""

    def __init__(
        self,
        project_id: str,
        tender_doc_path: str,
        bid_doc_path: str,
        user_id: str,
        event_callback=None,
        **kwargs,
    ):
        """Initialize the bid review agent.

        Args:
            project_id: The project ID for organizing workspace
            tender_doc_path: Path to the parsed tender document
            bid_doc_path: Path to the parsed bid document
            user_id: The user ID for workspace organization
            event_callback: Optional callback for SSE event publishing
        """
        self.project_id = project_id
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        self.user_id = user_id
        self.event_callback = event_callback

        # Initialize LLM client (MiniMax uses OpenAI protocol)
        llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,  # MiniMax uses OpenAI-compatible API
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )

        # Initialize tools
        tools = [
            DocSearchTool(tender_doc_path=tender_doc_path, bid_doc_path=bid_doc_path),
            RAGSearchTool(),
            ComparatorTool(),
        ]

        # Set up workspace
        workspace_dir = settings.workspace_path / str(user_id) / str(project_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Initialize base agent
        super().__init__(
            llm_client=llm_client,
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            workspace_dir=str(workspace_dir),
            **kwargs,
        )

    def _send_event(self, event_type: str, data: dict) -> None:
        """Send an event via callback if available."""
        if self.event_callback:
            self.event_callback(event_type, data)

    async def run_review(self) -> list[dict]:
        """Run the bid review process.

        Returns:
            List of findings with requirement, bid content, compliance status, etc.
        """
        # Build the review task description
        task = f"""Please review the bid document against the tender requirements.

Tender Document: {self.tender_doc_path}
Bid Document: {self.bid_doc_path}

Follow the workflow in your system prompt to:
1. Read and extract all requirements from the tender document
2. Query the enterprise knowledge base for relevant policies
3. Search the bid document for corresponding responses
4. Compare each requirement against the bid response
5. Identify non-compliant items with severity levels

Output your findings in the specified JSON format for each finding.
"""

        self.add_user_message(task)

        # Send event
        self._send_event("progress", {"message": "Starting agent review..."})

        # Run the agent
        await self.run()

        # Parse and return results
        return self._parse_findings()

    def _parse_findings(self) -> list[dict]:
        """Parse agent output into structured findings.

        Returns:
            List of finding dictionaries.
        """
        # Get the last assistant message
        findings = []
        for msg in reversed(self.messages):
            if msg.role == "assistant" and msg.content:
                # Try to parse JSON from the response
                import json
                import re

                # Look for JSON array in the content
                json_match = re.search(r'\[.*\]', msg.content, re.DOTALL)
                if json_match:
                    try:
                        findings = json.loads(json_match.group())
                        break
                    except json.JSONDecodeError:
                        pass

        return findings
