# Memory Lifecycle: Consolidation, Splitting, Supersession & Link Traversal

*Technical architecture for memory graph maintenance and evolution*

---

## Overview

While `ARCHITECTURE_LT_MEMORY.md` covers the foundational decay-based scoring system, this document covers the **lifecycle operations** that maintain and evolve the memory graph over time:

1. **Consolidation** - Merging similar memories to eliminate redundancy
2. **Splitting** - Breaking verbose memories into focused units
3. **Supersession** - Temporal updates that mark old information as outdated
4. **Link Traversal** - Navigating the relationship graph

These operations run as scheduled jobs or are triggered by specific conditions, continuously refining the memory graph for optimal retrieval and reasoning.

---

## Memory Consolidation

### What It Does

Consolidation merges multiple similar memories into a single consolidated memory. The key insight: redundant memories waste context tokens and create retrieval noise. A user who mentions "loves coffee" in 5 conversations doesn't need 5 memories—they need one well-connected one.

### When It Triggers

| Trigger | Interval | Function |
|---------|----------|----------|
| Scheduled job | Every 7 days | `run_consolidation_for_all_users()` |
| Manual | On-demand | `BatchingService.submit_consolidation_batch()` |

### The Multi-Phase Process

Consolidation uses a **two-pass LLM verification** to ensure correctness for consequential decisions.

#### Phase 1: Hub-Based Cluster Identification

**File**: `lt_memory/refinement.py:173-273`

```
RefinementService.identify_consolidation_clusters():
    1. Find "hub" memories (high importance OR well-connected)
       - importance ≥ 0.3 AND access_count ≥ 5, OR
       - ≥ 5 non-entity inbound links
    2. Sort by importance, take top 50
    3. For each hub, vector-search for similar memories
    4. Form clusters where similarity ≥ threshold
    5. Return clusters with consolidation_confidence ≥ threshold
```

**Why hub-based?** Starting from important, well-connected memories ensures we consolidate around conceptual anchors rather than arbitrary groupings.

#### Phase 2: LLM Analysis (First Pass)

**File**: `lt_memory/batching.py:1089-1162`

- Model: `claude-sonnet-4-5-20250929` (reasoning model for consequential decisions)
- Temperature: 1.0 (required for extended thinking)
- Extended Thinking: enabled (budget_tokens: 1024)

The LLM receives the cluster and returns:
```json
{
  "should_consolidate": true,
  "consolidated_text": "Combined memory content...",
  "reasoning": "Why these memories should merge..."
}
```

#### Phase 3: Consolidation Review (Second Pass)

**File**: `lt_memory/batching.py:1410-1591`

A lightweight verification gate:
- Model: `claude-haiku-4-5`
- Temperature: 0.0 (deterministic)
- Minimal prompt: "Approve consolidation? yes/no"

**Why two passes?** The reasoning model proposes, the fast model approves. This catches edge cases where extended thinking produced an overconfident but wrong decision.

#### Phase 4: Link Bundle Transfer & Archival

**File**: `lt_memory/processing/consolidation_handler.py:41-212`

When consolidation is approved:

```
1. COLLECT link bundles from all old memories:
   - inbound_links: memories pointing TO these
   - outbound_links: memories these point TO
   - entity_links: named entity connections

2. DEDUPLICATE links:
   - Remove self-references to old memories
   - Keep highest-confidence version of duplicates

3. CREATE consolidated memory:
   - Text: LLM-provided consolidated_text
   - Importance: MEDIAN of old scores (preserves value without inflation)
   - consolidates_memory_ids: [old_id_1, old_id_2, ...] for traceability

4. TRANSFER link bundles to new memory

5. REWRITE source memory links:
   - Find memories with outbound links to old IDs
   - Replace with new memory ID
   - Maintains graph continuity

6. ARCHIVE old memories:
   - is_archived = TRUE, archived_at = NOW()
   - Still queryable but filtered from active searches
```

### Consolidation Data Model

```python
class ConsolidationCluster:
    memory_ids: List[UUID]           # Memories in this cluster
    memory_texts: List[str]          # Their content
    similarity_scores: List[float]   # Pairwise similarities
    consolidation_confidence: float  # Overall cluster coherence
```

---

## Memory Splitting

### What It Does

Splitting breaks verbose memories into multiple focused memories. The opposite of consolidation—sometimes a memory tries to capture too much and should be decomposed.

### When It Triggers

**File**: `lt_memory/refinement.py:107-171`

