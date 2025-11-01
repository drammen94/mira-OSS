# LT_Memory (Long-Term Memory) System Overview

## What is LT_Memory?

LT_Memory is MIRA's autonomous long-term memory system that extracts, stores, and surfaces memories from conversations without human intervention. It implements a sophisticated "Earning Your Keep" model where memories must prove their value through access patterns and relationships to persist in the system.

## Architecture Overview

### Core Components
```
lt_memory/
├── models.py                 # Pydantic data models
├── db_access.py              # Database access layer (single source of truth)
├── scoring_formula.sql       # Importance scoring SQL formula
├── extraction.py             # LLM-based memory extraction
├── linking.py                # Memory relationship classification
├── refinement.py             # Verbose memory consolidation
├── batching.py               # Anthropic Batch API orchestration
├── proactive.py              # Context-aware memory surfacing
├── vector_ops.py             # Embedding and similarity operations
├── entity_extraction.py      # Named entity recognition (spaCy)
├── entity_gc.py              # Entity garbage collection
├── scheduled_tasks.py        # Periodic maintenance jobs
└── factory.py                # Service initialization and DI
```

## Data Model

### Core Data Structures (`models.py`)

**ProcessingChunk**: Represents a coherent segment of conversation for processing
- `messages`: List of conversation messages
- `topic_boundary`: Whether chunk ends at topic change
- `memory_context_snapshot`: UUIDs of memories surfaced during conversation

**ExtractedMemory**: A memory extracted from conversation
- `text`: The memory content
- `importance_score`: Initial importance (default: 0.5)
- `expires_at`: Optional expiration timestamp
- `confidence`: LLM confidence in extraction (0.0-1.0)
- `metadata`: Relationship info, consolidation targets, refinement data

**MemoryLink**: Relationship between memories
- `target_id`: UUID of related memory
- `link_type`: Relationship type (related, supports, conflicts, supersedes)
- `confidence`: Link confidence score
- `reasoning`: LLM explanation for the relationship

## Memory Lifecycle

### 1. **Extraction Pipeline** (`memory_extraction_service.py`)

**Event-Driven Processing** (pointer summary coalescence):
1. Pointer summary coordinator receives the coalesced window event
2. Hydrates the source messages for that window from the conversation store
3. Batches those messages into extraction chunks
4. Sends chunks through the extraction pipeline

**Memory Context Awareness**:
- Provides existing memory context to LLM during extraction
- Maps UUIDs to their first 8 hex characters (no dashes) for LLM comprehension
- Prevents re-extraction of known facts
- Enables relationship detection with existing memories

### 2. **LLM Extraction** (`memory_extractor.py`)

**Extraction Process**:
1. Formats conversation chunk with speaker labels and timestamps
2. Includes surfaced memory context with shortened UUID identifiers
3. Uses Google Gemini-2.5-flash-lite with structured JSON output
4. Extracts:
   - New factual memories about the user
   - Relationships to existing memories
   - Temporal metadata (expires_at for time-bound facts)
   - Consolidation candidates (memories that update/replace others)

**Quality Controls**:
- Temperature: 0.3 (focused extraction)
- Frequency penalty: 0.5 (reduce duplicates)
- JSON schema validation
- Retry logic with exponential backoff

### 3. **Memory Processing** (`memory_processor.py`)

**Consolidation Logic**:
- When LLM identifies memories that should be consolidated:
  1. Selects memory with highest importance score as target
  2. Updates target with consolidated text
  3. Transfers all relationships from consolidated memories
  4. Deletes redundant memories
- Preserves importance scores and temporal metadata

**Link Processing**:
- Creates bidirectional links in database
- Each link stored in both `outbound_links` (source) and `inbound_links` (target)
- Enables efficient graph traversal in both directions

### 4. **Vector Storage** (`vector_store.py`)

**Embedding Generation**:
- Uses hybrid embeddings provider (all-MiniLM-L6-v2)
- Generates 384-dimensional embeddings for all memories
- Supports batch operations for efficiency

**Storage Operations**:
- Bulk insert with automatic user isolation
- Embedding updates when memory text changes
- Link management with JSONB arrays
- Dead link cleanup during traversal

## Memory Scoring & Decay

### "Earning Your Keep" Model (`lt_memory/scoring_formula.sql`)

The scoring formula is defined in a standalone SQL file for clarity and maintainability. All scoring operations use this single source of truth.

**Formula Structure**:
```
importance = sigmoid(
    (value_score + hub_score)
    * recency_boost
    * temporal_multiplier
    - 2.0
)
```

**Key Innovation - Activity Days**:
Decay calculations use **activity days** (cumulative user engagement days) instead of calendar days to prevent vacation-induced memory degradation. Temporal events (happens_at, expires_at) still use calendar days since real-world deadlines don't pause.

**Components**:

1. **Value Score**:
   - Momentum decay: `access_count * 0.95^(activity_days_since_last_access)`
   - Access rate: `effective_access_count / MAX(7, activity_days_since_creation)`
   - Baseline rate: 0.02 (1 access per 50 activity days)
   - Logarithmic scaling: `LN(1 + access_rate / baseline) * 0.8`

2. **Hub Score** (diminishing returns):
   - 0-10 links: 0.04 points per link (linear)
   - 10+ links: `0.4 + (links - 10) * 0.02 / (1 + (links - 10) * 0.05)` (diminishing)

3. **Recency Boost** (activity-based):
   - Formula: `1.0 / (1.0 + activity_days_since_last_access * 0.03)`
   - Recent accesses maintain high scores

