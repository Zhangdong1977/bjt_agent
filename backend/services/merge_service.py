"""Merge service for combining historical review results."""

import json
import logging
import uuid
from datetime import datetime

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from mini_agent.schema import Message

from backend.models import Project, ReviewTask, ReviewResult, ProjectReviewResult

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

logger = logging.getLogger(__name__)


class MergeService:
    """Service for merging historical review results with LLM deduplication."""

    def __init__(self, db: AsyncSession, agent=None):
        """Initialize the merge service.

        Args:
            db: Database session
            agent: BidReviewAgent instance for LLM decisions (optional for backwards compatibility)
        """
        self.db = db
        self.agent = agent

    async def _get_llm_merge_decision(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> dict:
        """调用 LLM 获取合并决策。

        Args:
            new_finding: 新发现
            existing_findings: 现有发现列表

        Returns:
            解析后的决策字典
        """
        from backend.services.merge_decision_parser import parse_merge_decision

        logger.info(f"[_get_llm_merge_decision] new_finding req_key={new_finding.get('requirement_key')}, existing_findings count={len(existing_findings)}")

        if not self.agent:
            # Fallback if no agent provided
            logger.warning("[_get_llm_merge_decision] No agent provided for LLM merge decision, using keep_both")
            return {
                "action": "keep_both",
                "reason": "No agent available",
                "replace_key": None,
                "parse_failed": True,
            }

        try:
            logger.info(f"[_get_llm_merge_decision] Calling agent.decide_merge...")
            decision_text = await self.agent.decide_merge(new_finding, existing_findings)
            logger.info(f"[_get_llm_merge_decision] Raw LLM response:\n{decision_text[:300]}")
            result = parse_merge_decision(decision_text)
            logger.info(f"[_get_llm_merge_decision] Parsed decision: {result}")
            return result
        except Exception as e:
            logger.warning(f"[_get_llm_merge_decision] LLM merge decision failed: {e}, using keep_both strategy")
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
        logger.info(f"[MergeService._batch_get_llm_merge_decisions] {len(new_findings)} findings, existing count={len(existing_findings)}")

        if not self.agent:
            logger.warning("[MergeService._batch_get_llm_merge_decisions] No agent, using keep_both for all")
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

            logger.info(f"[MergeService._batch_get_llm_merge_decisions] Calling agent._call_llm_with_retry for {len(new_findings)} findings...")
            response = await self.agent._call_llm_with_retry(messages=messages)
            logger.info(f"[MergeService._batch_get_llm_merge_decisions] Raw LLM response length: {len(response.content)}")

            # Parse batch response
            decisions = parse_batch_merge_decisions(response.content, new_findings_keys)
            logger.info(f"[MergeService._batch_get_llm_merge_decisions] Parsed {len(decisions)} decisions")
            return decisions

        except Exception as e:
            logger.warning(f"[MergeService._batch_get_llm_merge_decisions] LLM failed: {e}, using keep_both for all")
            return [
                {"action": "keep_both", "reason": f"LLM调用失败: {str(e)}", "replace_key": None, "parse_failed": True}
                for _ in new_findings
            ]

    def _generate_new_requirement_key(self, existing_records: list[dict]) -> str:
        """Generate a new requirement key that doesn't exist in existing records.

        Args:
            existing_records: List of existing ProjectReviewResult records

        Returns:
            New requirement key in format 'req_XXX' where XXX is next sequential number
        """
        max_num = 0
        for rec in existing_records:
            key = rec.get("requirement_key", "")
            if key.startswith("req_"):
                try:
                    num = int(key[4:])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"req_{max_num + 1:03d}"

    def _is_duplicate_content(self, new_finding: dict, existing_finding: dict) -> bool:
        """判断新旧发现是否实质内容相同。

        实质内容相同意味着：
        - is_compliant 相同
        - severity 相同（如果不是 compliant）
        - bid_content 相似
        - explanation 相似

        Args:
            new_finding: 新发现
            existing_finding: 现有发现

        Returns:
            True 如果实质内容相同（应该 discard），False 如果不同（应该 compare further）
        """
        # is_compliant 必须相同才可能是重复
        if new_finding.get("is_compliant") != existing_finding.get("is_compliant"):
            return False

        # 如果是不合规的，severity 必须相同
        if not new_finding.get("is_compliant", True):
            new_severity = new_finding.get("severity") or ""
            existing_severity = existing_finding.get("severity") or ""
            if new_severity != existing_severity:
                return False

        # 比较 bid_content（忽略微小差异）
        new_bid = (new_finding.get("bid_content") or "").strip()
        existing_bid = (existing_finding.get("bid_content") or "").strip()
        if new_bid != existing_bid:
            # 如果 bid_content 不同，可能不是重复
            return False

        # 比较 explanation（允许一定差异）
        new_exp = (new_finding.get("explanation") or "").strip()
        existing_exp = (existing_finding.get("explanation") or "").strip()
        if new_exp != existing_exp:
            # explanation 不同，可能是不同的评估
            return False

        # 所有关键字段都相同，判断为重复
        return True

    async def merge_project_results(
        self,
        project_id: str,
        latest_task_id: str,
        event_callback=None,
    ) -> tuple[int, int]:
        """Merge all historical review results for a project.

        Args:
            project_id: Project ID
            latest_task_id: The most recently completed task ID
            event_callback: Optional callback for SSE events

        Returns:
            Tuple of (merged_count, total_count)
        """
        logger.info(f"[merge] ========== merge_project_results START ==========")
        logger.info(f"[merge] project_id={project_id}, latest_task_id={latest_task_id}")

        # Send merging event
        if event_callback:
            event_callback("merging", {"message": "正在合并历史结果..."})

        # Get all historical ReviewResult for this project
        historical_results = await self._get_historical_results(project_id)
        logger.info(f"[merge] Found {len(historical_results)} historical ReviewResult records")
        for i, r in enumerate(historical_results):
            logger.info(f"[merge] historical_results[{i}]: task_id={r.get('task_id')}, req_key={r.get('requirement_key')}, compliant={r.get('is_compliant')}, severity={r.get('severity')}")

        # Get existing ProjectReviewResult records
        existing_merged = await self._get_existing_merged(project_id)
        logger.info(f"[merge] Found {len(existing_merged)} existing ProjectReviewResult records")
        for i, r in enumerate(existing_merged):
            logger.info(f"[merge] existing_merged[{i}]: req_key={r.get('requirement_key')}, compliant={r.get('is_compliant')}, severity={r.get('severity')}, merged_from={r.get('merged_from_count')}")

        # Process new results from latest task
        latest_results = [r for r in historical_results if r["task_id"] == latest_task_id]
        logger.info(f"[merge] latest_results count: {len(latest_results)}, keys: {[r.get('requirement_key') for r in latest_results]}")
        for i, r in enumerate(latest_results):
            logger.info(f"[merge] latest_results[{i}]: req_key={r.get('requirement_key')}, compliant={r.get('is_compliant')}, severity={r.get('severity')}, bid_content={str(r.get('bid_content', ''))[:100]}")

        # Fast path: first task with no existing merged records
        if len(existing_merged) == 0:
            logger.info("[merge] First task detected, using fast path without LLM decision")
            if event_callback:
                event_callback("merging", {"message": "首次入库，无需合并..."})

            now = datetime.utcnow()
            for record in latest_results:
                prr = ProjectReviewResult(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    requirement_key=record["requirement_key"],
                    requirement_content=record["requirement_content"],
                    bid_content=record.get("bid_content"),
                    is_compliant=record.get("is_compliant", False),
                    severity=record["severity"],
                    location_page=record.get("location_page"),
                    location_line=record.get("location_line"),
                    suggestion=record.get("suggestion"),
                    explanation=record.get("explanation"),
                    source_task_id=record["task_id"],
                    merged_from_count=1,
                    created_at=now,
                    updated_at=now,
                )
                self.db.add(prr)

            await self.db.commit()

            if event_callback:
                event_callback("merged", {
                    "merged_count": len(latest_results),
                    "total_count": len(latest_results),
                })

            logger.info(f"[merge] Fast path completed: {len(latest_results)} records inserted")
            return len(latest_results), len(latest_results)

        # Build map of existing by requirement_key
        existing_by_key: dict[str, dict] = {}
        for rec in existing_merged:
            key = rec.get("requirement_key", "")
            if key:
                existing_by_key[key] = rec

        logger.info(f"[merge] existing_by_key count: {len(existing_by_key)}, keys: {list(existing_by_key.keys())}")
        for key, rec in existing_by_key.items():
            logger.info(f"[merge] existing_by_key['{key}']: compliant={rec.get('is_compliant')}, severity={rec.get('severity')}")

        # 先复制所有现有记录作为基础
        new_merged_records: list[dict] = []
        for rec in existing_merged:
            new_merged_records.append({**rec})

        merge_count = 0

        # First pass: collect findings that need LLM decisions (fast duplicate checks done inline)
        findings_to_decide: list[tuple[int, dict]] = []  # (index, finding) for order preservation

        for new_result in latest_results:
            req_key = new_result.get("requirement_key", "")

            if req_key in existing_by_key:
                # 当 requirement_key 相同时，说明新旧发现可能评估的是同一个招标要求
                # 快速检查：比较新旧发现的实质内容是否相同
                existing = existing_by_key[req_key]
                logger.info(f"[merge] key={req_key} exists in existing_by_key, checking duplicate content")

                if self._is_duplicate_content(new_result, existing):
                    # 实质内容相同，直接 discard
                    logger.info(f"[merge] key={req_key}: 发现实质内容重复，自动 discard")
                    continue

                # 内容不同，需要 LLM 决策
                findings_to_decide.append((len(findings_to_decide), new_result))
            else:
                # 新 key，需要 LLM 决策
                findings_to_decide.append((len(findings_to_decide), new_result))

        # Batch call LLM for all findings needing decisions
        if findings_to_decide:
            findings_list = [f for _, f in findings_to_decide]
            logger.info(f"[merge] Batch calling LLM for {len(findings_list)} findings...")
            decisions = await self._batch_get_llm_merge_decisions(findings_list, new_merged_records)

            # Apply decisions in order
            for i, (orig_idx, new_result) in enumerate(findings_to_decide):
                req_key = new_result.get("requirement_key", "")
                decision = decisions[i]
                logger.info(f"[merge] Batch LLM decision for key={req_key}: action={decision['action']}, reason={decision.get('reason', '')[:100]}, replace_key={decision.get('replace_key')}")

                if decision["action"] == "keep":
                    # keep = 作为独立条目添加（不同的招标要求）
                    new_key = self._generate_new_requirement_key(new_merged_records)
                    merged_record = {**new_result, "requirement_key": new_key, "merged_from_count": 1}
                    new_merged_records.append(merged_record)
                    merge_count += 1
                    logger.info(f"[merge] ACTION=keep: generated new key={new_key}, added to merged records")
                elif decision["action"] == "replace":
                    # replace = 更新现有的那条记录
                    replace_key = decision.get("replace_key") or req_key
                    target_record = None
                    target_idx = None
                    for idx, rec in enumerate(new_merged_records):
                        if rec.get("requirement_key") == replace_key:
                            target_record = rec
                            target_idx = idx
                            break
                    if target_record:
                        # Immutable update: create new record instead of mutating existing
                        new_record = {
                            **target_record,
                            **new_result,
                            "requirement_key": replace_key,
                            "merged_from_count": target_record.get("merged_from_count", 1) + 1,
                        }
                        new_merged_records[target_idx] = new_record
                        merge_count += 1
                        logger.info(f"[merge] ACTION=replace: updated record at key={replace_key}, new merged_from_count={new_record.get('merged_from_count')}")
                    else:
                        logger.warning(f"[merge] replace target {replace_key} not found, treating as keep")
                        new_key = self._generate_new_requirement_key(new_merged_records)
                        merged_record = {**new_result, "requirement_key": new_key, "merged_from_count": 1}
                        new_merged_records.append(merged_record)
                        merge_count += 1
                elif decision["action"] == "discard":
                    # discard = 丢弃新发现
                    logger.info(f"[merge] ACTION=discard: discarded new_result")
                elif decision["action"] == "keep_both":
                    # keep_both = 保留两条记录
                    new_key = self._generate_new_requirement_key(new_merged_records)
                    merged_record = {**new_result, "requirement_key": new_key, "merged_from_count": 1}
                    new_merged_records.append(merged_record)
                    merge_count += 1
                    logger.info(f"[merge] ACTION=keep_both: generated new key={new_key}")

        logger.info(f"[merge] After processing: new_merged_records count={len(new_merged_records)}")
        for i, rec in enumerate(new_merged_records):
            logger.info(f"[merge] final_merged[{i}]: req_key={rec.get('requirement_key')}, compliant={rec.get('is_compliant')}, severity={rec.get('severity')}, merged_from={rec.get('merged_from_count')}")

        # Delete all existing ProjectReviewResult for this project
        logger.info(f"[merge] DELETING all existing ProjectReviewResult for project_id={project_id}")
        await self.db.execute(
            delete(ProjectReviewResult).where(ProjectReviewResult.project_id == project_id)
        )
        logger.info(f"[merge] DELETED existing records")

        # Insert merged records
        logger.info(f"[merge] INSERTING {len(new_merged_records)} records into ProjectReviewResult")
        now = datetime.utcnow()
        for idx, record in enumerate(new_merged_records):
            prr = ProjectReviewResult(
                id=record.get("id") or str(uuid.uuid4()),
                project_id=project_id,
                requirement_key=record["requirement_key"],
                requirement_content=record["requirement_content"],
                bid_content=record.get("bid_content"),
                is_compliant=record.get("is_compliant", False),
                severity=record["severity"],
                location_page=record.get("location_page"),
                location_line=record.get("location_line"),
                suggestion=record.get("suggestion"),
                explanation=record.get("explanation"),
                source_task_id=record["task_id"],
                merged_from_count=record.get("merged_from_count", 1),
                created_at=now,
                updated_at=now,
            )
            self.db.add(prr)

        await self.db.commit()
        logger.info(f"[merge] COMMIT completed")

        # Send merged event
        if event_callback:
            event_callback("merged", {
                "merged_count": merge_count,
                "total_count": len(new_merged_records),
            })

        logger.info(f"[merge] ========== merge_project_results END ==========")
        logger.info(f"[merge] Completed: {merge_count} merged, {len(new_merged_records)} total records")
        return merge_count, len(new_merged_records)

    async def _get_historical_results(self, project_id: str) -> list[dict]:
        """Get all ReviewResult records for a project's tasks."""
        result = await self.db.execute(
            select(ReviewResult)
            .join(ReviewTask, ReviewTask.id == ReviewResult.task_id)
            .where(ReviewTask.project_id == project_id)
        )
        records = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "task_id": str(r.task_id),
                "requirement_key": r.requirement_key,
                "requirement_content": r.requirement_content,
                "bid_content": r.bid_content,
                "is_compliant": r.is_compliant,
                "severity": r.severity,
                "location_page": r.location_page,
                "location_line": r.location_line,
                "suggestion": r.suggestion,
                "explanation": r.explanation,
            }
            for r in records
        ]

    async def _get_existing_merged(self, project_id: str) -> list[dict]:
        """Get existing ProjectReviewResult records for a project."""
        result = await self.db.execute(
            select(ProjectReviewResult).where(ProjectReviewResult.project_id == project_id)
        )
        records = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "requirement_key": r.requirement_key,
                "requirement_content": r.requirement_content,
                "bid_content": r.bid_content,
                "is_compliant": r.is_compliant,
                "severity": r.severity,
                "location_page": r.location_page,
                "location_line": r.location_line,
                "suggestion": r.suggestion,
                "explanation": r.explanation,
                "task_id": str(r.source_task_id),
                "merged_from_count": r.merged_from_count,
            }
            for r in records
        ]
