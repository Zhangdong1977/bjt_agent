"""ClusterManager: online incremental clustering for experience cases."""

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from mini_agent.schema import Message

from backend.config import get_settings
from backend.services.llm_factory import create_llm_client
from backend.experience.prompts.cluster_assign_zh import CLUSTER_ASSIGN_PROMPT

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.65
LLM_SKIP_THRESHOLD = 0.85
LLM_TOP_K_CLUSTERS = 5


class ClusterManager:
    def __init__(self, llm_client=None, embedding_service=None):
        self._llm_client = llm_client or create_llm_client()
        self._embedding_service = embedding_service
        self._settings = get_settings()

    async def assign_cluster(self, case, db) -> str:
        existing_clusters = await self._load_clusters(case.group_id, db)

        if not existing_clusters:
            cluster_id = f"cluster_{case.group_id}_{uuid.uuid4().hex[:8]}"
            await self._save_membership(case.id, cluster_id, case.group_id, "llm", None, db)
            logger.info(f"Created first cluster {cluster_id} for group {case.group_id}")
            return cluster_id

        if self._embedding_service:
            best_match = await self._embedding_recall(case, existing_clusters)
            if best_match and best_match["similarity"] >= LLM_SKIP_THRESHOLD:
                await self._save_membership(
                    case.id, best_match["cluster_id"], case.group_id,
                    "embedding", best_match["similarity"], db,
                )
                logger.info(
                    f"Case {case.id} assigned to cluster {best_match['cluster_id']} "
                    f"via embedding (sim={best_match['similarity']:.3f})"
                )
                return best_match["cluster_id"]

            candidates = [c for c in existing_clusters if c.get("similarity", 0) >= SIMILARITY_THRESHOLD]
            if not candidates:
                candidates = sorted(existing_clusters, key=lambda x: x.get("similarity", 0), reverse=True)[:LLM_TOP_K_CLUSTERS]
        else:
            candidates = existing_clusters[:LLM_TOP_K_CLUSTERS]

        if candidates:
            cluster_id = await self._llm_assign(case, candidates, db)
            if cluster_id and cluster_id != "new":
                similarity = None
                for c in candidates:
                    if c["cluster_id"] == cluster_id:
                        similarity = c.get("similarity")
                        break
                await self._save_membership(
                    case.id, cluster_id, case.group_id, "llm", similarity, db,
                )
                return cluster_id

        cluster_id = f"cluster_{case.group_id}_{uuid.uuid4().hex[:8]}"
        await self._save_membership(case.id, cluster_id, case.group_id, "llm", None, db)
        logger.info(f"Created new cluster {cluster_id} for group {case.group_id}")
        return cluster_id

    async def _load_clusters(self, group_id: str, db) -> list[dict]:
        from sqlalchemy import select, func
        from backend.experience.models import ExperienceClusterMembership, ExperienceCase

        result = await db.execute(
            select(
                ExperienceClusterMembership.cluster_id,
                func.count(ExperienceClusterMembership.case_id).label("case_count"),
            ).where(
                ExperienceClusterMembership.group_id == group_id,
            ).group_by(
                ExperienceClusterMembership.cluster_id,
            )
        )
        clusters = []
        for row in result.all():
            cluster_id = row.cluster_id
            case_result = await db.execute(
                select(ExperienceCase.task_intent).where(
                    ExperienceCase.id.in_(
                        select(ExperienceClusterMembership.case_id).where(
                            ExperienceClusterMembership.cluster_id == cluster_id
                        )
                    )
                ).limit(3)
            )
            representative_intents = [r.task_intent for r in case_result.all()]
            clusters.append({
                "cluster_id": cluster_id,
                "case_count": row.case_count,
                "representative_intents": representative_intents,
            })
        return clusters

    async def _embedding_recall(self, case, clusters: list[dict]) -> dict | None:
        if not self._embedding_service:
            return None

        try:
            case_embedding = await self._embedding_service.embed(case.task_intent)
            best_match = None
            best_sim = 0.0

            for cluster in clusters:
                cluster_embedding = await self._compute_cluster_centroid(cluster, case.group_id)
                if cluster_embedding is None:
                    continue
                sim = self._cosine_similarity(case_embedding, cluster_embedding)
                cluster["similarity"] = sim
                if sim > best_sim:
                    best_sim = sim
                    best_match = cluster

            return best_match
        except Exception as e:
            logger.warning(f"Embedding recall failed: {e}")
            return None

    async def _compute_cluster_centroid(self, cluster: dict, group_id: str) -> list[float] | None:
        return None

    async def _llm_assign(self, case, candidates: list[dict], db) -> str | None:
        candidates_text = ""
        for i, c in enumerate(candidates, 1):
            intents = "\n".join(f"  - {intent}" for intent in c.get("representative_intents", []))
            candidates_text += f"候选簇 {i} (ID: {c['cluster_id']}, 含 {c.get('case_count', 0)} 个案例):\n{intents}\n\n"

        prompt = CLUSTER_ASSIGN_PROMPT.format(
            case_summary=f"意图: {case.task_intent}\n方法: {getattr(case, 'approach', '')[:500]}",
            candidate_clusters=candidates_text,
        )

        try:
            messages = [
                Message(role="system", content="你是审查经验聚类分析器。仅输出 JSON。"),
                Message(role="user", content=prompt),
            ]
            response = await self._llm_client.generate(messages=messages)
            result = self._parse_json(response.content)
            return result.get("cluster_id")
        except Exception as e:
            logger.warning(f"LLM cluster assignment failed: {e}")
            return None

    async def _save_membership(
        self, case_id: str, cluster_id: str, group_id: str,
        assigned_by: str, similarity: float | None, db,
    ) -> None:
        from backend.experience.models import ExperienceClusterMembership

        membership = ExperienceClusterMembership(
            case_id=case_id,
            cluster_id=cluster_id,
            group_id=group_id,
            assigned_by=assigned_by,
            similarity_score=similarity,
            assigned_at=datetime.now(timezone.utc),
        )
        db.add(membership)
        await db.flush()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _parse_json(self, content: str) -> dict:
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        return {}