4. **Temporal Multiplier** (calendar-based):
   - Future events (happens_at):
     - Within 1 day: **2.0x** boost
     - 1-7 days: **1.5x** boost
     - 7-14 days: **1.2x** boost
     - Beyond 14 days: **1.0x** (neutral)
   - Past events: Linear decay from **0.8x → 0.1x** over 14 days
   - Expired (expires_at < NOW): **0.0** (hard zero)

5. **Sigmoid Transform**:
   - Center point: 2.0 (maps average memories to ~0.5 importance)
   - Range: 0.0 to 1.0
   - Rounded to 3 decimal places

### Decay Operations (`db_access.py`)

**Three scoring methods share the same formula:**

1. **update_access_stats()**: Records memory access, updates activity day snapshot, recalculates score
2. **bulk_recalculate_scores()**: Periodic maintenance for stale memories (7+ days since access)
3. **recalculate_temporal_scores()**: Updates temporal memories with upcoming/recent events

**Archive Process**:
- Memories with importance ≤ 0.001 are archived
- Expired memories (expires_at < NOW) score 0.0 and are archived
- Archived memories can be recovered if accessed again

## Memory Surfacing

### Proactive Memory Search (`proactive.py`)

**Search Process**:
1. Receives pre-computed embedding from CNS orchestrator
2. Vector similarity search against memory embeddings
3. Filters by importance threshold (default: 0.3)
4. Expands results with linked memories (up to 2 levels)
5. Returns fresh results per message (no cross-message dedup)

**Link Expansion**:
- Primary memories found through vector search
- Traverses `outbound_links` to find related memories
- Marks linked memories with relationship metadata
- Prevents cycles with visited set

### Memory Relevance Service (`memory_relevance_service.py`)

**CNS Integration**:
- Parallel architecture to ToolRelevanceService
- Receives EmbeddedMessage with pre-computed embeddings
- No additional embedding generation needed
- Returns relevant memories to orchestrator

## Memory Refinement

### Verbose Memory Distillation (`memory_refiner.py`)

**Candidate Selection**:
- Text length > 60 characters
- Access count >= 3 (well-established)
- No updates for 30+ days (stable)
- At least 7 days old

**Refinement Process**:
1. Identifies verbose but valuable memories
2. Uses LLM to extract core facts
3. Creates refined memory with consolidation metadata
4. Original memory replaced through standard consolidation

**Benefits**:
- Reduces token usage over time
- Preserves essential information
- Maintains importance scores
- Improves retrieval precision

## Graph Traversal

### Link Following (`memory_link_traverser.py`)

**Traversal Algorithm**:
```python
def traverse(memory_id, max_depth=3, visited=None):
    if depth <= 0 or memory_id in visited:
        return []
    
    links = get_outbound_links(memory_id)
    batch_fetch_linked_memories(links)
    
    for linked_memory in memories:
        results.extend(traverse(linked_memory.id, depth-1, visited))
```

**Features**:
- Batch fetching for performance
- Automatic dead link cleanup
- Configurable depth limits
- Cycle detection

## Database Schema

### Core Tables

**memories**:
- `id`: UUID primary key
- `user_id`: User isolation
- `text`: Memory content
- `embedding`: 384-dim vector (JSONB)
- `importance_score`: 0.0-1.0 importance
- `access_count`: Total accesses
- `last_accessed`: Momentum decay calculation
- `outbound_links`: JSONB array of links
- `inbound_links`: JSONB array of backlinks
- `expires_at`: Optional expiration
- `is_refined`: Refinement status

**archived_memories**:
- Same structure as memories table
- Stores decayed memories (importance < 0.001)
- Enables recovery if referenced again

## Key Algorithms

### Momentum Decay (Activity Days)
```sql
effective_access = access_count * POWER(0.95, activity_days_since_last_access)
```
Note: Uses activity days (user engagement days), not calendar days, to prevent vacation-induced decay.

### Hub Score Calculation
```sql
CASE 
    WHEN inbound_links = 0 THEN 0.0
    WHEN inbound_links <= 10 THEN inbound_links * 0.04
    ELSE 0.4 + (inbound_links - 10) * 0.02 / (1 + (inbound_links - 10) * 0.05)
END
```

### Similarity Threshold
- Cosine similarity > 0.4 for surfacing
- Optional reranking with Cohere model
- Importance score filtering after retrieval

## Configuration

Key settings in `config.py`:
- `proactive_similarity_threshold`: Min similarity (default: 0.4)
- `proactive_min_importance`: Min importance to surface (default: 0.3)
- `max_memories`: Max memories per query (default: 10)
- `extraction_lookback_days`: History to process (default: 7)
- `max_extraction_tokens`: LLM token limit (default: 2000)

## Benefits

1. **Autonomous Operation**: No human curation needed
2. **Natural Forgetting**: Unimportant memories fade automatically
3. **Relationship Awareness**: Graph structure enhances retrieval
4. **Temporal Intelligence**: Handles time-sensitive information
5. **Self-Improvement**: Refines verbose memories over time
6. **Scalable Architecture**: Handles large memory stores efficiently
7. **Privacy by Design**: Complete user isolation at database level

## Integration Points

- **CNS Orchestrator**: Receives embedded messages for search
- **Working Memory**: Surfaces memories through ProactiveMemoryTrinket
- **Event Bus**: Publishes memory-related events
- **Scheduler Service**: Triggers periodic extraction
- **Embeddings Provider**: Shared embedding generation
