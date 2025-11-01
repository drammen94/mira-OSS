# Segment System Overview

## What is the Segment System?

The Segment System is MIRA's intelligent conversation chunking and compression mechanism that automatically divides continuous conversations into time-bounded segments. It enables progressive disclosure of conversation history through automatic summarization, semantic search capabilities, and efficient memory management. The system uses sentinel messages as segment boundaries, following an event-driven architecture that integrates seamlessly with CNS and other MIRA components.

## Architecture Overview

### Directory Structure
```
cns/
├── services/
│   ├── segment_helpers.py           # Sentinel lifecycle utilities
│   ├── segment_timeout_service.py   # Scheduled timeout detection
│   ├── segment_collapse_handler.py  # Collapse pipeline orchestration
│   └── manifest_query_service.py    # ASCII tree manifest generation
├── core/
│   ├── segment_cache_loader.py      # Session loading with boundaries
│   └── events.py                    # Segment-specific events
└── infrastructure/
    └── continuum_repository.py      # Segment persistence operations
```

## Core Components

### 1. **Segment Helpers** (`cns/services/segment_helpers.py`)

Utility functions for managing segment sentinel lifecycle:

**Key Functions**:
- `create_segment_boundary_sentinel()`: Creates active segment sentinel when second message arrives
- `add_tools_to_segment()`: Tracks tool usage within segment (deduplicated)
- `collapse_segment_sentinel()`: Transitions sentinel to collapsed state with summary
- `mark_segment_processed()`: Sets flags for downstream processing completion
- `get_segment_id()`: Extracts unique segment identifier
- `is_segment_boundary()`: Checks if message is a sentinel
- `is_active_segment()`: Verifies segment is still active
- `create_collapse_marker()`: Creates "older content available" notification
- `create_session_boundary_marker()`: Creates session break notification

**Sentinel Structure**:
```python
{
    'is_segment_boundary': True,
    'status': 'active',  # or 'collapsed', 'archived'
    'segment_id': UUID,
    'segment_start_time': ISO timestamp,
    'segment_end_time': ISO timestamp,
    'tools_used': [],
    'memories_extracted': False,
    'domain_blocks_updated': False
}
```

### 2. **Segment Timeout Service** (`cns/services/segment_timeout_service.py`)

APScheduler job that detects inactive segments:

**Key Features**:
- Runs every 5 minutes as scheduled job
- Queries all active segments across users
- Calculates inactive duration from last message
- Configurable timeout threshold (default: 1 hour)
- Publishes `SegmentTimeoutEvent` for inactive segments
- Supports time-of-day aware thresholds (commented out)

**Timeout Detection**:
1. Query active segment sentinels
2. Find last message timestamp in each segment
3. Calculate inactive duration
4. Compare against threshold
5. Publish timeout events

### 3. **Segment Collapse Handler** (`cns/services/segment_collapse_handler.py`)

Orchestrates segment collapse pipeline:

**Processing Pipeline**:
1. Subscribe to `SegmentTimeoutEvent`
2. Find segment sentinel by ID
3. Load messages between sentinels
4. Generate summary via LLM
5. Create 384-dim embedding
6. Update sentinel to collapsed state
7. Save to database
8. Invalidate cache
9. Trigger downstream processing
10. Publish `SegmentCollapsedEvent` and `ManifestUpdatedEvent`

**Key Features**:
- Defensive boundary checking
- Fallback summary on LLM failure
- Tool extraction from message content
- Batch API integration for memory extraction
- Event-driven downstream coordination

### 4. **Manifest Query Service** (`cns/services/manifest_query_service.py`)

Generates conversation manifests for system prompt:

**Manifest Format**:
```
CONVERSATION MANIFEST
├─ Today
│  ├─ [2:15pm - Active] Manifest architecture discussion
│  └─ [9:00am - 10:15am] Morning standup notes
├─ Yesterday
│  └─ [8:00am - 8:27am] Arduino servo debugging
└─ Jan 18
   └─ [3:00pm - 4:12pm] Nacho recipe research
```

**Key Features**:
- ASCII tree format for readability
- Groups segments by relative date
- Shows time ranges and status
- Uses telegraphic display titles
- Valkey caching with TTL
- Event-driven cache invalidation
- Configurable depth (default: 30 segments)

### 5. **Segment Cache Loader** (`cns/core/segment_cache_loader.py`)

Loads context for new sessions:

**Loading Sequence**:
1. Collapse marker (indicates searchable history)
2. Collapsed segment summaries (configurable count)
3. Continuity messages (last N turns)
4. Session boundary marker (time gap)
5. Active segment messages

