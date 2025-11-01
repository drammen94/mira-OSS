# Event Bus System Overview

## What is the Event Bus?

The Event Bus is MIRA's central coordination mechanism that enables loose coupling between system components through publish/subscribe event-driven architecture. It orchestrates communication between CNS, Working Memory, Tool Repository, Long-Term Memory, and other MIRA components, allowing them to interact without direct dependencies. All event processing is synchronous to maintain simplicity and ensure immediate consistency.

## Architecture Overview

### Directory Structure
```
cns/
├── integration/
│   └── event_bus.py         # Event bus implementation
└── core/
    └── events.py            # Domain event definitions
```

## Core Components

### 1. **Event Bus** (`cns/integration/event_bus.py`)

The central publish/subscribe coordinator that manages event distribution:

**Key Responsibilities**:
- Event publishing to registered subscribers
- Subscription management for event types
- MIRA component integration (working memory, tool repository)
- Built-in handler registration for system coordination

**Core Methods**:
- `publish(event: ContinuumEvent)`: Broadcasts event to all subscribers
- `subscribe(event_type: str, callback: Callable)`: Registers callback for event type
- `unsubscribe(event_type: str, callback: Callable)`: Removes subscription
- `get_subscriber_count(event_type: str)`: Returns subscriber count for monitoring
- `shutdown()`: Cleanup for graceful shutdown

**Design Characteristics**:
- **Synchronous execution**: Events processed immediately (not queued)
- **Error isolation**: Subscriber failures don't affect other subscribers
- **Thread-safe**: Uses threading.Event for shutdown coordination
- **Component references**: Holds direct references to working_memory and tool_repo

### 2. **Domain Events** (`cns/core/events.py`)

Immutable event objects representing state changes in the continuum domain:

**Event Categories**:

**MessageEvent** - User and AI message processing:
- `MessageReceivedEvent`: User message received, starts processing chain
- `ResponseGeneratedEvent`: AI response generated and ready

**ToolEvent** - Tool execution and management:
- `ToolCallsRequestedEvent`: LLM requested tool calls
- `ToolsEnabledEvent`: Tools enabled by system

**WorkingMemoryEvent** - System prompt composition:
- `ComposeSystemPromptEvent`: Triggers full prompt composition
- `SystemPromptComposedEvent`: Composed prompt ready
- `UpdateTrinketEvent`: Request specific trinket update
- `TrinketContentEvent`: Trinket published content
- `WorkingMemoryUpdatedEvent`: Working memory state changed

**ContinuumCheckpointEvent** - System coordination:
- `TopicChangedEvent`: Topic changed, triggers cache operations
- `NeedToolEvent`: `<mira:need_tool />` tag detected, load all tools
- `TurnCompletedEvent`: Full turn completed (user + assistant)
- `PointerSummariesCollapsingEvent`: Summaries being coalesced

**Event Structure**:
```python
@dataclass(frozen=True, kw_only=True)
class ContinuumEvent:
    continuum_id: str         # Conversation identifier
    user_id: str             # User context for isolation
    event_id: str            # Unique event ID (UUID)
    occurred_at: datetime    # UTC timestamp via utc_now()
```

**Key Design Decisions**:
- **Immutability**: Events are frozen dataclasses, preventing modification
- **Automatic metadata**: Event ID and timestamp auto-generated via `.create()` factory methods
- **Category hierarchy**: Abstract base classes organize events by domain concern
- **Timezone consistency**: All timestamps use `utils.timezone_utils.utc_now()`

### 3. **Built-in MIRA Integrations** (`event_bus.py:51-146`)

The event bus automatically registers handlers for system coordination:

**NeedToolEvent → Enable All Tools**:
```python
def _handle_need_tool(self, event: NeedToolEvent):
    """Handle need_tool tag by enabling all tools."""
    if self.tool_repo:
        all_tools = self.tool_repo.list_all_tools()
        for tool_name in all_tools:
            if not self.tool_repo.is_tool_enabled(tool_name):
                self.tool_repo.enable_tool(tool_name)

        # Publish confirmation event
        self.publish(ToolsEnabledEvent(...))
```

