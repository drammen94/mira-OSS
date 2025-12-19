# CNS Event-Driven Architecture

*Technical architecture for MIRA's Central Nervous System*

---

## Overview

The CNS (Central Nervous System) is MIRA's event-driven conversation management system. It coordinates message flow, working memory composition, LLM interaction, and segment lifecycle through a synchronous event bus architecture.

---

## Architecture Layers

### Core Layer (`cns/core/`)
- **Continuum** (`continuum.py:18-192`) - Aggregate root managing conversation state
- **Message** (`message.py:14-88`) - Immutable value object for messages
- **ContinuumState** (`state.py:15-44`) - Frozen dataclass with continuum ID, user_id, metadata
- **Events** (`events.py:1-307`) - Complete event type hierarchy

### Integration Layer (`cns/integration/`)
- **EventBus** (`event_bus.py:20-139`) - Central pub/sub mechanism
- **CNSIntegrationFactory** (`factory.py:30-402`) - Dependency injection factory

### Infrastructure Layer (`cns/infrastructure/`)
- **ContinuumRepository** - PostgreSQL persistence with RLS
- **ContinuumPool** - Valkey hot cache manager
- **ValkeyMessageCache** - Session caching

### Services Layer (`cns/services/`)
- **ContinuumOrchestrator** (`orchestrator.py:24-646`) - Main conversation orchestration
- **SegmentCollapseHandler** (`segment_collapse_handler.py:32-501`) - Segment lifecycle
- **SegmentTimeoutService** (`segment_timeout_service.py:22-171`) - Inactivity detection
- **ManifestQueryService** (`manifest_query_service.py:39-173`) - Segment retrieval with caching

### API Layer (`cns/api/`)
- **ChatEndpoint** (`chat.py`) - HTTP endpoint for messages
- **WebSocketChat** (`websocket_chat.py`) - Streaming WebSocket endpoint
- Health, tool config, actions, data APIs

---

## Event Bus Implementation

**File**: `cns/integration/event_bus.py` (lines 20-139)

### Class: EventBus

```python
class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._shutdown_event = threading.Event()

    def publish(self, event: ContinuumEvent) -> None:
        """Publish event to all subscribers synchronously."""
        event_type = event.__class__.__name__
        for callback in self._subscribers.get(event_type, []):
            try:
                callback(event)
            except Exception as e:
                self.logger.error(f"Error in subscriber: {e}")

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Register callback for event type."""
        self._subscribers.setdefault(event_type, []).append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Remove callback from event type."""
```

### Key Characteristics

| Characteristic | Implementation |
|----------------|----------------|
| **Execution model** | Synchronous - callbacks execute immediately in publishing thread (line 63-65) |
| **Error isolation** | Catches exceptions, logs errors, continues to next subscriber (line 64-67) |
| **Routing** | String-based using `event.__class__.__name__` (line 57) |
| **Built-in handler** | Registers handler for `WorkingMemoryUpdatedEvent` at init (line 42) |

---

## Event Hierarchy

**File**: `cns/core/events.py` (lines 1-307)

### Base Class: ContinuumEvent (lines 23-29)

```python
class ContinuumEvent:
    continuum_id: str
    user_id: str
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=utc_now)
```

### Event Categories

```
ContinuumEvent (base)
├── MessageEvent (placeholder)
├── ToolEvent (placeholder)
├── WorkingMemoryEvent
│   ├── WorkingMemoryUpdatedEvent
│   ├── ComposeSystemPromptEvent
│   ├── SystemPromptComposedEvent
│   ├── UpdateTrinketEvent
│   └── TrinketContentEvent
└── ContinuumCheckpointEvent
    ├── TurnCompletedEvent
    ├── PointerSummariesCollapsingEvent
    ├── SegmentTimeoutEvent
    ├── SegmentCollapsedEvent
    └── ManifestUpdatedEvent
```

---

## Concrete Events

### Working Memory Events

#### ComposeSystemPromptEvent (lines 143-159)
**Fired when**: System prompt composition requested
**Fired by**: `ContinuumOrchestrator` (orchestrator.py line 225)
**Subscribers**: `WorkingMemory._handle_compose_prompt`
**Payload**: `base_prompt: str`

#### SystemPromptComposedEvent (lines 162-180)
**Fired when**: System prompt composition complete
**Fired by**: `WorkingMemory` (core.py line 114)
**Subscribers**: `ContinuumOrchestrator._handle_system_prompt_composed`
**Payload**: `cached_content: str`, `non_cached_content: str`

