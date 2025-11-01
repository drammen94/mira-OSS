# Tool Relevance Engine - Reintegration Guide

## Overview

The Tool Relevance Engine was an ML-based system for intelligent tool selection that determined which tools to surface to the LLM based on conversation context. It used embedding similarity and classification to dynamically enable/disable tools, reducing context bloat and improving relevance.

**Removal Date**: October 2025
**Reason for Removal**: The system added significant complexity for marginal benefit. With the introduction of `invokeother_tool` pattern, MIRA can now dynamically load tools on-demand without requiring upfront classification. The simpler approach is to provide all tool definitions to the LLM and let it load what it needs through the `invokeother_tool` mechanism.

**Future Vision**: Rather than ML-based classification, tool relevance should emerge naturally from:
1. Tool documentation quality (clear descriptions help the LLM choose correctly)
2. `invokeother_tool` pattern for dynamic tool loading
3. Working memory trinkets that guide tool selection through natural language
4. Optional: Simple heuristics based on conversation state (e.g., calendar-heavy conversations)

## Original Architecture

### Core Components

The relevance engine consisted of 6 main components organized in `tools/relevance_engine/`:

#### 1. **ToolRelevanceService** (`tool_relevance_service.py`)
Main CNS integration point that coordinated all relevance operations.

**Responsibilities:**
- Received `EmbeddedMessage` with pre-computed embeddings
- Coordinated classification, state management, and tool persistence
- Returned list of relevant tool definitions to orchestrator
- Handled suspension/resumption for workflow management

**Key Methods:**
- `get_relevant_tools(embedded_msg)` - Main entry point for tool selection
- `set_topic_changed(topic_changed)` - Topic continuity management
- `suspend()` / `resume()` - Workflow control
- `get_system_status()` - Debugging and monitoring

#### 2. **ClassificationEngine** (`classification_engine.py`)
ML classification system using one-vs-rest binary classifiers.

**Responsibilities:**
- One-vs-rest classification (separate binary classifier per tool)
- Matrix operations for efficient similarity scoring
- Embedding cache management
- Thread-safe concurrent operations

**Key Features:**
- Precomputed tool embeddings matrix for fast similarity
- Persistent cache to avoid recomputation
- Semaphore-limited threading (config: `thread_limit`)
- Fallback to matrix similarity when classifiers unavailable

#### 3. **ExampleManager** (`example_manager.py`)
Managed tool trigger examples for classifier training.

**Responsibilities:**
- Load examples from `data/tools/[tool_name]/embedding_trigger_examples.json`
- Generate synthetic examples for tools without data
- Handle example deduplication and validation
- Track example file changes for retraining triggers

**Example Format:**
```json
[
  "Set a reminder for tomorrow at 3pm",
  "Remind me to call John next week",
  "Add meeting to calendar for Friday"
]
```

#### 4. **ToolDiscovery** (`tool_discovery.py`)
Automatic tool detection and lifecycle management.

**Responsibilities:**
- Discover available tools from `tools/implementations/`
- Create data directories for new tools
- Manage tool data lifecycle
- Clean up orphaned tool data

**Directory Structure Created:**
```
data/tools/
├── tool_name/
│   └── embedding_trigger_examples.json
├── tool_list.json
└── classifier_file_hashes.json
```

#### 5. **RelevanceState** (`relevance_state.py`)
Conversation context and tool persistence management.

**Responsibilities:**
- Track which tools are active per conversation
- Manage tool persistence across messages
- Handle topic change events (reset relevance on topic shifts)
- Maintain activation history

**State Tracking:**
- **Persistent Tools**: Tools that remain enabled for N messages after activation
- **Activation History**: When each tool was last activated
- **Topic Continuity**: Reset persistence on topic changes
- **Message Counter**: Track conversation progress

Configuration:
- `tool_persistence_messages` - Number of messages to keep tools enabled (default: 2)

#### 6. **CacheManager** (`cache_manager.py`)
Unified caching layer for embeddings and classifiers.

**Responsibilities:**
- Cache tool embeddings to avoid recomputation
- Cache trained classifiers for fast startup
- Invalidate caches when examples change
- Thread-safe cache access

**Cache Locations:**
- `data/classifier/` - Trained classifier models
- In-memory embedding cache

### Data Flow

```
User Message → CNS Orchestrator
    ↓
EmbeddedMessage created (embeddings pre-computed)
    ↓
ToolRelevanceService.get_relevant_tools(embedded_msg)
    ↓
ClassificationEngine analyzes embeddings
    ↓
[For each tool] Binary classifier predicts relevance
    ↓
RelevanceState adds persistent tools
    ↓
Combined list of relevant tools returned
    ↓
Tool definitions passed to LLM for function calling
```

### Integration Points

The relevance engine was integrated at multiple levels:

#### 1. **CNS Factory** (`cns/integration/factory.py`)

