# CNS (Central Nervous System) Overview

## What is CNS?

CNS (Central Nervous System) is MIRA's event-driven conversation orchestration framework. It coordinates all conversation processing, from user input to final response, managing system interactions through clean interfaces and domain events. The architecture follows Domain-Driven Design principles with clear separation of concerns.

## Architecture Overview

### Directory Structure
```
cns/
├── api/              # REST and WebSocket endpoints
├── core/             # Domain models and events
├── infrastructure/   # Data persistence layer
├── integration/      # Event bus and factories
└── services/         # Business logic and orchestration
```

## Core Components

### 1. **Domain Events** (`cns/core/events.py`)

CNS uses immutable domain events to represent state changes, enabling loose coupling between components:

- **MessageEvent**: User/AI message processing (MessageReceivedEvent, ResponseGeneratedEvent)
- **ToolEvent**: Tool execution and management (ToolCallsRequestedEvent, ToolsEnabledEvent)
- **WorkingMemoryEvent**: Memory updates and trinket operations (ComposeSystemPromptEvent, SystemPromptComposedEvent)
- **WorkflowEvent**: Workflow detection and state transitions (WorkflowDetectedEvent)
- **ContinuumCheckpointEvent**: System coordination (TopicChangedEvent, NeedToolEvent)

Each event includes:
- `continuum_id`: Unique conversation identifier
- `user_id`: User context for isolation
- `event_id`: Unique event identifier
- `occurred_at`: UTC timestamp

### 2. **Event Bus** (`cns/integration/event_bus.py`)

The event bus provides publish/subscribe infrastructure for system coordination:

- **Synchronous execution**: Events are processed immediately (not queued)
- **Component integration**: Bridges CNS with MIRA components (tool relevance, working memory, workflow manager)
- **Built-in handlers**:
  - `TopicChangedEvent` → Notifies tool relevance service, flushes context
  - `NeedToolEvent` → Enables all available tools
  - `WorkflowDetectedEvent` → Updates working memory with workflow context
  - `UpdateTrinketEvent` → Routes updates to specific trinkets

### 3. **Continuum Model** (`cns/core/continuum.py`)

The Continuum aggregate root manages continuum state with immutability:

- **State management**: Encapsulates continuum metadata, workflow ID, topic status
- **Message cache**: Cache of recent messages with lazy loading
- **Event generation**: All state changes produce domain events
- **Multimodal support**: Handles both text and image content
- **Repository pattern**: Clean separation between domain logic and persistence

Key methods:
- `add_user_message()`: Adds user message, generates MessageReceivedEvent
- `add_assistant_message()`: Adds AI response, generates ResponseGeneratedEvent
- `set_topic_changed()`: Marks topic change, generates TopicChangedEvent
- `get_messages_for_api()`: Returns formatted messages for LLM calls

### 4. **Continuum Orchestrator** (`cns/services/orchestrator.py`)

The orchestrator coordinates the entire continuum flow:

**Processing Pipeline**:
1. **Message Reception**: Add user message to continuum
2. **Context Building**: Create weighted context from conversation history
3. **Embedding Generation**: Generate embeddings once, propagate to all services
4. **Memory Surfacing**: Find relevant long-term memories using embeddings
5. **Workflow Detection**: Identify active workflows from user input
6. **System Prompt Composition**: Trigger working memory to compose prompt
7. **Tool Selection**: Use tool relevance service to select appropriate tools
8. **LLM Interaction**: Send to LLM with tools and context
9. **Response Processing**: Handle tool calls, parse tags, save response

**Key Features**:
- Single embedding generation for efficiency
- Embedded message propagation to all services
- Tag parsing for system instructions (`<mira:topic_changed/>`, `<mira:need_tool/>`)
- Metadata collection for analytics

### 5. **Continuum Repository** (`cns/infrastructure/continuum_repository.py`)

Handles persistence with Row Level Security (RLS):