#### UpdateTrinketEvent (lines 183-201)
**Fired when**: Specific trinket update requested
**Fired by**: `WorkingMemory` or `ContinuumOrchestrator`
**Subscribers**: `WorkingMemory._handle_update_trinket`
**Payload**: `target_trinket: str`, `context: Dict[str, Any]`

#### TrinketContentEvent (lines 204-226)
**Fired when**: Trinket publishes composed content
**Fired by**: Individual trinkets via base class
**Subscribers**: `WorkingMemory._handle_trinket_content`
**Payload**: `variable_name: str`, `content: str`, `trinket_name: str`, `cache_policy: bool`

### Checkpoint Events

#### TurnCompletedEvent (lines 64-109)
**Fired when**: User message + assistant response complete
**Fired by**: `ContinuumOrchestrator` (orchestrator.py line 435-440)
**Subscribers**: `GetContextTrinket._handle_turn_completed`, `ToolLoaderTrinket._handle_turn_completed`
**Payload**: `turn_number: int`, `segment_turn_number: int`, `continuum: Any`

**Architectural note** (lines 73-77): Event carries entire continuum object to prevent race conditions where handlers re-fetch stale data.

#### SegmentTimeoutEvent (lines 229-257)
**Fired when**: Segment inactivity threshold reached
**Fired by**: `SegmentTimeoutService` (segment_timeout_service.py line 77)
**Subscribers**: `SegmentCollapseHandler.handle_timeout`
**Payload**: `segment_id: str`, `inactive_duration_minutes: int`, `local_hour: int`

#### SegmentCollapsedEvent (lines 260-287)
**Fired when**: Segment successfully collapsed to manifest
**Fired by**: `SegmentCollapseHandler` (segment_collapse_handler.py line 161)
**Subscribers**: `GetContextTrinket._handle_segment_collapsed`
**Payload**: `segment_id: str`, `summary: str`, `tools_used: List[str]`

**Architectural note** (lines 265-268): Clears all search results in GetContextTrinket to prevent context leakage.

#### ManifestUpdatedEvent (lines 290-306)
**Fired when**: Manifest structure changed
**Fired by**: `SegmentCollapseHandler` (segment_collapse_handler.py line 180)
**Subscribers**: `ManifestQueryService._handle_manifest_updated`
**Payload**: `segment_count: int`

---

## Stream Events

**File**: `cns/core/stream_events.py` (lines 12-106)

For streaming LLM responses:

| Event | Lines | Payload |
|-------|-------|---------|
| `StreamEvent` | 12-16 | Base class |
| `TextEvent` | 19-24 | `content: str` |
| `ThinkingEvent` | 27-32 | `content: str` (extended thinking) |
| `ToolDetectedEvent` | 35-41 | `tool_name`, `tool_id` |
| `ToolExecutingEvent` | 44-51 | `tool_name`, `tool_id`, `arguments: Dict` |
| `ToolCompletedEvent` | 54-61 | `tool_name`, `tool_id`, `result: str` |
| `ToolErrorEvent` | 64-71 | `tool_name`, `tool_id`, `error: str` |
| `CompleteEvent` | 74-79 | `response: Dict[str, Any]` |
| `ErrorEvent` | 82-88 | `error: str`, `technical_details: Optional[str]` |
| `CircuitBreakerEvent` | 91-96 | `reason: str` |
| `RetryEvent` | 99-106 | `attempt`, `max_attempts`, `reason` |

---

## Continuum Aggregate Root

**File**: `cns/core/continuum.py` (lines 18-192)

The Continuum is the aggregate root - an immutable entity holding conversation state.

### State

- `_state: ContinuumState` - ID, user_id, metadata
- `_message_cache: List[Message]` - Hot cache of recent messages

### Key Methods

| Method | Lines | Purpose |
|--------|-------|---------|
| `add_user_message(content)` | 67-80 | Add user message, returns `(Message, List[Event])` |
| `add_assistant_message(content, metadata)` | 82-99 | Add assistant response |
| `add_tool_message(content, tool_call_id)` | 101-119 | Add tool result |
| `get_messages_for_api()` | 121-182 | Convert to Anthropic API format |
| `apply_cache(messages)` | 55-65 | External cache update |

### Properties

- `id: UUID` (line 41-43)
- `user_id: str` (line 45-48)
- `messages: List[Message]` (line 50-53) - Returns hot cache

---

## Conversation Flow

### End-to-End Message Processing

**Files**: `cns/api/chat.py`, `cns/services/orchestrator.py`

#### Phase 1: API Entry (`chat.py`)