**WorkingMemoryUpdatedEvent → Monitoring**:
```python
def _handle_working_memory_updated(self, event: WorkingMemoryUpdatedEvent):
    """Handle working memory updates for monitoring."""
    logger.info(f"Working memory updated: {event.updated_categories}")
    # Future: Could trigger system prompt refresh
```

## Integration Patterns

### Component Subscription Pattern

Components subscribe to events during initialization:

```python
class WorkingMemory:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._subscribe_to_events()

    def _subscribe_to_events(self):
        """Subscribe to relevant CNS events."""
        self.event_bus.subscribe('ComposeSystemPromptEvent',
                                 self._handle_compose_prompt)
        self.event_bus.subscribe('UpdateTrinketEvent',
                                 self._handle_update_trinket)
        self.event_bus.subscribe('TrinketContentEvent',
                                 self._handle_trinket_content)
```

### Event Publishing Pattern

Components publish events to notify the system of state changes:

```python
# From continuum.py
def add_user_message(self, content: str) -> tuple[Message, List[ContinuumEvent]]:
    """Add user message and generate events."""
    message = Message(content=content, role="user")
    self._message_cache.append(message)

    events = [MessageReceivedEvent.create(
        continuum_id=str(self.id),
        user_id=self.user_id,
        message_id=str(message.id),
        content=content
    )]

    return message, events

# In orchestrator - publish generated events
for event in events:
    self.event_bus.publish(event)
```

### Cross-Component Coordination Pattern

Events enable complex workflows without direct dependencies:

**System Prompt Composition Flow**:
```
Orchestrator publishes ComposeSystemPromptEvent
    ↓
WorkingMemory handles event, publishes UpdateTrinketEvent for each trinket
    ↓
Trinkets handle UpdateTrinketEvent, publish TrinketContentEvent
    ↓
WorkingMemory collects TrinketContentEvent, composes prompt
    ↓
WorkingMemory publishes SystemPromptComposedEvent
    ↓
Orchestrator receives composed prompt
```

## Data Flow

### Event Lifecycle

```
Component creates event → Event.create() factory method
    ↓
Auto-generates event_id and occurred_at timestamp
    ↓
Component publishes via event_bus.publish(event)
    ↓
Event bus looks up subscribers for event.__class__.__name__
    ↓
Event bus calls each subscriber synchronously
    ↓
Subscribers handle event (isolated error handling)
    ↓
Control returns to publisher
```

### Message Processing Flow with Events

```
User Input → API → Orchestrator.process_user_message()
    ↓
Continuum.add_user_message() → MessageReceivedEvent
    ↓
Event bus publishes MessageReceivedEvent to subscribers
    ↓
Orchestrator publishes ComposeSystemPromptEvent
    ↓
WorkingMemory composes prompt → SystemPromptComposedEvent
    ↓
Orchestrator calls LLM with composed prompt
    ↓
Response received → Continuum.add_assistant_message()
    ↓
ResponseGeneratedEvent published
    ↓
Orchestrator publishes TurnCompletedEvent
    ↓
Domain Knowledge service buffers turn for Letta
```

### Topic Change Flow

```
Tag Parser detects <mira:topic_changed/> in assistant response
    ↓
Orchestrator sets continuum.set_topic_changed(True)
    ↓
Returns TopicChangedEvent
    ↓
Event bus publishes TopicChangedEvent
    ↓
Cache Event Handler receives event
    ↓
Triggers cache summarization and pruning
    ↓
Progressive Hot Cache Manager generates topic summary
    ↓
Cache updated with summary, old messages removed
```

## Integration with MIRA Components

### Working Memory Integration

**Event Subscriptions**:
- `ComposeSystemPromptEvent`: Triggers trinket updates and composition
- `UpdateTrinketEvent`: Routes to specific trinkets
- `TrinketContentEvent`: Collects trinket output for composition

**Events Published**:
- `SystemPromptComposedEvent`: Composed prompt ready
- `WorkingMemoryUpdatedEvent`: State changed notification

### Tool Repository Integration

**Event Subscriptions**:
- `NeedToolEvent` (via event bus built-in handler): Enables all tools

