"""
Immutable state management for CNS continuums.

Provides immutable state objects and controlled state transitions
for continuum data.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID

from .message import Message


@dataclass(frozen=True)
class ContinuumState:
    """
    Immutable continuum state.

    Represents all continuum data with immutable updates only.
    No direct mutations allowed - use with_* methods for state changes.
    """
    id: UUID
    user_id: str

    # Continuum metadata - flexible dict for extensible state
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for persistence."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ContinuumState':
        """Create state from dictionary."""
        return cls(
            id=UUID(data["id"]),
            user_id=data["user_id"],
            metadata=data.get("metadata", {})
        )