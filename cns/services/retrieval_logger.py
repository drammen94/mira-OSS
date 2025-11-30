"""
Per-turn retrieval logging for quality evaluation.

Logs retrieval results to JSONL files for offline manual review.
This enables qualitative assessment of retrieval quality without
requiring labeled ground truth data.

Temporary evaluation infrastructure - will be ablated once we have
sufficient data to assess fingerprint retrieval quality.
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from uuid import UUID

from utils.timezone_utils import utc_now
from utils.user_context import get_current_user_id

logger = logging.getLogger(__name__)


class RetrievalLogger:
    """
    Logs retrieval results for manual quality evaluation.

    Log location: data/users/{user_id}/retrieval_logs/{date}.jsonl
    """

    def __init__(self, base_path: str = "data/users"):
        self.base_path = Path(base_path)

    def log_retrieval(
        self,
        continuum_id: UUID,
        raw_query: str,
        fingerprint: str,
        surfaced_memories: List[Dict[str, Any]],
        embedding_model: str = "mdbr-leaf-ir-768d"
    ) -> None:
        """
        Log a retrieval result for later review.

        Args:
            continuum_id: ID of the current continuum
            raw_query: Original user message
            fingerprint: Expanded memory fingerprint
            surfaced_memories: List of retrieved memory dicts
            embedding_model: Model used for embeddings
        """
        user_id = get_current_user_id()

        # Build log directory
        log_dir = self.base_path / str(user_id) / "retrieval_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Date-based log file
        today = utc_now().strftime("%Y-%m-%d")
        log_file = log_dir / f"{today}.jsonl"

        # Build log entry
        entry = {
            "timestamp": utc_now().isoformat(),
            "continuum_id": str(continuum_id),
            "raw_query": raw_query,
            "fingerprint": fingerprint,
            "surfaced_memories": [
                {
                    "id": str(m.get("id", "")),
                    "text": m.get("text", "")[:200],  # Truncate for readability
                    "similarity": round(m.get("similarity_score", 0.0), 3)
                }
                for m in surfaced_memories[:10]  # Limit to top 10
            ],
            "memory_count": len(surfaced_memories),
            "embedding_model": embedding_model
        }

        # Append to JSONL
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.debug(f"Logged retrieval to {log_file}")


# Singleton instance
_retrieval_logger: RetrievalLogger = None


def get_retrieval_logger() -> RetrievalLogger:
    """Get singleton retrieval logger instance."""
    global _retrieval_logger
    if _retrieval_logger is None:
        _retrieval_logger = RetrievalLogger()
    return _retrieval_logger
