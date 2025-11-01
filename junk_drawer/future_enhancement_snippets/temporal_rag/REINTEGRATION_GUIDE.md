# Temporal RAG Service - Reintegration Guide

## What This Is

The Temporal RAG (Retrieval Augmented Generation) service provides time-aware context retrieval from historical conversation days. It allows users to explicitly "link" past conversation days to their current session, making that historical context semantically searchable and automatically surfaced when relevant.

## Why It Was Removed

This feature was implemented but **never integrated** into the CNS orchestration layer:
- No tools invoke it
- The orchestrator doesn't call it
- No API endpoints expose it
- It became orphaned code that broke during config cleanup (commit `ac5b3f7`)

## How It Works

1. **Day Linking**: Users can link historical conversation days to their current continuum
2. **Index Creation**: LlamaIndex creates semantic vector indices of linked days using BGE-M3 embeddings
3. **Contextual Retrieval**: During conversations, the service queries linked days for relevant context
4. **Smart Surfacing**: Top-scoring historical excerpts are injected into the system prompt

## Key Components

### `temporal_context.py`
- `TemporalContextService`: Main service class
- `MIRAEmbeddingAdapter`: Bridges MIRA's embeddings provider to LlamaIndex
- `get_temporal_context_service()`: Singleton factory pattern
- Key methods:
  - `get_temporal_context()`: Retrieve context for current continuum
  - `link_day()`: Link a historical date to current session
  - `unlink_day()`: Remove a linked day
  - `clear_linked_days()`: Clean up during session boundaries

## Configuration Required

Add these fields back to `config.py` `LTMemoryConfig`:

```python
# Temporal RAG Settings
temporal_rag_enabled: bool = Field(
    default=True,
    description="Enable temporal RAG functionality for linking historical conversation days"
)
temporal_chunk_size: int = Field(
    default=2048,
    description="Size of document chunks for LlamaIndex processing"
)
temporal_chunk_overlap: int = Field(
    default=200,
    description="Overlap between document chunks to preserve context"
)
temporal_max_days_linked: int = Field(
    default=5,
    description="Maximum number of historical days that can be linked simultaneously"
)
```

## Reintegration Steps

### 1. Restore Configuration
Add the config fields above to `config/config.py`

### 2. Move Service Back
```bash
git mv junk_drawer/future_enhancement_snippets/temporal_rag/temporal_context.py cns/services/temporal_context.py
```

### 3. Restore main.py
In `main.py` lifespan startup (after line 62, before lt_memory initialization):
```python
# Initialize temporal context service (sets up LlamaIndex)
from cns.services.temporal_context import get_temporal_context_service
temporal_service = get_temporal_context_service(embeddings_provider)
logger.info(f"Temporal context service initialized (enabled: {temporal_service.enabled})")
```

In shutdown (after line 165, in cleanup section):
```python
# Clean up temporal context service indices
if temporal_service and temporal_service.enabled:
    temporal_service.cleanup()
    logger.info("Temporal context service cleaned up")
```

### 4. Restore cns/api/actions.py
Add back to `DomainType` enum (after `CONTACTS = "contacts"`):
```python
CONTINUUM = "continuum"
```

Add entire `ContinuumDomainHandler` class before `DomainKnowledgeDomainHandler` class (reference the commit for exact implementation).

In `ActionsEndpoint.__init__()` domain_handlers dict, add:
```python
DomainType.CONTINUUM: ContinuumDomainHandler,
```

### 5. Restore cns/api/data.py
Add back to `DataType` enum (after `USER = "user"`):
```python
LINKED_DAYS = "linked_days"
```

In `process_request()` routing, add:
```python
elif data_type == DataType.LINKED_DAYS:
    return self._get_linked_days(user_id, **request_params)
```

Add entire `_get_linked_days()` method after `_get_user()` method (reference the commit for exact implementation).

### 6. Restore cns/integration/factory.py
In `__init__()`, add back:
```python
self._temporal_context_service = None
```

In `create_orchestrator()`, before memory_relevance_service creation:
```python
# Create temporal context service if enabled
temporal_context_service = self._get_temporal_context_service()
```