```python
# Line 67: Set user context
set_current_user_id(user_id)

# Line 136-138: Increment segment turn counter
segment_turn_number = continuum_pool.repository.increment_segment_turn(
    continuum.id, user_id
)

# Pass to orchestrator
continuum, final_response, metadata = orchestrator.process_message(...)
```

#### Phase 2: Memory Surfacing (`orchestrator.py:126-218`)

```python
# Line 126: Add user message to continuum cache
user_msg_obj, user_events = continuum.add_user_message(user_message)

# Lines 174-178: Generate fingerprint for retrieval
fingerprint, pinned_ids = self.fingerprint_generator.generate_fingerprint(
    continuum, text_for_context, previous_memories=previous_memories
)

# Lines 188-192: Get fresh memories from long-term memory
fresh_memories = self.memory_relevance_service.get_relevant_memories(
    fingerprint=fingerprint, fingerprint_embedding=fingerprint_embedding, limit=20
)

# Lines 214-218: Update ProactiveMemoryTrinket
self.event_bus.publish(UpdateTrinketEvent.create(
    continuum_id=str(continuum.id),
    target_trinket="ProactiveMemoryTrinket",
    context={"memories": surfaced_memories}
))
```

#### Phase 3: System Prompt Composition (`orchestrator.py:225-232`)

```python
# Publish compose request
self.event_bus.publish(ComposeSystemPromptEvent.create(
    continuum_id=str(continuum.id),
    base_prompt=system_prompt
))

# Handler populates cached_content, non_cached_content synchronously
```

#### Phase 4: LLM Invocation (`orchestrator.py:309-394`)

```python
for event in self.llm_provider.stream_events(
    messages=complete_messages,
    tools=available_tools,
    **llm_kwargs
):
    # Process stream events
    # Collect tool executions, text, thinking content
```

#### Phase 5: Response Processing (`orchestrator.py:402-441`)

```python
# Parse response tags and extract referenced memories
parsed_tags = self.tag_parser.parse_response(response_text)
clean_response_text = parsed_tags['clean_text']

# Add assistant response to continuum
assistant_msg_obj, response_events = continuum.add_assistant_message(
    clean_response_text, assistant_metadata
)

# Publish turn completed event
self._publish_events([TurnCompletedEvent.create(
    continuum_id=str(continuum.id),
    turn_number=turn_number,
    segment_turn_number=segment_turn_number,
    continuum=continuum
)])
```

#### Phase 6: Persistence (`orchestrator.py:453-485`)

```python
# Add messages to unit of work
unit_of_work.add_messages(persist_user_msg, assistant_msg_obj)
unit_of_work.mark_metadata_updated()
```

---

## Event Handlers

### WorkingMemory Handlers (`working_memory/core.py`)

| Handler | Event | Actions |
|---------|-------|---------|
| `_handle_compose_prompt` (74-119) | `ComposeSystemPromptEvent` | Personalizes base prompt, requests trinket updates, composes final prompt |
| `_handle_update_trinket` (122-159) | `UpdateTrinketEvent` | Routes to specific trinket's `handle_update_request()` |
| `_handle_trinket_content` (161-171) | `TrinketContentEvent` | Adds section to composer with cache policy |

### Trinket Handlers

| Handler | Trinket | Event | Actions |
|---------|---------|-------|---------|
| `_handle_turn_completed` | GetContextTrinket | `TurnCompletedEvent` | Increments turn counter, expires old results |
| `_handle_segment_collapsed` | GetContextTrinket | `SegmentCollapsedEvent` | Clears all active_results state |
| `_handle_turn_completed` | ToolLoaderTrinket | `TurnCompletedEvent` | Triggers tool auto-unload |

### Service Handlers

| Handler | Service | Event | Actions |
|---------|---------|-------|---------|
| `handle_timeout` (68-198) | SegmentCollapseHandler | `SegmentTimeoutEvent` | Generates summary, collapses segment, publishes downstream events |
| `_handle_manifest_updated` (64-76) | ManifestQueryService | `ManifestUpdatedEvent` | Invalidates Valkey cache |
| `_handle_system_prompt_composed` (513-519) | ContinuumOrchestrator | `SystemPromptComposedEvent` | Stores composed content |

---

## Event Publishing Patterns

### From Orchestrator

```python
# Update specific trinket (orchestrator.py lines 213-218)
self.event_bus.publish(UpdateTrinketEvent.create(
    continuum_id=str(continuum.id),
    target_trinket="ProactiveMemoryTrinket",
    context={"memories": surfaced_memories}
))

# Request prompt composition (lines 225-228)
self.event_bus.publish(ComposeSystemPromptEvent.create(
    continuum_id=str(continuum.id),
    base_prompt=system_prompt
))

# Signal turn completion (lines 435-440)
self._publish_events([TurnCompletedEvent.create(
    continuum_id=str(continuum.id),
    turn_number=turn_number,
    segment_turn_number=segment_turn_number,
    continuum=continuum
)])
```

