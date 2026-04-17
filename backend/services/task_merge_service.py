"""Task-level merge service for combining sub-agent results within a single task."""

import json
import logging
from typing import Optional

# Ensure Mini-Agent path is in sys.path before importing mini_agent modules
from backend.utils.mini_agent_utils import setup_mini_agent_path
setup_mini_agent_path()

from mini_agent.schema import Message

logger = logging.getLogger(__name__)


def _format_finding_for_log(finding: dict, max_len: int = 200) -> str:
    """Format a finding dict for detailed audit logging.

    Args:
        finding: Finding dict to format
        max_len: Maximum length for string fields

    Returns:
        Formatted string with finding details
    """
    def truncate(s, length=max_len):
        if s is None:
            return "None"
        s = str(s)
        return s[:length] + "..." if len(s) > length else s

    req_key = finding.get("requirement_key", "N/A")
    req_content = truncate(finding.get("requirement_content"))
    bid_content = truncate(finding.get("bid_content"))
    compliant = finding.get("is_compliant", "N/A")
    severity = finding.get("severity", "N/A")
    explanation = truncate(finding.get("explanation", ""))

    return (
        f"Finding[{req_key}]: "
        f"compliant={compliant}, severity={severity}, "
        f"req_content={req_content}, "
        f"bid_content={bid_content}, "
        f"explanation={explanation}"
    )


def _format_findings_for_log(findings: list[dict], max_len: int = 200) -> str:
    """Format a list of findings for detailed audit logging.

    Args:
        findings: List of finding dicts to format
        max_len: Maximum length for string fields

    Returns:
        Formatted multi-line string with all findings
    """
    if not findings:
        return "  (empty)"

    lines = []
    for i, f in enumerate(findings):
        lines.append(f"  [{i}] {_format_finding_for_log(f, max_len)}")
    return "\n".join(lines)

# Batch merge prompt for processing multiple findings in one LLM call
BATCH_MERGE_PROMPT = """你是专业的标书审查结果合并决策专家，负责将多个新的审查发现与历史发现进行批量合并。

## 决策原则

**重要**：每次审查都应该被保留，除非新发现与某历史发现**完全重复**（实质内容相同）。

1. **keep** - 保留新发现作为独立条目
   - 新发现与所有现有发现都不重复
   - 新发现提供了新的有价值的信息（不同的位置、补充说明等）

2. **replace** - 用新发现替换某个现有发现
   - 新发现与某现有发现描述的是同一个招标要求
   - 新发现的内容更完整、位置更精确、或 severity 更高

3. **discard** - 丢弃新发现
   - 新发现与某现有发现**实质内容完全相同**
   - is_compliant 相同、severity 相同、explanation 相似、bid_content 相似

## 批量新发现列表：
{new_findings}

## 现有发现列表：
{existing_findings}

## 决策指南

- **谨慎 discard**：只有当新发现与现有某个发现"实质相同"时才 discard
- **倾向 keep**：如果有任何疑问，优先选择 keep 而非 discard
- **replace 的使用**：当新旧发现描述同一招标要求但评估结果不同时使用（如一个 compliant 一个不是）

## 输出格式（必须按顺序为每个新发现输出决策，每个新发现用"新发现[N]"标记）：

新发现[1]：
决策：keep | replace | discard
理由：[详细解释为什么做出这个决策，30-100字]
替换key：[如果决策是replace，填入被替换的 requirement_key，否则填"无"]

新发现[2]：
决策：keep | replace | discard
理由：[详细解释为什么做出这个决策，30-100字]
替换key：[如果决策是replace，填入被替换的 requirement_key，否则填"无"]

...（以此类推，为每个新发现输出决策）
"""