- **Singleton pattern**: Single instance with connection pooling
- **User isolation**: Automatic RLS enforcement via PostgresClient
- **Message persistence**: Stores messages with metadata and embeddings
- **Summary management**: Handles topic and coalesced summaries
- **Search support**: Enables conversational search across messages

Key methods:
- `get_or_create()`: Gets existing or creates new continuum
- `save_message()`: Persists message with immediate commit
- `load_recent_messages()`: Loads messages for hot cache
- `get_messages_since()`: Retrieves messages after timestamp

### 6. **Tag Parser** (`cns/services/tag_parser.py`)

Parses special instructions in LLM responses:

- `<mira:topic_changed/>`: Triggers topic change event and cache management
- `<mira:need_tool/>`: Loads all available tools for next interaction
- Custom tag support for future extensions

### 7. **Summary Generator** (`cns/services/summary_generator.py`)

Creates intelligent summaries for cache management:

- **Topic summaries**: Concise single-topic summaries (~150 tokens)
- **Coalesced summaries**: Multi-topic overviews with sliding windows
- **Metadata preservation**: Maintains topic boundaries and relationships
- **Token optimization**: Balances detail vs. context window usage

### 8. **Pointer Summary Extraction** (`lt_memory/pointer_summary_extraction.py`)

Transforms topic summaries into long-term memories without blocking the cache:

- **Pre-coalescence hook**: `ProgressiveHotCacheManager` publishes a `PointerSummariesCollapsingEvent` before pointer summaries leave the hot cache.
- **Asynchronous worker**: `PointerSummaryExtractionCoordinator` subscribes to the event bus, rehydrates the original messages, and queues extraction work on a background thread.
- **Metadata tracking**: Processed summaries receive an `lt_memory_extraction` status stamp to prevent duplicate extraction when sliding windows overlap.
- **Batch processing**: `MemoryExtractionService.process_message_batch()` reuses existing chunking, scoring, and storage logic for the hydrated message set.

## Data Flow

### User Message Processing
```
User Input → API Endpoint → Orchestrator → Continuum.add_user_message()
    ↓
MessageReceivedEvent → Event Bus → Subscribers
    ↓
Context Building → Embedding Generation → Memory Search
    ↓
Tool Selection → System Prompt Composition → LLM Call
    ↓
Response → Tag Parsing → Continuum.add_assistant_message()
    ↓
ResponseGeneratedEvent → Event Bus → API Response
```

### Topic Change Flow
```
Tag Parser detects <mira:topic_changed/> → TopicChangedEvent
    ↓
Event Bus → Tool Relevance Service (flush context)
```

## Key Design Principles

1. **Event-Driven Architecture**: Loose coupling through domain events
2. **Domain-Driven Design**: Clear bounded contexts and aggregate roots
3. **Immutability**: Events and core domain objects are immutable
4. **User Isolation**: Complete data separation at infrastructure level
5. **Single Responsibility**: Each component has one clear purpose
6. **Dependency Injection**: Components receive dependencies, don't create them
7. **Repository Pattern**: Clean separation of domain logic from persistence

## Integration Points

### With MIRA Components
- **Tool Relevance Service**: Receives context updates and topic changes
- **Working Memory**: Composed through event-driven trinket updates
- **Workflow Manager**: Detected workflows update working memory
- **LT Memory**: Memories surfaced using shared embeddings
- **Tool Repository**: Dynamic tool enabling/disabling

### API Layer
- RESTful endpoints for conversation management
- WebSocket support for real-time streaming
- Health checks and debugging endpoints
- Search API for conversation history

## Configuration

Key configuration in `config.py` for system settings.

## Benefits

1. **Scalability**: Event-driven architecture supports horizontal scaling
2. **Maintainability**: Clear separation of concerns and single responsibility
3. **Extensibility**: New features added by subscribing to events
4. **Performance**: Single embedding generation, efficient caching
5. **Debugging**: Event stream provides complete audit trail
6. **Flexibility**: Components can evolve independently