**Events Published**:
- `ToolsEnabledEvent`: Tools enabled confirmation
- `ToolCallsRequestedEvent`: LLM requested tool execution

### Long-Term Memory Integration

**Event Subscriptions**:
- `TurnCompletedEvent`: Triggers memory extraction (future)
- `PointerSummariesCollapsingEvent`: Summaries ready for extraction

**Events Published**:
- Currently none (memory system operates on scheduled batch processing)
- Future: Memory extraction events for real-time memory creation

### Progressive Hot Cache Integration

**Event Subscriptions**:
- `TopicChangedEvent`: Triggers cache summarization and pruning

**Events Published**:
- `PointerSummariesCollapsingEvent`: Before coalescing summaries

### Domain Knowledge Integration

**Event Subscriptions**:
- `TurnCompletedEvent`: Buffers messages to Letta agents

**Events Published**:
- Currently none (Letta integration is write-only)

## Key Design Decisions

### Synchronous vs Asynchronous

**Decision**: All event processing is synchronous

**Rationale**:
- Simpler to reason about - no concurrency bugs
- Immediate consistency - handlers see correct state
- Easier debugging - linear execution flow
- No queue management complexity
- Performance is adequate for MIRA's scale

**Trade-offs**:
- Publisher blocks until all subscribers complete
- Long-running handlers delay publisher
- Mitigation: Keep handlers fast, move heavy work to background tasks

### Event Data Carriage

**Decision**: Events carry state directly rather than just identifiers

**Rationale** (from `TurnCompletedEvent` docstring):
> "Events describing state changes should carry the changed state. Requiring handlers to re-fetch state introduces race conditions - the event may be published before persistence completes, causing handlers to see stale data."

**Pattern**:
- `MessageReceivedEvent` carries `content` directly
- `TurnCompletedEvent` carries `continuum` object reference
- `SystemPromptComposedEvent` carries `composed_prompt`

**Benefits**:
- Handlers get correct state regardless of persistence timing
- No race conditions between publish and database commits
- Reduced database queries
- Self-contained event logs for debugging

### Event Category Organization

**Decision**: Four top-level event categories with inheritance hierarchy

**Categories**:
1. **MessageEvent**: User/AI communication
2. **ToolEvent**: Tool execution lifecycle
3. **WorkingMemoryEvent**: Prompt composition and trinkets
4. **ContinuumCheckpointEvent**: System coordination

**Rationale** (from `events.py:7-15`):
> "Future events should fit into these categories. Only create new categories if there's a fundamentally different type of system interaction that doesn't fit the above. Resist the urge to create one-off events - adapt existing categories instead."

**Benefits**:
- Clear domain boundaries
- Easy to find related events
- Prevents event proliferation
- Enables category-level subscriptions (future)

### Component References in Event Bus

**Decision**: Event bus holds direct references to `working_memory` and `tool_repo`

**Rationale**:
- Enables built-in integration handlers without circular dependencies
- Simplifies factory initialization order
- Centralizes MIRA component coordination

**Pattern**:
```python
# In CNSIntegrationFactory
event_bus = self._get_event_bus()
working_memory = self._get_working_memory(event_bus)
tool_repo = self._get_tool_repository(working_memory)

# Configure event bus with components
event_bus.working_memory = working_memory
event_bus.tool_repo = tool_repo
event_bus._register_mira_integrations()
```

### Factory Method Pattern for Events

**Decision**: Events use `.create()` class methods instead of direct instantiation

**Pattern**:
```python
@classmethod
def create(cls, continuum_id: str, user_id: str,
           content: str) -> 'MessageReceivedEvent':
    """Create event with auto-generated metadata."""
    return cls(
        continuum_id=continuum_id,
        user_id=user_id,
        event_id=str(uuid4()),
        occurred_at=utc_now(),
        content=content
    )
```

**Benefits**:
- Consistent event ID and timestamp generation
- Enforces UTC timezone via `utc_now()`
- Simpler call sites (fewer parameters)
- Future-proof for validation logic

## Usage Examples

### Publishing Events from Aggregates