class TaskMergeService:
    """Service for merging sub-agent results within a single task using LLM deduplication."""

    def __init__(self, agent=None):
        """Initialize the task merge service.

        Args:
            agent: BidReviewAgent instance for LLM decisions (optional)
        """
        self.agent = agent

    async def _get_llm_merge_decision(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> dict:
        """Call LLM to get merge decision.

        Args:
            new_finding: New finding to potentially merge
            existing_findings: Existing findings already merged

        Returns:
            Parsed decision dict with action, reason, replace_key
        """
        from backend.services.merge_decision_parser import parse_merge_decision

        logger.info(f"[TaskMergeService._get_llm_merge_decision] new_finding req_key={new_finding.get('requirement_key')}, existing count={len(existing_findings)}")

        if not self.agent:
            logger.warning("[TaskMergeService._get_llm_merge_decision] No agent, using keep_both")
            return {
                "action": "keep_both",
                "reason": "No agent available",
                "replace_key": None,
                "parse_failed": True,
            }

        try:
            decision_text = await self.agent.decide_merge(new_finding, existing_findings)
            logger.info(f"[TaskMergeService._get_llm_merge_decision] Raw LLM response:\n{decision_text[:300]}")
            result = parse_merge_decision(decision_text)
            logger.info(f"[TaskMergeService._get_llm_merge_decision] Parsed decision: {result}")
            return result
        except Exception as e:
            logger.warning(f"[TaskMergeService._get_llm_merge_decision] LLM failed: {e}, using keep_both")
            return {
                "action": "keep_both",
                "reason": f"LLM调用失败: {str(e)}",
                "replace_key": None,
                "parse_failed": True,
            }

    async def _batch_get_llm_merge_decisions(
        self,
        new_findings: list[dict],
        existing_findings: list[dict],
    ) -> list[dict]:
        """Batch call LLM to get merge decisions for multiple findings.

        Args:
            new_findings: List of new findings to potentially merge
            existing_findings: Existing findings already merged

        Returns:
            List of parsed decision dicts, one per new finding
        """
        from backend.services.merge_decision_parser import parse_batch_merge_decisions

        if not new_findings:
            return []

        new_findings_keys = [f.get("requirement_key", f"unknown_{i}") for i, f in enumerate(new_findings)]
        logger.info(f"[TaskMergeService._batch_get_llm_merge_decisions] {len(new_findings)} findings, existing count={len(existing_findings)}")

        if not self.agent:
            logger.warning("[TaskMergeService._batch_get_llm_merge_decisions] No agent, using keep_both for all")
            return [
                {"action": "keep_both", "reason": "No agent available", "replace_key": None, "parse_failed": True}
                for _ in new_findings
            ]

        try:
            # Build batch prompt
            prompt = BATCH_MERGE_PROMPT.format(
                new_findings=json.dumps(new_findings, ensure_ascii=False, indent=2),
                existing_findings=json.dumps(existing_findings, ensure_ascii=False, indent=2),
            )

            messages = [
                Message(role="user", content=prompt),
            ]

            logger.info(f"[TaskMergeService._batch_get_llm_merge_decisions] Calling LLM for {len(new_findings)} findings...")
            response = await self.agent._call_llm_with_retry(messages)
            logger.info(f"[TaskMergeService._batch_get_llm_merge_decisions] Raw LLM response length: {len(response.content)}")

            # Parse batch response
            decisions = parse_batch_merge_decisions(response.content, new_findings_keys)
            logger.info(f"[TaskMergeService._batch_get_llm_merge_decisions] Parsed {len(decisions)} decisions")
            return decisions

        except Exception as e:
            logger.warning(f"[TaskMergeService._batch_get_llm_merge_decisions] LLM failed: {e}, using keep_both for all")
            return [
                {"action": "keep_both", "reason": f"LLM调用失败: {str(e)}", "replace_key": None, "parse_failed": True}
                for _ in new_findings
            ]

    def _generate_new_requirement_key(self, existing_records: list[dict]) -> str:
        """Generate a new requirement key that doesn't exist in existing records.

        Args:
            existing_records: List of existing finding records

        Returns:
            New requirement key in format 'req_XXX'
        """
        max_num = 0
        for rec in existing_records:
            key = rec.get("requirement_key", "")
            if key and key.startswith("req_"):
                try:
                    num = int(key[4:])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"req_{max_num + 1:03d}"

    def _is_duplicate_content(self, new_finding: dict, existing_finding: dict) -> bool:
        """Check if new and existing findings are实质内容相同 (essentially the same).

        Args:
            new_finding: New finding
            existing_finding: Existing finding

        Returns:
            True if essentially duplicate, False otherwise
        """
        # is_compliant must be the same
        if new_finding.get("is_compliant") != existing_finding.get("is_compliant"):
            return False

        # For non-compliant, severity must be the same
        if not new_finding.get("is_compliant", True):
            new_severity = new_finding.get("severity") or ""
            existing_severity = existing_finding.get("severity") or ""
            if new_severity != existing_severity:
                return False

        # Compare bid_content
        new_bid = (new_finding.get("bid_content") or "").strip()
        existing_bid = (existing_finding.get("bid_content") or "").strip()
        if new_bid != existing_bid:
            return False

        # Compare explanation
        new_exp = (new_finding.get("explanation") or "").strip()
        existing_exp = (existing_finding.get("explanation") or "").strip()
        if new_exp != existing_exp:
            return False

        return True

    async def merge_sub_agent_results(
        self,
        findings: list[dict],
        event_callback=None,
    ) -> dict:
        """Merge findings from multiple sub-agents within a single task.

        Args:
            findings: List of findings from all sub-agents
            event_callback: Optional callback for SSE events

        Returns:
            {
                "total_findings": int,
                "critical_count": int,
                "major_count": int,
                "minor_count": int,
                "passed_count": int,
                "findings": list[dict] - merged findings
            }
        """
        logger.info(f"[TaskMergeService.merge_sub_agent_results] Starting with {len(findings)} findings")

        if event_callback:
            event_callback("merging", {"message": "正在合并子代理审核结果..."})

        if not findings:
            return {
                "total_findings": 0,
                "critical_count": 0,
                "major_count": 0,
                "minor_count": 0,
                "passed_count": 0,
                "findings": [],
            }

        # Fast path: single finding or no duplicates possible
        if len(findings) == 1:
            f = findings[0]
            return self._build_result([f])

        # Build map by requirement_key for quick duplicate detection
        by_key: dict[str, dict] = {}
        for f in findings:
            key = f.get("requirement_key", "")
            if key:
                if key not in by_key:
                    by_key[key] = []
                by_key[key].append(f)

        # Process findings with LLM-based merging
        merged_findings: list[dict] = []
        seen_content_keys: set[str] = set()  # Track by content hash for cross-key deduplication

        # First pass: collect findings that need LLM decisions (after fast-path checks)
        findings_needing_llm: list[tuple[int, dict]] = []  # (index, finding)

        # Filter out invalid findings before processing
        from backend.services.merge_decision_parser import _is_valid_finding
        valid_findings = [f for f in findings if _is_valid_finding(f)]
        if len(valid_findings) < len(findings):
            logger.info(f"[TaskMergeService] Filtered out {len(findings) - len(valid_findings)} invalid findings, {len(valid_findings)} remain")
        findings = valid_findings

        for idx, finding in enumerate(findings):
            req_key = finding.get("requirement_key", "")

            # Check for same key duplicates first (fast path)
            if req_key and req_key in by_key and len(by_key[req_key]) > 1:
                # Multiple findings with same key - check if essentially duplicate
                existing_with_key = [f for f in merged_findings if f.get("requirement_key") == req_key]
                if existing_with_key:
                    # Already have this key in merged - check for duplicate content
                    is_dup = False
                    for existing in existing_with_key:
                        if self._is_duplicate_content(finding, existing):
                            logger.info(f"[TaskMergeService] Found duplicate content for key={req_key}, discarding")
                            is_dup = True
                            break
                    if is_dup:
                        continue

            # Check content-based deduplication against all merged
            content_key = self._content_hash(finding)
            if content_key in seen_content_keys:
                logger.info(f"[TaskMergeService] Found duplicate content hash, discarding finding with key={req_key}")
                continue

            # Needs LLM decision
            findings_needing_llm.append((idx, finding))

        # Batch call LLM for all findings needing decisions
        if findings_needing_llm:
            findings_to_decide = [f for _, f in findings_needing_llm]

            # Audit log: all findings to be decided
            logger.info(f"[TaskMergeService] ========== LLM MERGE AUDIT START ==========")
            logger.info(f"[TaskMergeService] Findings to decide: {len(findings_to_decide)}")
            logger.info(f"[TaskMergeService] Existing merged findings: {len(merged_findings)}")
            logger.info(f"[TaskMergeService] --- NEW FINDINGS TO DECIDE ---")
            for i, f in enumerate(findings_to_decide):
                logger.info(f"[TaskMergeService]   [AUDIT] NewFinding[{i}]: {_format_finding_for_log(f)}")

            if merged_findings:
                logger.info(f"[TaskMergeService] --- EXISTING MERGED FINDINGS ---")
                for i, f in enumerate(merged_findings):
                    logger.info(f"[TaskMergeService]   [AUDIT] Existing[{i}]: {_format_finding_for_log(f)}")

            logger.info(f"[TaskMergeService] Calling LLM for {len(findings_to_decide)} findings...")
            decisions = await self._batch_get_llm_merge_decisions(findings_to_decide, merged_findings)

            # Apply decisions in order
            logger.info(f"[TaskMergeService] --- LLM DECISIONS ---")
            for i, (idx, finding) in enumerate(findings_needing_llm):
                req_key = finding.get("requirement_key", "")
                decision = decisions[i]
                reason = decision.get("reason", "")[:100] if decision.get("reason") else "N/A"
                replace_key = decision.get("replace_key", "N/A")
                logger.info(
                    f"[TaskMergeService] [AUDIT] Decision[{i}] key={req_key}: "
                    f"action={decision['action']}, reason={reason}..., replace_key={replace_key}"
                )
                logger.debug(f"[TaskMergeService] [AUDIT] Decision[{i}] full content: {_format_finding_for_log(finding, 300)}")

                # If merged_findings is empty (first batch), auto-keep instead of discard
                if len(merged_findings) == 0 and decision["action"] == "discard":
                    logger.info(f"[TaskMergeService] First finding auto-kept despite discard: key={req_key}")
                    decision = {"action": "keep_both", "reason": "first finding, auto-keep", "replace_key": None}

                if decision["action"] == "keep":
                    # keep = new finding is independent, add as new entry
                    new_key = self._generate_new_requirement_key(merged_findings)
                    merged = {**finding, "requirement_key": new_key}
                    merged_findings.append(merged)
                    seen_content_keys.add(self._content_hash(merged))
                    logger.info(
                        f"[TaskMergeService] [AUDIT] ACTION=keep: "
                        f"original_key={req_key} -> new_key={new_key}, "
                        f"finding={_format_finding_for_log(merged)}"
                    )
                elif decision["action"] == "replace":
                    replace_key = decision.get("replace_key")
                    target = None
                    if replace_key:
                        for rec in merged_findings:
                            if rec.get("requirement_key") == replace_key:
                                target = rec
                                break

                    if target:
                        # Create new object instead of mutating existing (immutability principle)
                        idx_in_list = merged_findings.index(target)
                        new_target = {**target, **{k: v for k, v in finding.items() if k != 'requirement_key'}, "requirement_key": replace_key}
                        merged_findings[idx_in_list] = new_target
                        seen_content_keys.discard(self._content_hash(target))
                        seen_content_keys.add(self._content_hash(new_target))
                        logger.info(
                            f"[TaskMergeService] [AUDIT] ACTION=replace: "
                            f"new_key={replace_key}, "
                            f"original_key={req_key}, "
                            f"target_removed={_format_finding_for_log(target)}, "
                            f"new_content={_format_finding_for_log(new_target)}"
                        )
                    else:
                        # Target not found, treat as keep
                        new_key = self._generate_new_requirement_key(merged_findings)
                        merged = {**finding, "requirement_key": new_key}
                        merged_findings.append(merged)
                        seen_content_keys.add(self._content_hash(merged))
                        logger.warning(
                            f"[TaskMergeService] [AUDIT] ACTION=replace_target_not_found: "
                            f"original_key={req_key} -> new_key={new_key}, "
                            f"finding={_format_finding_for_log(merged)}"
                        )
                elif decision["action"] == "keep_both":
                    new_key = self._generate_new_requirement_key(merged_findings)
                    merged = {**finding, "requirement_key": new_key}
                    merged_findings.append(merged)
                    seen_content_keys.add(self._content_hash(merged))
                    logger.info(
                        f"[TaskMergeService] [AUDIT] ACTION=keep_both: "
                        f"original_key={req_key} -> new_key={new_key}, "
                        f"finding={_format_finding_for_log(merged)}"
                    )
                elif decision["action"] == "discard":
                    logger.info(
                        f"[TaskMergeService] [AUDIT] ACTION=discard: "
                        f"key={req_key}, "
                        f"reason={decision.get('reason', 'N/A')}, "
                        f"finding={_format_finding_for_log(finding)}"
                    )

        # Sort by severity
        def sort_key(f):
            severity_order = {"critical": 0, "major": 1, "minor": 2, None: 3}
            return severity_order.get(f.get("severity"), 3)

        merged_findings.sort(key=sort_key)

        result = self._build_result(merged_findings)

        # Final audit summary
        logger.info(f"[TaskMergeService] ========== LLM MERGE AUDIT SUMMARY ==========")
        logger.info(f"[TaskMergeService] Input findings: {result['total_findings']}")
        logger.info(f"[TaskMergeService]   critical={result['critical_count']}, major={result['major_count']}, minor={result['minor_count']}, passed={result['passed_count']}")
        logger.info(f"[TaskMergeService] --- FINAL MERGED FINDINGS ---")
        for i, f in enumerate(merged_findings):
            logger.info(f"[TaskMergeService]   [FINAL] [{i}] {_format_finding_for_log(f)}")
        logger.info(f"[TaskMergeService] =============================================")

        logger.info(f"[TaskMergeService.merge_sub_agent_results] Completed: {result['total_findings']} total findings")

        if event_callback:
            event_callback("merging_completed", {"result": result})

        return result

    def _content_hash(self, finding: dict) -> str:
        """Generate a content-based hash for deduplication.

        Args:
            finding: Finding dict

        Returns:
            Hash string based on key content fields
        """
        key_parts = [
            str(finding.get("is_compliant", "")),
            str(finding.get("severity", "")),
            (finding.get("bid_content") or "").strip()[:500],  # Increased from 100 to 500
            (finding.get("explanation") or "").strip()[:500],   # Increased from 100 to 500
        ]
        return "|".join(key_parts)

    def _build_result(self, findings: list[dict]) -> dict:
        """Build standardized result dict with counts.

        Args:
            findings: List of merged findings

        Returns:
            Result dict with counts and findings list
        """
        critical_count = 0
        major_count = 0
        minor_count = 0
        passed_count = 0

        for f in findings:
            if f.get("is_compliant", True):
                passed_count += 1
            else:
                severity = f.get("severity", "minor")
                if severity == "critical":
                    critical_count += 1
                elif severity == "major":
                    major_count += 1
                else:
                    minor_count += 1

        return {
            "total_findings": len(findings),
            "critical_count": critical_count,
            "major_count": major_count,
            "minor_count": minor_count,
            "passed_count": passed_count,
            "findings": findings,
        }
