"""Parser for LLM merge decision output."""
import re
import logging

logger = logging.getLogger(__name__)


def _is_valid_finding(finding: dict) -> bool:
    """Check if a finding has valid content for merging.

    Args:
        finding: Finding dict to validate

    Returns:
        True if finding has meaningful content, False if invalid
    """
    requirement_content = (finding.get("requirement_content") or "").strip()
    bid_content = (finding.get("bid_content") or "").strip()

    # Empty requirement_content is invalid
    if not requirement_content:
        logger.info(f"[_is_valid_finding] Invalid: empty requirement_content, key={finding.get('requirement_key')}")
        return False

    # Single punctuation mark is not meaningful content
    if len(requirement_content) <= 1 and requirement_content in '.。,，;；:：':
        logger.info(f"[_is_valid_finding] Invalid: requirement_content is just punctuation, key={finding.get('requirement_key')}")
        return False

    # JSON-like bid_content suggests parsing error
    if bid_content.startswith('{') or bid_content.startswith('[') or bid_content.startswith('suggestion'):
        logger.info(f"[_is_valid_finding] Invalid: bid_content looks like JSON fragment, key={finding.get('requirement_key')}")
        return False

    # "文件对此技术要求未提供任何说明" type messages are placeholder/incomplete, not real findings
    if "文件对此" in bid_content and "未提供" in bid_content:
        logger.info(f"[_is_valid_finding] Invalid: bid_content is a placeholder message, key={finding.get('requirement_key')}")
        return False

    return True


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
    logger.info(f"[parse_merge_decision] Input text:\n{text[:500]}")
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

        result = {
            "action": decision,
            "reason": reason,
            "replace_key": replace_key,
            "parse_failed": False,
        }
        logger.info(f"[parse_merge_decision] Parsed successfully: action={decision}, reason={reason[:50]}, replace_key={replace_key}")
        return result

    except Exception as e:
        logger.warning(f"[parse_merge_decision] Failed to parse merge decision: {e}, text: {text[:200]}")
        return {
            "action": "keep_both",
            "reason": f"parse failed: {str(e)}",
            "replace_key": None,
            "parse_failed": True,
        }


def parse_batch_merge_decisions(text: str, new_findings_keys: list[str]) -> list[dict]:
    """Parse batch natural language decisions from LLM.

    Args:
        text: LLM output with multiple decisions in natural language format
        new_findings_keys: List of requirement_keys for the new findings in order

    Returns:
        List of decision dicts, one per new finding:
        [{
            "action": "keep" | "replace" | "discard" | "keep_both",
            "reason": str,
            "replace_key": str | None,
            "parse_failed": bool
        }, ...]

    If a finding cannot be parsed, returns keep_both for that finding.
    """
    logger.info(f"[parse_batch_merge_decisions] text length={len(text)}, new_findings_keys count={len(new_findings_keys)}")

    # Find all "新发现[N]" markers with robust pattern
    # Handle both 新发现[1] and 新发现[21] formats
    matches = list(re.finditer(r'新发现\[(\d+)\]', text))

    # Log marker indices to detect LLM skipping
    if matches:
        indices = [int(m.group(1)) if m.lastindex == 1 else int(m.group(2)) for m in matches]
        logger.info(f"[parse_batch_merge_decisions] Found {len(matches)} markers with indices: {indices}")

    if not matches:
        # No markers found, try numbered rule pattern "序号N:" or "N. " at line start
        matches = list(re.finditer(r'(?:^|\n)(序号)?(\d+)[:：.]\s*', text))

    # Extract blocks: content AFTER each marker (until next marker or end of text)
    blocks = []
    if matches:
        for i, m in enumerate(matches):
            start = m.end()
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end = len(text)
            block = text[start:end].strip()
            blocks.append(block)
    else:
        # No markers at all: treat entire text as single block for first finding
        logger.warning("[parse_batch_merge_decisions] No markers found, using entire text as block 0")
        blocks = [text.strip()] if text.strip() else []

    decisions = []
    # Mapping is by position order (block[i] corresponds to new_findings_keys[i]),
    # NOT by marker index. This handles cases where LLM skips indices or uses
    # different numbering schemes. When using Pattern 1 (新发现[N]), validate indices.
    for i, key in enumerate(new_findings_keys):
        if i < len(blocks):
            # Validate marker index for Pattern 1 (新发现[N]) to catch LLM errors.
            # Pattern 1 has lastindex==1 (m.group(1) is the index).
            # Pattern 2 has lastindex==2 (m.group(2) is the index, m.group(1) is "序号").
            if matches and i < len(matches):
                m = matches[i]
                if m.lastindex == 1:  # Pattern 1 only
                    expected_idx = int(m.group(1))
                    if expected_idx != i + 1:
                        logger.warning(
                            f"[parse_batch_merge_decisions] Marker index mismatch at position {i}: "
                            f"found index {expected_idx}, expected {i + 1}. Using position-order mapping."
                        )
            decision = parse_merge_decision(blocks[i])
            decisions.append(decision)
        else:
            # Not enough blocks - pad with keep_both
            logger.info(f"[parse_batch_merge_decisions] Padding keep_both for index {i}, key={key}")
            decisions.append({
                "action": "keep_both",
                "reason": "no corresponding decision block",
                "replace_key": None,
                "parse_failed": True,
            })

    logger.info(f"[parse_batch_merge_decisions] Returning {len(decisions)} decisions")
    return decisions