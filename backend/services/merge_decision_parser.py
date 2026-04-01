"""Parser for LLM merge decision output."""
import re
import logging

logger = logging.getLogger(__name__)


def parse_merge_decision(text: str) -> dict:
    """Parse natural language decision from LLM.

    Args:
        text: LLM output in natural language format

    Returns:
        {
            "action": "keep" | "replace" | "discard" | "keep_both",
            "reason": str,
            "replace_key": str | None,
            "parse_failed": bool
        }

    On parse failure, returns keep_both strategy:
        {
            "action": "keep_both",
            "reason": "parse failed",
            "replace_key": None,
            "parse_failed": True
        }
    """
    try:
        # Extract decision (case-insensitive)
        decision_match = re.search(r'决策\s*[:：]\s*(\w+)', text, re.IGNORECASE)
        if not decision_match:
            raise ValueError("Cannot find decision field")

        decision = decision_match.group(1).lower().strip()

        # Validate decision
        valid_decisions = {"keep", "replace", "discard"}
        if decision not in valid_decisions:
            raise ValueError(f"Invalid decision: {decision}")

        # Extract reason
        reason_match = re.search(r'理由\s*[:：]\s*(.+?)(?=\n替换key|替换key|$)', text, re.DOTALL)
        reason = reason_match.group(1).strip() if reason_match else ""

        # Extract replace key
        replace_key = None
        if decision == "replace":
            key_match = re.search(r'替换key\s*[:：]\s*(\S+)', text)
            replace_key = key_match.group(1) if key_match else None

        return {
            "action": decision,
            "reason": reason,
            "replace_key": replace_key,
            "parse_failed": False,
        }

    except Exception as e:
        logger.warning(f"Failed to parse merge decision: {e}, text: {text[:200]}")
        return {
            "action": "keep_both",
            "reason": f"parse failed: {str(e)}",
            "replace_key": None,
            "parse_failed": True,
        }