**Key Methods**:
- `load_session_cache()`: Orchestrates full cache loading
- `_load_segment_summaries()`: Gets collapsed sentinels
- `_load_active_segment_messages()`: Gets current conversation
- `_load_continuity_messages()`: Gets conversation tail

### 6. **Summary Generator Integration**

Segments use specialized prompts for summary generation:

**Summary Components**:
1. **Display Title**: 8 words or fewer, telegraphic noun phrase
2. **Synopsis**: 2-3 sentences with entities, outcomes, tools used

**Example**:
- Title: "Segment system architecture deep dive"
- Synopsis: "Explored MIRA's segment system implementation including timeout detection, collapse handlers, and manifest generation..."

## Data Models

### Messages Table Extensions

**Segment-Specific Columns**:
```sql
-- Embedding for segment search (sentinels only)
segment_embedding vector(384)

-- Indexes for segment operations
CREATE INDEX idx_messages_segment_embedding
    USING ivfflat(segment_embedding vector_cosine_ops)
    WHERE metadata->>'is_segment_boundary' = 'true';

CREATE INDEX idx_messages_active_segments
    ON messages(continuum_id, created_at)
    WHERE metadata->>'is_segment_boundary' = 'true'
    AND metadata->>'status' = 'active';
```

### Continuum Segments Table (Denormalized)

**Schema**:
```sql
CREATE TABLE continuum_segments (
    -- Identity
    id UUID PRIMARY KEY,
    continuum_id UUID REFERENCES continuums(id),
    user_id UUID REFERENCES users(id),

    -- Time boundaries
    start_time TIMESTAMP WITH TIME ZONE,
    end_time TIMESTAMP WITH TIME ZONE,
    inactive_duration_minutes INTEGER,

    -- Content
    summary TEXT,
    summary_embedding vector(384),

    -- Tracking
    message_count INTEGER,
    status TEXT CHECK (status IN ('active', 'collapsed', 'archived')),

    -- Processing flags
    memories_extracted BOOLEAN DEFAULT FALSE,
    domain_blocks_updated BOOLEAN DEFAULT FALSE,

    -- Metadata
    tools_used JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}'
);
```

## Event Flow

### Segment Creation Flow
```
First message arrives → No segment needed
    ↓
Second message arrives → Continuum Repository
    ↓
_ensure_active_segment() called
    ↓
Check for existing active segment
    ↓
If none exists: create_segment_boundary_sentinel()
    ↓
Save sentinel to messages table
    ↓
Segment tracking begins
```

### Segment Timeout and Collapse Flow
```
APScheduler (every 5 min) → SegmentTimeoutService.check_timeouts()
    ↓
Query active segments → Calculate inactive duration
    ↓
If timeout exceeded → Publish SegmentTimeoutEvent
    ↓
SegmentCollapseHandler receives event
    ↓
Load segment messages → Generate summary → Create embedding
    ↓
Update sentinel to collapsed → Save to database
    ↓
Publish SegmentCollapsedEvent → Trigger downstream
    ↓
Publish ManifestUpdatedEvent → Invalidate cache
```

### Session Loading Flow
```
New session starts → SegmentCacheLoader.load_session_cache()
    ↓
Load collapsed summaries → Load continuity messages
    ↓
Create boundary markers → Load active messages
    ↓
Assemble in order:
[collapse_marker] + [summaries] + [continuity] + [boundary] + [active]
```

## Key Design Principles

### 1. **Sentinel-Based Architecture**
- Segments represented as special messages
- No separate segment entity needed
- Metadata drives segment behavior
- Efficient querying via indexes

### 2. **Event-Driven Coordination**
- Loose coupling between components
- Clear event contracts
- Async processing support
- No direct dependencies

### 3. **Progressive Disclosure**
- Recent context readily available
- Older content compressed to summaries
- Semantic search via embeddings
- Manifest provides navigation

### 4. **Defensive Programming**
- Boundary checking prevents overrun
- Fallback summaries on failure
- Idempotent processing flags
- Graceful degradation

### 5. **User Isolation**
- Segments scoped by user_id
- RLS enforcement at DB level
- No cross-user data access
- Complete data separation

## Shutdown and Restart Behavior

### Crash-Resilient Design

The segment system is designed to be fully resilient to application shutdowns, restarts, and crashes. Segment state persists in the database rather than in-memory, enabling seamless recovery.

**What Happens During Shutdown:**
- Active segments remain in database with `status='active'`
- Segment sentinels (including start time, tools used, segment ID) persist
- All previously saved messages remain intact
- No explicit shutdown handler closes active segments