In ContinuumOrchestrator init call, add parameter:
```python
temporal_context_service=temporal_context_service,
```

Add entire `_get_temporal_context_service()` method before `_get_analysis_generator()` method (reference the commit for exact implementation).

### 7. Restore cns/services/orchestrator.py
In `__init__()` signature, add parameter after `memory_relevance_service`:
```python
temporal_context_service=None,  # Optional temporal context service
```

In assignments, add:
```python
self.temporal_context_service = temporal_context_service
```

In the response building section (around where dynamic_parts is built), add before the "Add non-cached content block" comment:
```python
# Add temporal context if available
linked_days = continuum._state.metadata.get('linked_days', [])
if self.temporal_context_service and linked_days:
    temporal_content = self.temporal_context_service.get_temporal_context(
        continuum,
        query=weighted_context
    )
    if temporal_content:
        dynamic_parts.append(temporal_content)
```

### 8. Create Tool Interface (Optional but Recommended)
Build a tool (e.g., `continuumhistory_tool.py`) that exposes:
- `link_day(date: str)`: Link a specific date
- `unlink_day(archive_id: str)`: Unlink by archive ID
- `list_linked_days()`: Show currently linked days
- `search_history(query: str, date: str)`: Direct historical search

## Dependencies

Requires LlamaIndex installation:
```bash
pip install llama-index-core
```

## Original Design Intent

This was meant to provide a "mental time travel" capability - allowing users to explicitly connect current conversations to past context without relying solely on long-term memory extraction. It's particularly useful for:

- Multi-day projects where context from specific past days is relevant
- Revisiting decisions or discussions from specific dates
- Building on past brainstorming sessions

## Performance Considerations

- Index creation is expensive (BGE-M3 embeddings + LlamaIndex processing)
- Indices are cached in-memory (`_global_temporal_indices`)
- Should limit linked days (default: 5) to prevent memory bloat
- Consider adding persistence for indices to avoid rebuilding on restart

## Files Modified During Removal (October 24, 2025)

For quick restoration, these files had temporal_context references removed:

### `main.py` (lines 65-70, 174-177)
- Removed: `from cns.services.temporal_context import get_temporal_context_service`
- Removed: `temporal_service = get_temporal_context_service(embeddings_provider)` initialization
- Removed: `temporal_service.cleanup()` in shutdown handler

### `cns/api/actions.py` (class removal + enum)
- Removed: Entire `ContinuumDomainHandler` class (was lines 762-840)
  - Implemented `link_day` and `unlink_day` actions
  - Calls to `temporal_service.link_day()` and `temporal_service.unlink_day()`
- Removed: `DomainType.CONTINUUM = "continuum"` enum value
- Removed: Handler registration in domain_handlers dict

### `cns/api/data.py` (lines 30, 54-55, 196-238)
- Removed: `DataType.LINKED_DAYS = "linked_days"` enum value
- Removed: Router condition `elif data_type == DataType.LINKED_DAYS: return self._get_linked_days(...)`
- Removed: Entire `_get_linked_days()` method (was lines 196-238)
  - Retrieved currently linked continuum days
  - Called `temporal_service._get_linked_days(continuum)`

### `cns/integration/factory.py` (initialization removal)
- Removed: `self._temporal_context_service = None` from `__init__`
- Removed: `temporal_context_service = self._get_temporal_context_service()` line from `create_orchestrator()`
- Removed: `temporal_context_service=temporal_context_service` param in ContinuumOrchestrator init call
- Removed: Entire `_get_temporal_context_service()` method (was lines 299-310)

### `cns/services/orchestrator.py` (parameter + usage removal)
- Removed: `temporal_context_service=None` parameter from `__init__`
- Removed: `self.temporal_context_service = temporal_context_service` assignment
- Removed: Temporal context retrieval block (lines 196-204)
  - Checked for linked_days in metadata
  - Called `self.temporal_context_service.get_temporal_context()`
  - Appended temporal content to dynamic_parts for system prompt injection

## Git History Reference

- Added: `d3877fa` (Aug 13, 2025)
- Config removed: `ac5b3f7` (cleanup commit)
- Fully removed: October 24, 2025 - referenced files above for exact removal locations