| Condition | Threshold |
|-----------|-----------|
| Text length | ≥ `verbose_threshold_chars` |
| Memory age | ≥ 7 days |
| Access count | ≥ 5 (must be useful) |
| Refinement cooldown | Not refined within `refinement_cooldown_days` |
| Rejection limit | `refinement_rejection_count` < max |

### The Process

```
RefinementService.identify_verbose_memories():
    1. Scan all active (non-archived) memories
    2. Filter by length, age, access, cooldown, rejection thresholds
    3. Sort by character count (longest first)
    4. Return top N candidates

RefinementService.refine_verbose_memory_sync():
    1. Send to LLM with refinement system prompt
    2. LLM returns action + data
```

### LLM Actions

| Action | Result |
|--------|--------|
| **TRIM** | Single refined memory, more concise |
| **SPLIT** | N new focused memories |
| **DO_NOTHING** | Increment `refinement_rejection_count` |

```json
{
  "action": "split",
  "split_memories": [
    "Memory 1: User prefers functional programming paradigms...",
    "Memory 2: User has experience with Haskell and Elm..."
  ],
  "confidence": 0.89,
  "reason": "Original memory conflates preference with experience"
}
```

### Key Difference from Consolidation

| Aspect | Consolidation | Splitting |
|--------|--------------|-----------|
| Original memories | **Archived** | **Stay active** |
| New memory tracks origin | `consolidates_memory_ids` | `consolidates_memory_ids` |
| Importance inheritance | Median of old scores | Each inherits original score |

**Why keep originals for splitting?** Split memories are a different view of the same information. The original might still be useful for full context, while splits are useful for targeted retrieval.

---

## Memory Supersession

### What It Does

Supersession creates a temporal relationship where new information explicitly marks old information as outdated. Unlike consolidation (which removes redundancy) or splitting (which decomposes), supersession preserves versioning history.

### How It Works

**Documented in**: `docs/MEMORY_LINK_TYPES.md:32-43`

**Definition**: Information has changed over time, making previous memory obsolete due to temporal progression.

**Example**:
```
New Memory: "Now using PostgreSQL with pgvector for vector operations"
Old Memory: "Using Pinecone for vector similarity search"
Link Type: supersedes (one-way from new → old)
```

### Storage Structure

Stored in memory's `outbound_links` JSONB array:

```json
{
  "uuid": "old-memory-id",
  "type": "supersedes",
  "confidence": 0.92,
  "reasoning": "Switched from Pinecone to pgvector for cost/latency",
  "created_at": "2025-10-15T..."
}
```

### Lifecycle of Superseded Memories

1. **Creation**: Link created during extraction/linking when LLM classifies relationship
2. **Active**: Old memory stays active (not archived)
3. **Presentation**: Link type tells MIRA "this is outdated, here's current state"
4. **Query behavior**: Both old and new are retrievable; link provides context

### When Supersession Is Created

**File**: `lt_memory/processing/batch_coordinator.py`

During the relationship classification batch:
1. New memory extracted
2. `LinkingService` finds semantically similar candidates
3. LLM classifies relationship type
4. If `supersedes`, bidirectional links created

**Classification criteria**: Does temporal progression make the old memory no longer current?

### Supersession vs Other Operations

| Operation | Old Memory State | Relationship |
|-----------|------------------|--------------|
| Consolidation | Archived | Structural (redundancy) |
| Splitting | Active | Structural (decomposition) |
| Supersession | Active | Semantic (versioning) |

---

## Link Traversal

### Link Types Overview

**LLM-Classified (Sparse, High-Value)**:
- `conflicts` - Mutually exclusive information
- `supersedes` - Temporal update
- `causes` - Direct causation
- `instance_of` - Concrete example of pattern
- `invalidated_by` - Empirical disproof
- `motivated_by` - Intent/reasoning behind decision
- `null` - No meaningful relationship (default)

**Automatic Structural (Dense, Cheap)**:
- `was_context_for` - Memory explicitly referenced during conversation
- `shares_entity:{EntityName}` - Memories mention same named entity

### Traversal Implementation

**File**: `lt_memory/linking.py:384-484`

```python
def traverse_related(memory_id: UUID, max_depth: int) -> List[Memory]:
    visited = set()
    current_level = [memory_id]

    for depth in range(1, max_depth + 1):
        next_level = []
        for memory in load_memories(current_level):

            # HEAL-ON-READ: Clean dead links opportunistically
            dead_links = find_dead_links(memory.outbound_links)
            if dead_links:
                db.remove_dead_links(memory.id, dead_links)
                log(f"Heal-on-read removed {len(dead_links)} dead links")

            for link in memory.outbound_links:
                if link.uuid not in visited:
                    next_level.append(link.uuid)
                    visited.add(link.uuid)
                    # Track: depth, link_type, confidence, source_id

        current_level = next_level

    return load_memories_with_link_metadata(visited)
```