### From WorkingMemory

```python
# Request trinket updates (core.py lines 103-108)
for trinket_name in self._trinkets.keys():
    self.event_bus.publish(UpdateTrinketEvent.create(
        continuum_id=event.continuum_id,
        target_trinket=trinket_name,
        context={'user_id': event.user_id}
    ))

# Publish composed prompt (lines 114-118)
self.event_bus.publish(SystemPromptComposedEvent.create(
    continuum_id=event.continuum_id,
    cached_content=structured['cached_content'],
    non_cached_content=structured['non_cached_content']
))
```

### From SegmentCollapseHandler

```python
# Segment collapsed (segment_collapse_handler.py lines 161-166)
self.event_bus.publish(SegmentCollapsedEvent.create(
    continuum_id=str(continuum_id),
    segment_id=segment_id,
    summary=summary,
    tools_used=tools_used
))

# Manifest updated (lines 180-183)
self.event_bus.publish(ManifestUpdatedEvent.create(
    continuum_id=str(continuum_id),
    segment_count=segment_count
))
```

---

## Event Subscription Patterns

```python
# WorkingMemory (core.py lines 50-58)
self.event_bus.subscribe('ComposeSystemPromptEvent', self._handle_compose_prompt)
self.event_bus.subscribe('UpdateTrinketEvent', self._handle_update_trinket)
self.event_bus.subscribe('TrinketContentEvent', self._handle_trinket_content)

# GetContextTrinket (getcontext_trinket.py lines 38-39)
self.event_bus.subscribe('TurnCompletedEvent', self._handle_turn_completed)
self.event_bus.subscribe('SegmentCollapsedEvent', self._handle_segment_collapsed)

# SegmentCollapseHandler (segment_collapse_handler.py line 68)
self.event_bus.subscribe('SegmentTimeoutEvent', self.handle_timeout)

# ManifestQueryService (manifest_query_service.py line 61)
self.event_bus.subscribe('ManifestUpdatedEvent', self._handle_manifest_updated)

# ContinuumOrchestrator (orchestrator.py line 82)
self.event_bus.subscribe('SystemPromptComposedEvent', self._handle_system_prompt_composed)
```

---

## Architectural Patterns

### Synchronous Execution
All event callbacks execute synchronously in the publishing thread (event_bus.py lines 63-65). No async/await or background queues. This ensures system prompt composition completes before LLM invocation.

### State Carrying in Events
`TurnCompletedEvent` carries the continuum object directly (events.py lines 73-77) to prevent race conditions where handlers re-fetch stale data.

### User Context Propagation
`set_current_user_id()` called at API boundaries. Database RLS enforces user isolation automatically. Contextvars flow through event handlers.

### Fail-Fast Infrastructure
Database, embeddings, fingerprint generation failures propagate immediately. No degraded modes for required infrastructure.

### Event Isolation
Trinket handler failures don't stop other trinkets (core.py lines 142-157). Events continue propagating even if individual handlers fail.

### Cache Invalidation via Events
`ManifestUpdatedEvent` triggers cache deletion in `ManifestQueryService`. Next query rebuilds from database.

---

## Summary: Events & Subscribers

| Event | Publisher | Subscriber(s) |
|-------|-----------|---------------|
| `ComposeSystemPromptEvent` | Orchestrator | WorkingMemory |
| `SystemPromptComposedEvent` | WorkingMemory | Orchestrator |
| `UpdateTrinketEvent` | WorkingMemory, Orchestrator | WorkingMemory (routes to trinket) |
| `TrinketContentEvent` | Individual trinkets | WorkingMemory |
| `TurnCompletedEvent` | Orchestrator | GetContextTrinket, ToolLoaderTrinket |
| `SegmentTimeoutEvent` | SegmentTimeoutService | SegmentCollapseHandler |
| `SegmentCollapsedEvent` | SegmentCollapseHandler | GetContextTrinket |
| `ManifestUpdatedEvent` | SegmentCollapseHandler | ManifestQueryService |

---

*Implementation: `cns/core/events.py` (event definitions), `cns/integration/event_bus.py` (pub/sub), `cns/core/continuum.py` (aggregate), `cns/services/orchestrator.py` (orchestration), `working_memory/core.py` (working memory handlers)*