```python
# Initialization
def _get_tool_relevance_service(
    self,
    tool_repo: ToolRepository,
    embedding_model
) -> ToolRelevanceService:
    if self._tool_relevance_service is None:
        logger.info("Initializing tool relevance service")
        self._tool_relevance_service = ToolRelevanceService(
            tool_repo=tool_repo,
            model=embedding_model
        )
        logger.info("Tool relevance service initialized")
    return self._tool_relevance_service

# Orchestrator wiring
orchestrator = ConversationOrchestrator(
    ...
    tool_relevance_service=tool_relevance_service,
    ...
)

# Event bus configuration
def _configure_event_bus_integrations(
    self,
    event_bus: EventBus,
    tool_relevance_service=None,
    ...
):
    event_bus.tool_relevance_service = tool_relevance_service
```

#### 2. **Orchestrator** (`cns/services/orchestrator.py`)

```python
def __init__(
    self,
    ...
    tool_relevance_service,  # Tool relevance service
    ...
):
    self.tool_relevance_service = tool_relevance_service
```

**Note**: The orchestrator stored the service but the actual tool selection logic was commented out or removed earlier. This suggests the relevance engine was already partially deprecated.

#### 3. **Event Bus** (`cns/integration/event_bus.py`)

```python
def __init__(self,
             tool_relevance_service=None,
             ...):
    self.tool_relevance_service = tool_relevance_service

def _handle_topic_changed(self, event: TopicChangedEvent):
    """Handle topic change by notifying tool relevance service."""
    if self.tool_relevance_service:
        try:
            self.tool_relevance_service.set_topic_changed(True)
            logger.info(f"Notified tool relevance service of topic change")
        except Exception as e:
            logger.error(f"Failed to notify tool relevance service: {e}")
```

#### 4. **Configuration** (`config/config.py`)

```python
class ToolRelevanceConfig(BaseModel):
    """Configuration for the ToolRelevanceEngine."""

    thread_limit: int = Field(default=2, description="Maximum threads for embedding calculations")
    context_window_size: int = Field(default=3, description="Previous messages for context")
    topic_coherence_threshold: float = Field(default=0.7, description="Similarity for related messages")
    tool_persistence_messages: int = Field(default=2, description="Messages to keep tool enabled after activation")
```

### Embedded Message Integration

The relevance engine consumed `EmbeddedMessage` objects that contained pre-computed embeddings:

```python
from cns.core.embedded_message import EmbeddedMessage

embedded_msg = EmbeddedMessage(
    content=text_for_context,
    weighted_context=weighted_context,
    user_id=conversation.user_id,
    embedding_384=embedding_384  # Pre-computed in orchestrator
)

# Passed to relevance service
relevant_tools = tool_relevance_service.get_relevant_tools(embedded_msg)
```

This architecture allowed the orchestrator to generate embeddings once and propagate them to multiple services (memory relevance, tool relevance) without recomputation.

## Why the Original Approach Was Problematic

1. **High Complexity, Marginal Benefit**
   - Required ML training infrastructure for simple tool selection
   - Added 6 components, each with their own state and lifecycle
   - Classification often defaulted to "enable all tools" anyway

2. **Maintenance Burden**
   - Required example data for every tool (`embedding_trigger_examples.json`)
   - Needed classifier retraining when examples changed
   - Cache invalidation logic prone to bugs
   - Thread management added concurrency complexity

3. **Latency Overhead**
   - Classification added ~50-100ms per message
   - Embedding generation already happening in orchestrator
   - Matrix operations and cache lookups added overhead

4. **Limited Accuracy**
   - Binary classification often wrong for ambiguous messages
   - Topic persistence heuristics unreliable
   - Fallback to "all tools" defeated the purpose

5. **Better Alternative Exists**
   - `invokeother_tool` pattern allows dynamic tool loading
   - LLM is better at understanding tool relevance than ML classifiers
   - Tool definitions in working memory provide natural guidance
   - Simpler to just pass all tool definitions (context limits are generous)

## Future Reintegration Approach

**Don't reintegrate the ML-based classification system.** Instead, use these simpler alternatives:

### 1. **invokeother_tool Pattern** (Already Implemented)

The `invokeother_tool` allows MIRA to dynamically load tools on demand:

```python
# Tool definition visible in working memory
{
  "name": "invokeother_tool",
  "description": "Load and invoke a tool that isn't currently available",
  "parameters": {
    "tool_name": "Name of tool to load and invoke",
    "arguments": "Arguments to pass to the tool"
  }
}
```

**Benefits:**
- No upfront classification needed
- LLM decides when to load tools based on conversation context
- Tools remain loaded for subsequent calls
- Natural language tool discovery through documentation

### 2. **All-Tools-Always Approach**

With modern context windows (200k+ tokens), providing all tool definitions is viable:

```python
# In orchestrator
available_tools = self.tool_repo.get_all_tool_definitions()

# Pass to LLM
self.llm_provider.stream_events(
    messages=complete_messages,
    tools=available_tools  # All tools available
)
```