```python
# In continuum.py
def add_user_message(self, content: str) -> tuple[Message, List[ContinuumEvent]]:
    """Add user message and return events for orchestrator to publish."""
    message = Message(content=content, role="user")
    self._message_cache.append(message)

    events = [MessageReceivedEvent.create(
        continuum_id=str(self.id),
        user_id=self.user_id,
        message_id=str(message.id),
        content=content
    )]

    return message, events
```

### Subscribing to Events

```python
# In cache_event_handler.py
class CacheEventHandler:
    def __init__(self, continuum_pool, cache_manager, event_bus):
        self.continuum_pool = continuum_pool
        self.cache_manager = cache_manager
        self.event_bus = event_bus

        # Subscribe to topic changes
        self.event_bus.subscribe('TopicChangedEvent',
                                 self._handle_topic_changed)

    def _handle_topic_changed(self, event: TopicChangedEvent):
        """Handle topic change by triggering cache operations."""
        continuum = self.continuum_pool.get_continuum(event.user_id)
        self.cache_manager.on_topic_changed(continuum)
```

### Coordinating Multi-Step Workflows

```python
# In working_memory/core.py
def _handle_compose_prompt(self, event: ComposeSystemPromptEvent):
    """Compose system prompt by coordinating trinkets."""
    # Set base
    self.composer.set_base_prompt(event.base_prompt)
    self.composer.clear_sections(preserve_base=True)

    # Request updates from all trinkets (synchronous)
    for trinket_name in self._trinkets.keys():
        self.event_bus.publish(UpdateTrinketEvent.create(
            continuum_id=event.continuum_id,
            user_id=event.user_id,
            target_trinket=trinket_name,
            context={}
        ))

    # All trinkets have updated synchronously, compose
    composed_prompt = self.composer.compose()

    # Publish result
    self.event_bus.publish(SystemPromptComposedEvent.create(
        continuum_id=event.continuum_id,
        user_id=event.user_id,
        composed_prompt=composed_prompt
    ))
```

## Monitoring and Debugging

### Subscriber Introspection

```python
# Check subscriber count for an event
count = event_bus.get_subscriber_count('TopicChangedEvent')
logger.info(f"TopicChangedEvent has {count} subscribers")

# List all event types with subscribers
event_types = event_bus.get_all_event_types()
logger.info(f"Event bus tracking: {event_types}")
```

### Event Logging

All event publishes and subscription operations are logged:

```python
# From event_bus.py
logger.debug(f"Publishing event: {event_type} - {event}")
logger.debug(f"Event {event_type} published to {len(subscribers)} subscribers")
logger.debug(f"Subscribed to {event_type} events")
```

### Error Isolation

Subscriber errors don't affect other subscribers:

```python
for callback in self._subscribers[event_type]:
    try:
        callback(event)
    except Exception as e:
        logger.error(f"Error in event subscriber for {event_type}: {e}")
        # Continue to next subscriber
```

## Future Enhancements

### Planned Improvements

1. **Async Event Support**: Optional async callback support for I/O-bound handlers
2. **Event Replay**: Persist events for debugging and system reconstruction
3. **Event Filtering**: Subscribe to event categories, not just specific types
4. **Metrics Collection**: Built-in event throughput and latency monitoring
5. **Event Versioning**: Handle event schema evolution gracefully

### Architectural Considerations

**Event Sourcing**: The event bus provides foundation for event sourcing pattern:
- Events already carry state changes
- Events are immutable and timestamped
- Factory integration could persist events to database
- Continuum state could be reconstructed from event log

**CQRS Pattern**: Event bus enables Command Query Responsibility Segregation:
- Commands trigger state changes (add_user_message)
- State changes generate events (MessageReceivedEvent)
- Read models subscribe to events and update projections
- Current architecture already separates writes (Continuum) from reads (caches)

## Related Documentation

- **[CNS System Overview](cns_system_overview.md)**: Event bus role in conversation orchestration
- **[Working Memory System](working_memory_system_overview.md)**: Trinket coordination via events
- **[Domain Events Pattern](../ADRs/)**: Domain-Driven Design event patterns