### Bidirectional Storage

Every link stored in both directions for efficient traversal:

```
Memory A:
  outbound_links: [{uuid: B, type: "causes", confidence: 0.85}]

Memory B:
  inbound_links: [{uuid: A, type: "causes", confidence: 0.85}]
```

**Why denormalized?**
- Hub score requires counting inbound links
- Traversal works from either direction without joins
- JSONB array operations are fast

### Dead Link Healing

Links can point to archived/deleted memories. Rather than proactive scanning:

1. During traversal, check if linked memory exists
2. If not found, call `db.remove_dead_links()`
3. Log cleanup for monitoring
4. Continue traversal with remaining valid links

**Acceptable variance**: 1-2 phantom links per memory. Proactive scanning overhead isn't worth marginal consistency gain.

### Link Statistics

```python
LinkingService.get_link_statistics() -> Dict[str, int]:
    {
        "total_inbound": 142,
        "total_outbound": 138,
        "by_type": {
            "supersedes": 23,
            "causes": 45,
            "motivated_by": 31,
            ...
        }
    }
```

---

## Batch Processing Pipeline

### Complete Flow: Extraction → Linking → Relationships

**File**: `lt_memory/processing/batch_coordinator.py`

```
┌─────────────────────────────────────────────────────────┐
│                    Conversation                          │
│          (messages since last extraction)                │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│               Extraction Batch                           │
│  • Build memory context (referenced + pinned)            │
│  • Submit to Anthropic Batch API                         │
│  • LLM extracts discrete memories                        │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│           Extraction Result Handler                      │
│  • Parse and validate memories                           │
│  • Deduplicate (fuzzy + vector similarity)               │
│  • Store with embeddings                                 │
│  • Extract entities (spaCy NER)                          │
│  • Build linking hints for classification                │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│         Relationship Classification Batch                │
│  • Memory pairs: extraction hints + similarity search    │
│  • LLM classifies relationship type                      │
│  • Returns: type, confidence, reasoning                  │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│           Relationship Result Handler                    │
│  • Filter out "null" relationships                       │
│  • Create bidirectional links                            │
│  • Update inbound/outbound arrays                        │
└─────────────────────────────────────────────────────────┘
```

---

## Scheduled Jobs

| Job | Interval | Purpose |
|-----|----------|---------|
| `poll_extraction_batches()` | 1 minute | Check Anthropic batch status |
| `poll_relationship_batches()` | 1 minute | Check relationship classification |
| `retry_failed_extractions()` | 6 hours | Retry failed extraction batches |
| `run_full_refinement()` | 7 days | Split/trim verbose memories |
| `submit_consolidation_batch()` | 7 days | Merge similar memories |
| `recalculate_temporal_scores()` | Daily | Update time-based scores |
| `bulk_recalculate_scores()` | Daily | Recalculate all importance scores |
| `run_entity_gc()` | Monthly | Clean orphaned entities |
| `cleanup_old_extraction_batches()` | Daily | Remove stale batch records |

---

## Design Philosophy

### Why Two-Phase Consolidation?

Consolidation is irreversible (archives originals). The two-phase approach:
1. **Reasoning model proposes** - Extended thinking catches subtle issues
2. **Fast model verifies** - Gate-keeps against overconfident proposals

This adds latency but prevents costly mistakes.

### Why Median Importance for Consolidated Memories?

Alternatives considered:
- **Max**: Inflates importance, gaming potential
- **Mean**: Outliers skew result
- **Median**: Preserves typical value without inflation

### Why Keep Superseded Memories Active?

Users often want version history: "What did I used to think about X?" Archiving would lose this capability. The `supersedes` link provides context without removing access.

### Sparse > Dense for LLM Links

The default classification is `null` (no link). Better to miss weak signals than add noise. Dense, low-confidence links:
- Waste context tokens
- Create retrieval noise
- Add computational overhead for traversal

High-confidence, sparse links enable precise reasoning.

---

*Implementation: `lt_memory/refinement.py` (consolidation/splitting), `lt_memory/linking.py` (traversal), `lt_memory/processing/batch_coordinator.py` (pipeline orchestration), `lt_memory/batching.py` (batch operations)*
