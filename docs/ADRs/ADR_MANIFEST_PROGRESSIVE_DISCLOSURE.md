# Architecture Decision Record: Manifest-Based Progressive Disclosure

**Status**: Approved for Implementation
**Date**: 2025-10-21
**Decision Makers**: Taylor (Three Pixel Drift)
**Supersedes**: Progressive Hot Cache with topic-based summarization and coalescence
**Integrates With**: ADR_BUCKET_IMPROVED.md (bucket-based topic organization)

---

## Context

### The Problem with Invisible Topic-Based Caching

The current Progressive Hot Cache system (`cns/core/progressive_hot_cache_manager.py`) uses event-driven IF-THEN logic for topic-based summarization:
- IF >3 topics exist → summarize oldest topic
- IF >10 summaries exist → coalesce oldest summaries via `PointerSummariesCollapsingEvent`

While this works functionally, it has fundamental UX and architectural limitations:

**User Visibility Problem**: The cache is completely invisible to users. They have no way to:
- See their conversation history
- Navigate to previous discussions
- Understand what context MIRA has access to
- Search across past conversations in a structured way

The cache exists purely for MIRA's benefit. Users are blind to what context is available.

**Mental Model Mismatch**: Topic-based segmentation doesn't match how humans think about conversations.

Consider these real conversation patterns:
- "Yesterday 8:00am - 8:27am: Spoke about paper airplanes"
- "Two days ago 9:12pm: The fall of man"
- "Three days ago 3:22am - 5:33am: Waifus"

Humans remember conversations by *when* they happened, not by abstract topic boundaries:
- "That chat we had yesterday morning"
- "The discussion last Tuesday night"
- "When we talked about X last week"

Topic changes are invisible, arbitrary, and don't correspond to how people naturally segment their interactions. You can't tell a user "the topic changed" or "we coalesced summaries" - these are implementation details, not meaningful conversation markers.

**Unpredictable Processing**: Memory extraction and processing happen based on topic changes:
- Can trigger mid-conversation (disrupts flow if observable)
- No natural pause points aligned with user behavior
- User has no awareness of when processing occurs
- Processing boundaries feel arbitrary

**No Temporal Organization**: Everything is organized by topics, not time:
- Can't browse history chronologically
- No sense of conversation timeline
- Impossible to answer "what did we discuss yesterday?"
- Difficult to locate specific sessions without topic knowledge

---

## Decision

Implement a **manifest-based progressive disclosure system** where:

1. **Time-Based Session Segmentation**: Conversations are automatically segmented into sessions based on inactivity timeouts rather than topic changes
2. **Visible Manifest**: Users can see and navigate their conversation history through a temporal manifest
3. **Manifest as Infrastructure**: Single data structure serves multiple consumers (visual UI, MIRA's context, search index)
4. **Natural Processing Boundaries**: Memory extraction, domain knowledge updates, and other transformations happen during segment collapse at natural pause points
5. **Dual-Index Navigation**: Temporal manifest + bucket assignment creates navigable conversation map

### Why This Approach is Better

**Time-Based Aligns with Human Memory**: People naturally segment conversations temporally - "this morning's discussion", "last night's chat", "yesterday afternoon". Time boundaries are universally understood and personally meaningful. Topic boundaries require abstract reasoning about "when did we change topics?" which is often unclear.

**Natural Pause Points**: Inactivity timeouts correspond to real breaks in conversation - user stepped away, went to sleep, switched contexts. These are natural moments for processing (memory extraction, summarization) that don't interrupt active conversation flow.

**Visible Infrastructure**: The manifest becomes a conversation map users can actually see and interact with. Instead of a hidden cache that "just works," users have a tangible timeline showing where they've been and what they discussed. This transforms an invisible optimization into a navigable interface.

**Predictable Behavior**: Users can understand "after I'm inactive for an hour, the session collapses" much more easily than "when the system detects a topic change, it might summarize something." The rules are simple and observable.

**Integration with Buckets**: By assigning segments to buckets during collapse (when you have the complete session context), you get better bucket assignment than real-time topic detection. The janitor can see patterns across segments that would be invisible during active conversation.

---

## Architecture

### Data Model

New `continuum_segments` table provides first-class segment storage:

```sql
CREATE TABLE IF NOT EXISTS continuum_segments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    continuum_id UUID NOT NULL REFERENCES continuums(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Time boundaries
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    inactive_duration_minutes INTEGER,

    -- Content
    summary TEXT NOT NULL,
    summary_embedding vector(384),  -- AllMiniLM for search
    summary_generated_at TIMESTAMP WITH TIME ZONE,

    -- Message tracking
    first_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    last_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    message_count INTEGER NOT NULL,
    user_message_count INTEGER NOT NULL DEFAULT 0,
    assistant_message_count INTEGER NOT NULL DEFAULT 0,

    -- Processing tracking
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'collapsed', 'archived')),
    collapsed_at TIMESTAMP WITH TIME ZONE,

    -- Downstream processing flags
    memories_extracted BOOLEAN DEFAULT FALSE,
    memory_extraction_at TIMESTAMP WITH TIME ZONE,
    memory_count INTEGER DEFAULT 0,

    domain_blocks_updated BOOLEAN DEFAULT FALSE,
    domain_update_at TIMESTAMP WITH TIME ZONE,

    -- Tool usage tracking (for manifest display)
    tools_used JSONB DEFAULT '[]'::jsonb,

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
```

**Key Design Choices**:
- **RESTRICT on message foreign keys**: Prevents accidental deletion of segment boundary messages
- **Separate role-based message counts**: UI can show detailed statistics
- **Processing flags in-table**: Single source of truth for segment state, enables idempotent processing
- **Vector embeddings from day one**: Searchable manifest from the start
- **Status progression**: `active` → `collapsed` → `archived`

### Event Architecture

New events for segment lifecycle:

```python
@dataclass(frozen=True)
class SessionTimeoutEvent(ContinuumCheckpointEvent):
    """Inactivity threshold reached, trigger collapse."""
    inactive_duration: timedelta
    local_hour: int  # User's local time for context

@dataclass(frozen=True)
class SegmentCollapsedEvent(ContinuumCheckpointEvent):
    """Segment collapsed into manifest."""
    segment_id: str
    summary: str
    bucket_assignments: List[str]
    manifest_entry: Dict[str, Any]

@dataclass(frozen=True)
class ManifestUpdatedEvent(ContinuumCheckpointEvent):
    """Manifest structure changed, notify UI."""
    segment_count: int
    active_buckets: List[str]
```

### Smart Timeout Logic

Context-aware collapse windows based on time of day and user patterns:

```python
def should_collapse_segment(last_message_time, current_time, user_tz):
    inactive_duration = current_time - last_message_time
    local_hour = convert_to_local(current_time, user_tz).hour

    # Late night sessions (11pm-4am): longer timeout
    if 23 <= local_hour or local_hour <= 4:
        return inactive_duration > timedelta(hours=2)

    # Morning routine (6am-9am): shorter timeout
    elif 6 <= local_hour <= 9:
        return inactive_duration > timedelta(minutes=30)

    # Normal hours: standard timeout
    else:
        return inactive_duration > timedelta(hours=1)
```

**Edge Case Handling**:

**Marathon Sessions** (rapid-fire conversation for hours):
- Simple timeout isn't enough - user might send messages every 29 minutes for 8 hours
- Solution: Maximum segment length (e.g., 4 hours) forces collapse regardless of activity
- Ensures segments remain manageable and processing stays current

**Single-Message Sessions**:
- Valid pattern: User asks one question, gets answer, disappears
- Example: "The fall of man" at 9:12pm - one exchange, complete thought
- Don't merge these into larger segments - they're semantically complete
- Segment count isn't a problem to solve

**Mid-Conversation Timeout**:
- User steps away while actively discussing something
- System captures partial conversation state
- Next message starts fresh segment
- Context continuity maintained through manifest and bucket links
- Better than holding indefinitely waiting for "conversation completion"

### Collapse Pipeline

When timeout triggers:

```
SessionTimeoutEvent → Collapse Handler
    ↓
1. Generate one-line summary
    ↓
2. Assign to bucket(s) (with active bucket context)
    ↓
3. Create segment record with status='collapsed'
    ↓
4. Publish SegmentCollapsedEvent
    ↓
5. Trigger downstream processing (parallel):
   - Memory extraction (lt_memory/extraction.py)
   - Domain knowledge updates (if blocks enabled)
   - Vector embedding generation
   - Update manifest cache
    ↓
6. Publish ManifestUpdatedEvent → Notify UI
```

**Processing Guarantees**:
- Atomic segment creation (transaction-wrapped)
- Idempotent downstream processing (flags prevent re-processing)
- Event-driven coordination (loose coupling)

---

## UI/UX Vision

### Manifest as Infrastructure: Three Consumers, One Data Structure

**Critical Insight**: The manifest is not a UI feature - it's **actual infrastructure**. It's a first-class data structure that lives in the database and powers multiple presentation layers simultaneously.

This is a fundamental architectural principle: **build once, present differently based on the consumer**. The `continuum_segments` table is the single source of truth. Different systems read from it and present the data in ways appropriate to their context:

**1. Visual UI (Web Frontend)**:
```
┌─────────────────────────────────────────────────────────┐
│ MIRA                                    🔍 [arduino]     │
├─────────────┬───────────────────────────────────────────┤
│ MANIFEST    │                                           │
│             │  Current: [mira_architecture]             │
│ ▼ Today     │                                           │
│   ├─ 2:15pm │  User: How should we handle the bucket   │
│   │  Active │        assignment during collapse?        │
│   │  [mira] │                                           │
│   │         │  MIRA: Great question! During the        │
│   └─ 9:00am │        collapse event, we can...         │
│      [work] │                                           │
│             │                                           │
│ ▼ Yesterday │                                           │
│   ├─ 8:00am │  ┌─────────────────────────────┐         │
│   │[arduino]│  │ Active Buckets:             │         │
│   └─ 3:00pm │  │ • mira_architecture (157)   │         │
│      [cook] │  │ • arduino_projects (48)     │         │
│             │  │ • philosophy (12)           │         │
│ ▶ Oct 12    │  └─────────────────────────────┘         │
│             │                                           │
│ [≡] Buckets │                                           │
└─────────────┴───────────────────────────────────────────┘
```

**macOS Dock-Style Expansion**:

The manifest uses progressive disclosure to handle dense history without overwhelming the screen:

- **Compressed by default**: "Yesterday (5 sessions)" - shows day groupings
- **Hover/expand**: Individual session summaries with time ranges appear
- **Dense information expands gracefully**: Like dock icons that magnify on hover
- **Minimal screen real estate**: Sidebar doesn't dominate the interface
- **Power-user friendly**: Those who want deep history navigation get it; casual users see clean timeline

This pattern makes the dense history approachable. Users can see at a glance "I had 12 conversations last week" without needing to see all 12 summaries until they want to.

**Interaction Patterns**:
- Click segment → Load that session's context into active chat
- Click bucket badge → Filter manifest to show only that bucket's sessions
- Hover segment → Preview: message count, tools used, memories extracted
- Natural language commands: "Continue from yesterday's Arduino discussion"

**2. MIRA's Context (ManifestTrinket)**:

The `ManifestTrinket` injects the manifest into MIRA's system prompt using this exact ASCII tree structure:

```
CONVERSATION MANIFEST
├─ Today
│  ├─ [2:15pm - Active] Manifest architecture
│  └─ [9:00am - 10:15am] Morning standup
├─ Yesterday
│  ├─ [8:00am - 8:27am] Arduino servo debugging
│  └─ [3:00pm - 4:12pm] Nacho recipe research
└─ Oct 18

ACTIVE BUCKETS: mira_architecture (157 msgs), arduino_projects (48 msgs)
```

**Format Specification**:
- Use ASCII tree characters (`├─`, `│`, `└─`) for visual hierarchy
- Group by relative time labels ("Today", "Yesterday", then dates)
- Each segment shows: `[time range] Summary` (no inline bucket tags)
- Active segment marked with `- Active`
- Older days collapsed into single line (e.g., `└─ Oct 18`) - expandable on demand
- Active buckets list shows message counts for context (separate from segment list)

**Design Principle**: Make it easy for MIRA to ignore or pay attention. The tree structure allows natural scanning - MIRA can quickly skip over "Oct 18" if irrelevant, or focus on specific time periods when they matter to the current conversation. No inline tags or cluttered metadata competing for attention. Bucket information exists in the database and in the ACTIVE BUCKETS summary, but doesn't clutter the segment list.

**3. Search Index (Searchable Segments)**:
- Each segment is a searchable unit
- Vector embeddings of summaries enable semantic search
- Temporal + semantic retrieval
- Search results show segment context

---

## Integration with Buckets

From ADR_BUCKET_IMPROVED.md, buckets provide persistent topic organization. The manifest system integrates seamlessly:

### Segment-to-Bucket Assignment

**Key Insight**: Assign buckets during collapse, not in real-time during active conversation.

**Why This Works Better**:
- You have the complete session context (all messages, tools used, outcomes)
- Natural pause point - no pressure to decide instantly
- Can generate accurate summary first, then assign based on summary
- Avoids topic detection during rapid back-and-forth
- Janitor can refine assignments later with full cross-segment visibility

During collapse, assign segment to bucket(s):

```python
def assign_segment_to_buckets(segment, active_buckets):
    """
    Assign segment to buckets using MIRA's heuristic:
    'Will we discuss this topic again in future conversations?'

    - Yes → Real bucket (arduino_projects, mira_architecture)
    - No → Ephemeral bucket (ephemeral_001)
    """
    # LLM call with:
    # - Segment summary (already generated)
    # - Complete message list from segment
    # - List of recent/active buckets for context
    # - Bucket assignment guidance

    return bucket_assignments  # Can be multiple buckets
```

**Many-to-Many Relationship**:
- Segments can belong to multiple buckets
- "Arduino + smart home" discussion → both `arduino_projects` and `home_automation`
- Enables rich cross-referencing

### Dual-Index Navigation

**Temporal Navigation (Manifest)**:
- "Show me yesterday's conversations"
- "What did we discuss last Tuesday?"
- Browse chronologically

**Topical Navigation (Buckets)**:
- "All Arduino discussions this week"
- "Show philosophy bucket history"
- Browse by theme

**Combined Navigation**:
- Manifest provides "when"
- Buckets provide "what"
- Together: Complete conversational map

### Janitor Integration

The bucket janitor (from ADR_BUCKET_IMPROVED.md) operates on segments:

```python
# Consolidate similar buckets based on segment assignments
for segment in recent_segments:
    if segment.buckets contains similar_buckets(threshold=0.90):
        merge_buckets(source, dest)
        update_segment_bucket_assignment(segment)
```

---

## Migration Strategy

### Greenfield Advantage

We have the luxury of **no backwards compatibility requirements**:
- Non-production server
- Can flush entire conversation database and rebuild from scratch
- Build the right architecture without compromise

This is the dream scenario for implementing this architecture correctly.

**What "Executing It Right" Means**:

Every "we'll fix that later" becomes permanent. Every schema design choice compounds. This is the chance to build the system as if you had perfect knowledge of future requirements - and we DO have that knowledge from this discussion and the bucket ADR.

**No Compromises**:
- **Perfect schema design**: RESTRICT foreign keys on message boundaries, processing flags in-table, proper indexes from day one
- **Clean event architecture**: No legacy adapters, no compatibility shims, just the right events for the right purposes
- **Vector embeddings from start**: Not "we'll add search later" - searchable manifest from day one
- **Proper lifecycle management**: Status transitions (`active` → `collapsed` → `archived`) built-in, not retrofitted
- **Atomic operations**: Transaction-wrapped segment creation + message archival, no partial states
- **Observable from birth**: Metrics, logging, audit trails from the start, not bolted on when problems emerge

**The Nuclear Option We Have**:
```bash
DROP DATABASE mira_app CASCADE;
DROP DATABASE mira_memory CASCADE;
./deploy/rebuild_from_scratch.sh
```

No migration scripts. No backwards compatibility layers. No "legacy" anything. Just the right architecture, implemented correctly, once.

### Implementation Phases

**Phase 1: Schema & Core Infrastructure**
- Deploy `continuum_segments` schema
- Implement segment creation on message receipt
- Build timeout detection service
- Basic collapse pipeline (summary generation)

**Phase 2: Event Integration**
- Wire up `SessionTimeoutEvent` / `SegmentCollapsedEvent`
- Integrate with memory extraction pipeline
- Integrate with domain knowledge updates
- Add bucket assignment during collapse

**Phase 3: Manifest Trinket & API**
- Build `ManifestTrinket` for MIRA's context
- Create API endpoints for manifest data
- Implement segment search/retrieval
- Vector embedding generation for summaries

**Phase 4: Visual UI**
- Frontend sidebar component
- macOS dock-style expansion
- Segment navigation
- Bucket filtering

**Phase 5: Refinement & Observability**
- Smart timeout tuning
- Manifest performance optimization
- Search quality improvements
- Metrics & monitoring

---

## Success Criteria

The system succeeds when:

**User Experience**:
- Users can see their conversation history in temporal structure
- Manifest loads quickly regardless of history size
- Navigation feels natural and intuitive
- Search works effectively across segments
- Segment summaries are accurate and helpful
- Users can naturally reference past conversations ("yesterday's Arduino discussion")

**Technical Performance**:
- Timeout detection responds promptly to inactivity
- Collapse processing completes without blocking active conversation
- Manifest queries are fast enough to feel instant
- Vector search finds relevant segments efficiently
- Memory extraction happens at natural boundaries (not mid-conversation)

**System Behavior**:
- Segment count stays bounded through archival policies
- Bucket assignments feel accurate based on user feedback and navigation patterns
- Processing flags prevent duplicate work (idempotent operations)
- No data loss during collapse transitions
- Greenfield implementation has zero legacy artifacts or compatibility layers

---

## Implementation Decisions

### Core Architecture

**Bucket Integration**: Bucket assignment logic is **not** being implemented in this phase. Buckets and manifest are orthogonal features operating on the same segment data structure. Manifest provides temporal navigation; buckets would provide topical navigation. This implementation focuses solely on time-based segmentation.

**Segment Creation Strategy**: **Eager creation on second message**. When the second message in a continuum arrives (user or assistant), create segment with first message as `first_message_id`. Single-message exchanges don't create segments until confirmed as conversations. This aligns with semantic reality - segments represent conversations, not isolated messages.

**Migration Approach**: **Hard cutover**. Completely deprecate `ProgressiveHotCacheManager`, remove all topic-based logic, build segment system cleanly. No backwards compatibility, no feature flags, no parallel operation. Leverages greenfield advantage of non-production environment.

### Timeout & Collapse

**Timeout Detection Mechanism**: **APScheduler background job running every 5 minutes**. Decoupled from message processing path. Queries all active segments, checks timeout thresholds based on user timezone and time-of-day, publishes `SessionTimeoutEvent` for timed-out segments.

**Timeout Thresholds** (system-wide constants in `config.py`):
- Morning (6am-9am): 30 minutes
- Normal hours (9am-11pm): 60 minutes
- Late night (11pm-6am): 120 minutes

**Maximum Segment Duration**: **Design for 4-hour max, implement later**. Inspired by Claude Code's `/compact` functionality. Marathon sessions will eventually force collapse regardless of activity, but initial implementation focuses on timeout-based collapse only.

**Timeout Granularity**: 5-minute scheduler interval is acceptable because segments represent natural pause points, not precise boundaries. User experience unaffected by slight lag between actual timeout and collapse trigger.

### Summary Generation

**Summary Format**: **Telegraphic noun phrases** (e.g., "Arduino servo debugging", "Manifest architecture planning"). Concise, scannable, fits tight display spaces in manifest tree.

**Summary Context**: **Include tool names used** in context provided to LLM. Helps create more specific summaries without cluttering with full tool call details. Tool usage stored separately in `segment.tools_used` JSONB field.

**Fallback Strategy**: If summary generation fails (LLM error, timeout), create segment with **fallback summary** like "Conversation segment" or preview of first/last message. Segment always created, never blocks collapse pipeline.

### Processing Coordination

**Collapse Processing**: **Fully synchronous**
1. Segment creation with status='collapsed'
2. Summary generation (with fallback)
3. Vector embedding generation
4. Memory extraction (submits to Batch API, returns immediately)
5. Domain knowledge updates (if enabled)

All steps execute synchronously in collapse handler. Memory extraction uses Anthropic Batch API (polling handled separately by existing APScheduler job), but the submission itself is synchronous.

**Embedding Generation**: **Synchronous during collapse**. Segment must be immediately searchable after collapse. 384-dim AllMiniLM embedding generated as part of collapse transaction.

**Processing Failures**: **Mark segment for manual review**. If memory extraction or domain update submission fails, add `metadata.processing_failed = true` flag. Provides observability and recovery path without retry complexity. Failed segments visible to admin for investigation.

**Processing Idempotency**: Use boolean flags in segments table:
- `memories_extracted` - prevents duplicate memory extraction
- `domain_blocks_updated` - prevents duplicate domain updates

Note: Batch API polling is handled by existing scheduled job (`lt_memory_extraction_batch_polling`), not by collapse handler.

### Display & Caching

**Manifest Display Depth**: **Last 30 segments** in MIRA's system prompt. Fixed count regardless of time span. Balances context richness with prompt length. Older segments still searchable but not injected into every prompt.

**Session Boundaries**: **Keep session boundary messages** alongside manifest. Manifest provides navigation structure, boundaries provide in-conversation temporal markers. Both serve distinct UX purposes.

**Manifest Cache Structure**:
- **Key format**: `manifest:{user_id}:segments` (single key stores full manifest)
- **Backend**: Valkey-backed cache with 1-hour TTL
- **Invalidation**: Event-driven on `ManifestUpdatedEvent`
- **Fallback**: Database query on cache miss (graceful degradation)

Follows existing pattern from `continuum:{user_id}:messages` cache.

### First Segment Creation

**Trigger Point**: **Wait for second message** before creating first segment.

Rationale:
- First message alone = user input without context
- Second message (response) = confirms active conversation
- Avoids creating meaningless single-message "segments"
- Matches semantic reality of conversations

Edge case: User sends message and abandons before response → no segment created → correct behavior (no conversation occurred).

---

## Technical Considerations

### Performance Optimizations

**Lazy Loading**:
- Load only visible segments initially
- Fetch older segments on scroll/expand
- Prefetch next page during idle time

**Caching Strategy**:
- Valkey cache for active manifest structure
- PostgreSQL as source of truth
- Invalidate cache on `ManifestUpdatedEvent`

**Vector Index Optimization**:
- IVFFlat index for segment embeddings
- Tuned list count based on corpus size
- Periodic index maintenance

### Data Retention

**Active Segments**:
- Keep in active state until collapsed
- Store all message metadata
- Full search/retrieval capabilities

**Collapsed Segments**:
- Summary + metadata always available
- Message content retained for N days (configurable)
- Full context on-demand load

**Archived Segments**:
- Summary only, messages in cold storage
- Still searchable via summary embeddings
- Message retrieval requires restore operation

### Observability

**Metrics to Track**:
- Segment creation rate (segments/day per user)
- Average segment duration
- Timeout trigger distribution (by time of day)
- Collapse processing time (total wall-clock time)
- Summary generation success/failure rate
- Memory extraction batch submission success rate
- Domain update processing success rate
- Manifest query latency
- Manifest cache hit/miss ratio

**Logging**:
- Segment lifecycle events (created, collapsed, archived)
- Timeout triggers with context (inactive duration, local hour)
- Summary generation (success with token count, or fallback reason)
- Processing failures marked for manual review
- Cache invalidation events

---

## References

**Related Systems**:
- `cns/core/progressive_hot_cache_manager.py` (current implementation being replaced)
- `cns/core/events.py` (event definitions)
- `cns/services/summary_generator.py` (summary generation logic to reuse)
- `lt_memory/extraction.py` (memory extraction integration point)
- `docs/ADRs/ADR_BUCKET_IMPROVED.md` (bucket-based topic organization)

**Database Schema**:
- `deploy/mira_service_schema.sql` (contains `continuum_segments` table)

**Key Insight**: This architecture transforms the conversation cache from an invisible optimization into a visible, navigable interface. By switching from reactive topic-based triggers to proactive time-based sessions, we create a system that matches how humans naturally segment their interactions - by time periods rather than abstract topic boundaries.