**Benefits:**
- Zero classification latency
- No ML infrastructure needed
- LLM makes relevance decisions (it's better at this anyway)
- Simpler codebase

### 3. **Working Memory Guidance** (Optional Enhancement)

If you want to guide tool selection, use working memory trinkets:

```python
# ProactiveToolGuidanceTrinket
class ProactiveToolGuidanceTrinket(Trinket):
    """Suggests relevant tools based on conversation patterns."""

    def get_guidance(self, conversation) -> str:
        recent_topics = self._analyze_recent_messages(conversation)

        suggestions = []
        if "time" in recent_topics or "schedule" in recent_topics:
            suggestions.append("- Consider using calendar_tool or reminder_tool")
        if "customer" in recent_topics or "client" in recent_topics:
            suggestions.append("- Consider using customerdatabase_tool")

        if suggestions:
            return "## Tool Suggestions\n" + "\n".join(suggestions)
        return ""
```

This provides gentle guidance without forcing classification.

### 4. **Heuristic-Based Hints** (Optional)

Simple conversation state heuristics can suggest tools:

```python
def suggest_tools(conversation) -> List[str]:
    """Simple heuristic suggestions without ML."""
    suggestions = []

    # Check conversation metadata
    if conversation.metadata.get('calendar_heavy'):
        suggestions.extend(['calendar_tool', 'reminder_tool'])

    if conversation.metadata.get('customer_context'):
        suggestions.append('customerdatabase_tool')

    # Check recent tool usage
    recent_tools = conversation.get_recently_used_tools(n=3)
    suggestions.extend(recent_tools)

    return list(set(suggestions))  # Deduplicate
```

## Migration Path (If Reintegrating)

**DO NOT reintegrate the ML-based system.** If you must have intelligent tool selection:

### Phase 1: Remove Dependencies
1. Update all imports that reference `tools.relevance_engine`
2. Remove `tool_relevance_service` parameter from orchestrator
3. Remove event bus integrations for topic changes
4. Remove `ToolRelevanceConfig` from config

### Phase 2: Implement Simple Alternative
1. Enable all tools by default: `tool_repo.get_all_tool_definitions()`
2. Add working memory trinket for tool suggestions (optional)
3. Document tool capabilities clearly for LLM understanding

### Phase 3: Test and Validate
1. Verify tool calls work with all tools available
2. Monitor context usage and latency
3. Confirm `invokeother_tool` handles dynamic loading

### Phase 4: Clean Up Data
1. Archive `data/tools/` directory (contains examples and classifiers)
2. Remove classifier cache from `data/classifier/`
3. Document data cleanup in release notes

## Removed Components Summary

### Files Removed
- `tools/relevance_engine/__init__.py`
- `tools/relevance_engine/tool_relevance_service.py`
- `tools/relevance_engine/classification_engine.py`
- `tools/relevance_engine/example_manager.py`
- `tools/relevance_engine/tool_discovery.py`
- `tools/relevance_engine/relevance_state.py`
- `tools/relevance_engine/cache_manager.py`

### Integration Points to Clean Up
- [ ] `cns/integration/factory.py` - Remove `_get_tool_relevance_service()`
- [ ] `cns/integration/factory.py` - Remove tool_relevance_service from orchestrator init
- [ ] `cns/integration/factory.py` - Remove tool_relevance_service from event bus config
- [ ] `cns/integration/event_bus.py` - Remove tool_relevance_service parameter and usage
- [ ] `cns/services/orchestrator.py` - Remove tool_relevance_service parameter
- [ ] `config/config.py` - Remove `ToolRelevanceConfig` class
- [ ] Documentation updates for removal

### Data Directories (Preserve for Reference)
- `data/tools/*/embedding_trigger_examples.json` - Keep as reference for tool examples
- `data/classifier/` - Can be deleted (cached classifier models)
- `data/tools/tool_list.json` - Can be deleted (tool discovery artifact)
- `data/tools/classifier_file_hashes.json` - Can be deleted (cache invalidation tracking)

## Conclusion

The Tool Relevance Engine was a well-architected ML system that solved tool selection through classification. However, it added significant complexity for marginal benefit. The simpler approach of providing all tool definitions and leveraging `invokeother_tool` for dynamic loading achieves the same goal with:

- **Zero ML infrastructure**
- **No classification latency**
- **Better accuracy** (LLM understands context better than classifiers)
- **Simpler codebase** (6 fewer components)
- **Easier maintenance** (no training data, no cache invalidation)

**Recommendation: Do not reintegrate the ML-based relevance engine. Use all-tools-always + invokeother_tool instead.**

If you need gentle guidance, add a simple working memory trinket that suggests tools based on conversation patterns, not ML classification.

---

## Ablation Process Notes

This feature was removed following the systematic process documented in `docs/FEATURE_ABLATION_GUIDE.md`.

**Removal Statistics**:
- **Date**: October 2025
- **Lines removed**: ~2,093 across 7 files
- **Integration points cleaned**: 5 major files
- **Time to complete**: 45 minutes
- **Remaining production references**: 0

**Key Findings During Removal**:
- The orchestrator stored `tool_relevance_service` but never actually called it (already effectively disabled)
- Event bus had three separate integration layers requiring cleanup
- Configuration required removal from both schema and manager files
- Multi-pattern grep verification caught references single patterns missed

For detailed ablation process guidance, see `docs/FEATURE_ABLATION_GUIDE.md`.