**What Happens On Restart:**
- When user sends next message, `_ensure_active_segment()` finds existing active segment
- Conversation automatically resumes in the same segment
- Timeout clock resets based on new message timestamp
- No user-visible disruption or segment fragmentation

**Autonomous Timeout Processing:**
- Timeout service calculates inactivity from database message timestamps
- Works correctly regardless of when MIRA was online
- If user doesn't return after restart, timeout service eventually collapses segment
- Summaries generated for aged segments even if MIRA was offline when timeout threshold passed

**Example Scenario:**
```
2:00 PM - User sends message (segment active)
2:01 PM - MIRA shuts down for deployment
2:05 PM - MIRA restarts
2:30 PM - User sends message → Segment resumes seamlessly
         → Timeout clock now set to 5:30 PM (3 hours from 2:30 PM)
3:00 PM - Another message → Timeout clock extends to 6:00 PM
[User stops messaging]
9:05 PM - Timeout service collapses segment with full summary
```

**What Gets Lost:**
- In-flight assistant responses being generated during shutdown (not yet saved)
- In-memory cache state (rebuilt on next access)
- Event bus pending events (not persisted)

**What Survives:**
- All segment metadata (`status`, `segment_id`, `start_time`, `tools_used`)
- All saved messages within segment
- Segment collapse happens eventually via timeout service
- Complete conversation history preserved

This database-first architecture means segments behave more like durable conversation threads than ephemeral sessions—they persist across any number of restarts and only collapse based on actual conversation inactivity.

## Integration Points

### With CNS Orchestrator
- Repository creates segments automatically
- Events published through CNS event bus
- Cache invalidation triggers reload

### With Working Memory
- `ManifestTrinket` displays segment tree
- Manifest included in system prompt
- Updates on segment changes

### With LT Memory System
- Collapsed segments queued for extraction
- Batch API processes asynchronously
- Memories extracted from full messages

### With Tool System
- Tools tracked per segment
- Usage displayed in manifest
- Helps understand conversation flow

### With Embeddings Provider
- 384-dim AllMiniLM embeddings
- Generated during collapse
- Enables semantic search

### With Valkey Cache
- Manifest cached with TTL
- Invalidated on updates
- Continuum cache cleared on collapse

## Configuration

Key settings in `config.system`:
- `segment_timeout`: Inactivity threshold (default: 60 min)
- `manifest_depth`: Segments in manifest (default: 30)
- `manifest_summary_truncate_length`: Title length (default: 60)
- `session_summary_count`: Summaries on reload (default: 3)
- `manifest_cache_ttl`: Cache duration

## Benefits

1. **Scalability**: Conversations can grow indefinitely without performance impact
2. **Searchability**: Semantic search across conversation history via embeddings
3. **Context Management**: Progressive disclosure keeps context windows manageable
4. **User Experience**: Natural conversation breaks preserved
5. **Knowledge Extraction**: Automatic memory extraction from segments
6. **Debugging**: Clear segment boundaries aid troubleshooting
7. **Flexibility**: Time-based segmentation adapts to usage patterns

## State Diagram

```
┌─────────────┐
│   CREATED   │ (First message only)
└──────┬──────┘
       │ Second message
┌──────▼──────┐
│   ACTIVE    │ ◄─── New messages added
└──────┬──────┘      Tools tracked
       │ Timeout
┌──────▼──────┐
│  COLLAPSING │ (Transient state)
└──────┬──────┘ Summary generation
       │        Embedding creation
┌──────▼──────┐
│  COLLAPSED  │ ◄─── Memory extraction
└──────┬──────┘      Domain updates
       │ Future
┌──────▼──────┐
│  ARCHIVED   │ (Cold storage - planned)
└─────────────┘
```

## Example Timeline

```
8:00 AM - User starts conversation
        - First message (no segment needed)
8:01 AM - Second message arrives
        - Active segment created
8:30 AM - Multiple messages exchanged
        - Tools tracked in segment
11:35 AM - User goes to lunch
2:35 PM - Timeout detected (3 hours)
        - SegmentTimeoutEvent published
2:36 PM - Collapse handler processes
        - Summary: "Morning debugging session"
        - Embedding generated
        - Memories queued for extraction
3:00 PM - User returns
        - New active segment created
        - Manifest shows collapsed morning session
```

## Future Enhancements

1. **Archival System**: Move old segments to cold storage
2. **Time-Aware Thresholds**: Different timeouts for different hours
3. **Domain Knowledge Integration**: Extract knowledge blocks from segments
4. **Cross-Segment Analysis**: Identify patterns across segments
5. **Segment Merging**: Combine related short segments
6. **Custom Segmentation**: User-defined segment triggers