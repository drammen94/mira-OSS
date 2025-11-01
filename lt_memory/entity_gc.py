"""
Entity garbage collection service.

Identifies dormant low-link entities, finds merge candidates using
vector + string + co-occurrence similarity, and orchestrates batch
LLM review for merge/delete decisions.
"""
import json
import logging
from typing import List, Dict, Any, Optional
from uuid import UUID
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from lt_memory.models import Entity
from config.config import EntityGarbageCollectionConfig
from lt_memory.db_access import LTMemoryDB
from lt_memory.entity_extraction import EntityExtractor
from clients.llm_provider import LLMProvider
from utils.user_context import get_current_user_id

logger = logging.getLogger(__name__)


class MergeCandidate(BaseModel):
    """Candidate entity for merging with dormant entity."""
    entity_id: UUID
    name: str
    entity_type: str
    link_count: int
    vector_similarity: float = Field(ge=0.0, le=1.0)
    string_similarity: float = Field(ge=0.0, le=1.0)
    co_occurrence_score: float = Field(ge=0.0, le=1.0)
    combined_score: float = Field(ge=0.0, le=1.0)


class EntityGCService:
    """
    Service for entity garbage collection.

    Identifies dormant entities, finds merge candidates using triple scoring
    (vector + string + co-occurrence), and orchestrates LLM review for
    merge/delete decisions.
    """

    def __init__(
        self,
        config: EntityGarbageCollectionConfig,
        db: LTMemoryDB,
        entity_extractor: EntityExtractor,
        llm_provider: LLMProvider
    ):
        """
        Initialize entity GC service.

        Args:
            config: Entity GC configuration
            db: Database access layer
            entity_extractor: Entity extractor for embeddings
            llm_provider: LLM provider for review
        """
        self.config = config
        self.db = db
        self.entity_extractor = entity_extractor
        self.llm_provider = llm_provider

    def find_dormant_entities(self) -> List[Entity]:
        """
        Find entities eligible for garbage collection.

        Dormant entities are those with:
        - No new links in N days (configured)
        - Low link count (between min and max thresholds)
        - Not archived

        Returns:
            List of dormant Entity models
        """
        user_id = get_current_user_id()

        dormant = self.db.find_dormant_entities(
            days_dormant=self.config.dormancy_days,
            min_link_count=self.config.min_link_count_for_gc,
            max_link_count=self.config.max_link_count_for_gc,
            user_id=user_id
        )

        logger.info(f"Found {len(dormant)} dormant entities for user {user_id}")
        return dormant

    def find_merge_candidates(
        self,
        dormant_entity: Entity
    ) -> List[MergeCandidate]:
        """
        Find merge candidates using triple scoring.

        Combines three similarity signals:
        1. Vector similarity (semantic equivalence) - 40% weight
        2. String similarity (spelling variations) - 30% weight
        3. Co-occurrence (shared memories) - 30% weight

        Only considers entities of the same type (PERSON with PERSON, etc.)

        Args:
            dormant_entity: Dormant entity to find candidates for

        Returns:
            List of MergeCandidate models, sorted by combined_score descending
        """
        user_id = get_current_user_id()
        candidates = []

        # Get vector similarity candidates (same type only via post-filter)
        if dormant_entity.embedding:
            vector_candidates = self.db.find_entities_by_vector_similarity(
                query_embedding=dormant_entity.embedding,
                limit=50,  # Cast wide net, filter later
                similarity_threshold=self.config.vector_similarity_threshold,
                user_id=user_id
            )

            # Filter to same type and exclude self
            vector_candidates = [
                e for e in vector_candidates
                if e.entity_type == dormant_entity.entity_type
                and e.id != dormant_entity.id
            ]
        else:
            vector_candidates = []

        if not vector_candidates:
            logger.debug(
                f"No vector candidates for dormant entity {dormant_entity.name} "
                f"({dormant_entity.entity_type})"
            )
            return []

        # Get memories for dormant entity (for co-occurrence calculation)
        dormant_memories = self.db.get_memories_for_entity(
            dormant_entity.id,
            user_id=user_id
        )
        dormant_memory_ids = {str(m.id) for m in dormant_memories}

        # Score each candidate
        for candidate in vector_candidates:
            # Vector similarity (already computed)
            vector_sim = candidate.similarity_score or 0.0

            # String similarity (normalized)
            string_sim = fuzz.ratio(
                dormant_entity.name.lower(),
                candidate.name.lower()
            ) / 100.0

            # Co-occurrence: Jaccard similarity of shared memories
            candidate_memories = self.db.get_memories_for_entity(
                candidate.id,
                user_id=user_id
            )
            candidate_memory_ids = {str(m.id) for m in candidate_memories}

            shared_count = len(dormant_memory_ids & candidate_memory_ids)
            union_count = len(dormant_memory_ids | candidate_memory_ids)
            co_occurrence = shared_count / union_count if union_count > 0 else 0.0

            # Check thresholds
            if string_sim < self.config.string_similarity_threshold:
                continue
            if co_occurrence < self.config.co_occurrence_threshold:
                continue

            # Weighted combination (vector 40%, string 30%, co-occurrence 30%)
            combined = (
                0.4 * vector_sim +
                0.3 * string_sim +
                0.3 * co_occurrence
            )

            candidates.append(MergeCandidate(
                entity_id=candidate.id,
                name=candidate.name,
                entity_type=candidate.entity_type,
                link_count=candidate.link_count,
                vector_similarity=vector_sim,
                string_similarity=string_sim,
                co_occurrence_score=co_occurrence,
                combined_score=combined
            ))

        # Sort by combined score descending, limit to max candidates
        candidates.sort(key=lambda c: c.combined_score, reverse=True)
        return candidates[:self.config.max_merge_candidates]

    def build_gc_review_batch(
        self,
        dormant_entities: List[Entity]
    ) -> Dict[str, Any]:
        """
        Build batch API payload for entity GC review.

        Creates prompts for LLM to review each dormant entity with its
        merge candidates and linked memories.

        Args:
            dormant_entities: List of dormant entities to review

        Returns:
            Dictionary with batch payload and entity mapping
        """
        user_id = get_current_user_id()
        requests = []
        entity_map = {}  # custom_id → entity data

        for entity in dormant_entities:
            # Find merge candidates
            candidates = self.find_merge_candidates(entity)

            # Get linked memories for context
            memories = self.db.get_memories_for_entity(entity.id, user_id=user_id)
            memory_texts = [m.text for m in memories[:5]]  # Limit to 5 for context

            # Build prompt
            custom_id = f"{user_id}_gc_{str(entity.id)[:8]}"

            system_prompt = """You are an entity garbage collection system. Review dormant entities and decide whether to merge, keep, or delete them.

Guidelines:
- MERGE: Entity is a variation of another (e.g., "PostgreSQL" vs "Postgres")
- KEEP: Entity is valid and distinct, even if rarely used
- DELETE: Entity is noise, typo, or extraction error

Default to KEEP when uncertain. Be conservative with merging - only merge when clearly the same entity."""

            candidate_lines = []
            if candidates:
                for c in candidates:
                    candidate_lines.append(
                        f"  - {c.name} ({c.link_count} links) - "
                        f"vector: {c.vector_similarity:.2f}, "
                        f"string: {c.string_similarity:.2f}, "
                        f"co-occur: {c.co_occurrence_score:.2f}, "
                        f"combined: {c.combined_score:.2f}"
                    )
            else:
                candidate_lines.append("  (no candidates found)")

            memory_context = "\n".join([f"  - {text}" for text in memory_texts]) if memory_texts else "  (no memories)"

            user_prompt = f"""Dormant Entity:
Name: "{entity.name}"
Type: {entity.entity_type}
Link Count: {entity.link_count}
Last Linked: {entity.last_linked_at or 'never'}

Merge Candidates (high-link entities, same type):
{chr(10).join(candidate_lines)}

Linked Memories (context):
{memory_context}

Decision: Should this entity be merged, kept, or deleted?

Respond with JSON:
{{
    "action": "merge|keep|delete",
    "merge_target_id": "uuid-if-merging",
    "reason": "Brief explanation"
}}"""

            requests.append({
                "custom_id": custom_id,
                "params": {
                    "model": self.config.gc_model,
                    "max_tokens": self.config.gc_max_tokens,
                    "temperature": self.config.gc_temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "response_format": {"type": "json_object"}
                }
            })

            entity_map[custom_id] = {
                "entity_id": str(entity.id),
                "name": entity.name,
                "entity_type": entity.entity_type,
                "link_count": entity.link_count,
                "candidates": [
                    {
                        "entity_id": str(c.entity_id),
                        "name": c.name,
                        "combined_score": c.combined_score
                    }
                    for c in candidates
                ]
            }

        return {
            "requests": requests,
            "entity_map": entity_map
        }

    def process_gc_review_results(
        self,
        results: List[Dict[str, Any]],
        entity_map: Dict[str, Any]
    ) -> Dict[str, int]:
        """
        Process LLM review results and execute merge/delete decisions.

        Args:
            results: LLM batch results (custom_id → response)
            entity_map: Entity data mapping from build_gc_review_batch

        Returns:
            Statistics: {"merged": N, "deleted": N, "kept": N, "errors": N}
        """
        user_id = get_current_user_id()
        stats = {"merged": 0, "deleted": 0, "kept": 0, "errors": 0}

        for custom_id, response_text in results.items():
            try:
                # Parse LLM response
                decision = json.loads(response_text)
                action = decision.get("action", "keep").lower()
                reason = decision.get("reason", "No reason provided")

                # Get entity data
                entity_data = entity_map.get(custom_id)
                if not entity_data:
                    logger.warning(f"No entity data for {custom_id}")
                    stats["errors"] += 1
                    continue

                entity_id = UUID(entity_data["entity_id"])
                entity_name = entity_data["name"]

                if action == "merge":
                    merge_target_id = decision.get("merge_target_id")
                    if not merge_target_id:
                        logger.warning(
                            f"Merge action for {entity_name} missing target_id, keeping instead"
                        )
                        stats["kept"] += 1
                        continue

                    # Execute merge
                    self.db.merge_entities(
                        source_id=entity_id,
                        target_id=UUID(merge_target_id),
                        user_id=user_id
                    )
                    logger.info(
                        f"Merged entity {entity_name} ({entity_id}) into {merge_target_id}: {reason}"
                    )
                    stats["merged"] += 1

                elif action == "delete":
                    # Archive entity (soft delete for safety)
                    self.db.archive_entity(entity_id, user_id=user_id)
                    logger.info(f"Archived entity {entity_name} ({entity_id}): {reason}")
                    stats["deleted"] += 1

                else:  # keep or unknown action
                    logger.debug(f"Keeping entity {entity_name} ({entity_id}): {reason}")
                    stats["kept"] += 1

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in GC result {custom_id}: {e}")
                stats["errors"] += 1
            except Exception as e:
                logger.error(f"Error processing GC result {custom_id}: {e}", exc_info=True)
                stats["errors"] += 1

        return stats

    def run_entity_gc_for_user(self) -> Dict[str, int]:
        """
        Run full entity GC cycle for user.

        Finds dormant entities, builds review batch, submits to LLM,
        and processes results.

        Returns:
            Statistics: {"merged": N, "deleted": N, "kept": N, "errors": N}

        Raises:
            Exception: If GC operations fail
        """
        user_id = get_current_user_id()

        # Find dormant entities
        dormant = self.find_dormant_entities()

        if not dormant:
            logger.info(f"No dormant entities for user {user_id}")
            return {"merged": 0, "deleted": 0, "kept": 0, "errors": 0}

        # Build review batch
        batch_payload = self.build_gc_review_batch(dormant)

        if not batch_payload["requests"]:
            logger.info(f"No GC review requests for user {user_id}")
            return {"merged": 0, "deleted": 0, "kept": 0, "errors": 0}

        # Process each request synchronously (monthly job, can afford latency)
        results = {}
        for request in batch_payload["requests"]:
            response = self.llm_provider.generate_response(
                messages=request["params"]["messages"],
                system=request["params"]["system"],
                temperature=request["params"]["temperature"],
                max_tokens=request["params"]["max_tokens"],
                response_format=request["params"].get("response_format")
            )

            response_text = self.llm_provider.extract_text_content(response)
            results[request["custom_id"]] = response_text

        # Process results
        stats = self.process_gc_review_results(
            results,
            batch_payload["entity_map"]
        )

        logger.info(
            f"Entity GC complete for user {user_id}: "
            f"{stats['merged']} merged, {stats['deleted']} deleted, "
            f"{stats['kept']} kept, {stats['errors']} errors"
        )

        return stats

    def cleanup(self):
        """
        Clean up service resources.

        No-op: Dependencies managed by factory lifecycle.
        Nulling references breaks in-flight scheduler jobs.
        """
        logger.debug("EntityGCService cleanup completed (no-op)")
