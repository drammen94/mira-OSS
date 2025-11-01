"""
Stream event types for LLM provider streaming.

Provides a clean, type-safe event hierarchy for streaming responses
through the LLM pipeline.
"""
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class StreamEvent:
    """Base event for all streaming events."""
    type: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class TextEvent(StreamEvent):
    """Text content chunk from LLM."""
    content: str
    type: str = field(default="text", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class ThinkingEvent(StreamEvent):
    """Thinking content chunk from LLM with extended thinking enabled."""
    content: str
    type: str = field(default="thinking", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class ToolDetectedEvent(StreamEvent):
    """Tool detected in LLM response."""
    tool_name: str
    tool_id: str
    type: str = field(default="tool_detected", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class ToolExecutingEvent(StreamEvent):
    """Tool execution started."""
    tool_name: str
    tool_id: str
    arguments: Dict[str, Any]
    type: str = field(default="tool_executing", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class ToolCompletedEvent(StreamEvent):
    """Tool execution completed successfully."""
    tool_name: str
    tool_id: str
    result: str
    type: str = field(default="tool_completed", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class ToolErrorEvent(StreamEvent):
    """Tool execution failed."""
    tool_name: str
    tool_id: str
    error: str
    type: str = field(default="tool_error", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class CompleteEvent(StreamEvent):
    """Stream completed with final response."""
    response: Dict[str, Any]
    type: str = field(default="complete", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class ErrorEvent(StreamEvent):
    """Stream error occurred."""
    error: str
    technical_details: Optional[str] = None
    type: str = field(default="error", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class CircuitBreakerEvent(StreamEvent):
    """Circuit breaker triggered during tool execution."""
    reason: str
    type: str = field(default="circuit_breaker", init=False)
    timestamp: float = field(default_factory=time.time, init=False)


@dataclass
class RetryEvent(StreamEvent):
    """Retry attempt for malformed tool calls."""
    attempt: int
    max_attempts: int
    reason: str
    type: str = field(default="retry", init=False)
    timestamp: float = field(default_factory=time.time, init=False)
