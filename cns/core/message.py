"""
Message value objects for CNS.

Immutable message representations that capture the essential business logic
without external dependencies. Timezone handling follows UTC-everywhere approach.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Union, List
from uuid import UUID, uuid4
from utils.timezone_utils import utc_now


@dataclass(frozen=True)
class Message:
    """
    Immutable message value object.
    
    Represents a single message in a continuum with proper timezone handling
    and immutable state management.
    """
    content: Union[str, List[Dict[str, Any]]]
    role: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate message on creation."""
        if self.role not in ["user", "assistant", "tool"]:
            raise ValueError(f"Invalid role: {self.role}. Must be 'user', 'assistant', or 'tool'")
        
        # Check for empty content - handle both None and empty strings
        # Allow assistant messages with tool calls but no content
        if self.content is None or (isinstance(self.content, str) and self.content.strip() == ""):
            if not (self.role == "assistant" and self.metadata.get("has_tool_calls", False)):
                raise ValueError(f"Message content cannot be empty for {self.role} messages")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": str(self.id),  # Convert UUID to string for serialization
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Message':
        """Create message from dictionary."""
        from utils.timezone_utils import parse_utc_time_string
        
        created_at = utc_now()
        if "created_at" in data:
            created_at = parse_utc_time_string(data["created_at"])
        
        return cls(
            id=UUID(data["id"]),  # ID is required, convert string to UUID
            role=data["role"],
            content=data["content"],
            created_at=created_at,
            metadata=data.get("metadata", {})
        )
    
    def with_metadata(self, **metadata_updates) -> 'Message':
        """Return new message with updated metadata."""
        new_metadata = {**self.metadata, **metadata_updates}
        return Message(
            id=self.id,
            role=self.role,
            content=self.content,
            created_at=self.created_at,
            metadata=new_metadata
        )
    
    def to_db_tuple(self, continuum_id: UUID, user_id: str) -> tuple:
        """Convert to tuple for database insertion - UUIDs handled by PostgresClient."""
        import json
        return (
            self.id,  # Keep as UUID - PostgresClient will convert
            continuum_id,  # Keep as UUID - PostgresClient will convert
            user_id,
            self.role,
            self.content if isinstance(self.content, str) else json.dumps(self.content),
            json.dumps(self.metadata) if self.metadata else '{}',  # Serialize metadata to JSON, empty object if None
            self.created_at
        )