# Memory Link Types & Graph Presentation

## Overview

Memory links serve dual purposes:
1. **Graph structure** for traversal and discovery
2. **Prompt metadata** that enables MIRA's reasoning patterns

Link types appear in MIRA's context window alongside memory text, framing the relationship and enabling specific cognitive moves.

---

## LLM-Classified Link Types (Expensive, Sparse, High-Value)

These require semantic understanding and are classified during memory linking. Default to `null` when uncertain - sparse, high-confidence links are better than dense, noisy ones.

### 1. conflicts
**Definition**: Mutually exclusive or contradictory information about the same specific attribute.

**Decision Test**: If one is true, must the other be false?

**Example**:
- Memory A: "Prefers TypeScript strict mode for all projects"
- Memory B: "Disabled strict mode to ship faster"

**Reasoning Affordance**: Signals MIRA to detect contradiction and seek clarification about which is current/correct. Enables "I have conflicting information - let me ask" behavior.

**Classification Guidance**: Only for direct logical contradictions, not different facets or temporal changes (use `supersedes` for that).

---

### 2. supersedes
**Definition**: Information has changed over time, making previous memory obsolete due to temporal progression.

**Decision Test**: Does temporal progression make the old memory no longer current?

**Example**:
- New Memory: "Now using PostgreSQL with pgvector for vector operations"
- Old Memory: "Using Pinecone for vector similarity search"

**Reasoning Affordance**: Signals MIRA this is versioned information - the old memory is outdated, here's the current state. Prevents using stale information.

**Classification Guidance**: For factual updates over time. Not for contradictions (use `conflicts`) or corrections based on evidence (use `invalidated_by`).

---

### 3. causes
**Definition**: Source memory directly leads to or triggers the target memory.

**Decision Test**: Did this make that happen?

**Example**:
- Cause: "Server costs increased 300% after migration"
- Effect: "Rolled back microservices architecture to monolith"

**Reasoning Affordance**: Enables causal chain reasoning and "what led to this?" reconstruction. Critical for learning from past decisions and understanding consequences.

**Classification Guidance**: For direct causation, not correlation or temporal sequence (don't confuse with `precedes`). Must be a clear cause → effect relationship.

---

### 4. instance_of
**Definition**: Source memory is a specific concrete example of the general pattern/principle in target memory.

**Decision Test**: Is this a concrete occurrence that exemplifies that pattern?

**Example**:
- Instance: "Got rate limited by OpenAI API at 3pm during batch processing of 10k requests"
- Pattern: "OpenAI API has aggressive rate limits that require careful batch sizing"

**Reasoning Affordance**: Bridges abstract/concrete gap. Enables pattern recognition and "here's a specific example of that principle" reasoning. Multiple instances strengthen confidence in the pattern.

**Classification Guidance**: The instance should be detailed/specific while the pattern is general/abstract. One-to-many relationship (many instances → one pattern).

---

### 5. invalidated_by
**Definition**: Source memory's factual claims are disproven by concrete evidence in target memory.

**Decision Test**: Does empirical evidence show this assumption/claim was wrong?

**Example**:
- Assumption: "Database can handle 10k writes/second"
- Evidence: "Load test measured consistent failures at 6k writes/second"

**Reasoning Affordance**: Stronger than `conflicts` - this is directional with empirical proof. Signals MIRA to trust evidence over assumptions and update mental models.

**Classification Guidance**: Requires concrete evidence that disproves the claim. Not just disagreement (use `conflicts`) or temporal change (use `supersedes`).

---

### 6. motivated_by
**Definition**: Source memory captures the intention, reasoning, or goal behind the action/decision in target memory.

**Decision Test**: Does this explain WHY that decision/action was taken?

**Example**:
- Action: "Implementing comprehensive request rate limiting across all API endpoints"
- Motivation: "Concerned about API abuse costs after $2k surprise bill last month"

**Reasoning Affordance**: Preserves the "why" behind decisions. Enables MIRA to explain historical choices and reason about whether motivations still apply to current situations.

**Classification Guidance**: Captures intention, not just correlation. The motivation should explain the reasoning, not just be temporally related.

---

### 7. null
**Definition**: No meaningful relationship exists between memories, or relationship is uncertain.

**Decision Test**: Default when none of the other types clearly apply.

**Reasoning Affordance**: Explicit absence of relationship. Prevents spurious links that would add noise to MIRA's reasoning.

**Classification Guidance**: When in doubt, choose `null`. Sparse, high-confidence links are more valuable than dense, uncertain ones.

---

## Automatic Structural Links (Cheap, Dense, Foundational)

These are created without LLM classification, providing dense graph structure for traversal.

### was_context_for (Automatic)
**What it captures**: Memory A was explicitly referenced (via memory reference tags) during the conversation that produced Memory B.

**How to create**: During extraction, only link memories that MIRA explicitly cited in their response using memory reference tags. These are memories that directly influenced the response generation, not just surfaced candidates.

**Example**:
- Referenced Memory: "Taylor is rebuilding MIRA's lt_memory system"
  *(MIRA cited this using a memory reference tag during the conversation)*
- New Memory: "Decided to use JSONB arrays for bidirectional link storage"
  *(Extracted from that same conversation)*
- Link: Referenced memory `was_context_for` new memory

**Reasoning Affordance**: Shows MIRA "I learned this while explicitly referencing that" - reconstructs high-confidence cognitive context. Much stronger signal than just "was surfaced" since it indicates the memory directly influenced thinking.

**Implementation Note**: Parse MIRA's responses for memory reference tags during the conversation, extract the referenced memory UUIDs, and create `was_context_for` links from those UUIDs to newly extracted memories. This keeps the links sparse and high-quality.

---

### shares_entity:{EntityName} (NER-based)
**What it captures**: Memories mention the same named entity (person, project, technology, location).

**How to create**: During extraction, run fast NER (spaCy or similar) to identify entities. Create links between memories sharing entities.

**Example**:
- Memory A: "MIRA uses PostgreSQL with pgvector for vector similarity"
- Memory B: "Considering upgrading PostgreSQL to version 16 for performance"
- Link: Both `shares_entity:PostgreSQL`

**Reasoning Affordance**: Entity-based clustering. "Tell me everything about X" queries can traverse entity links to find all related memories, even if semantically distant.

**Implementation Note**: Store entity name in link metadata. Entity extraction should normalize names (PostgreSQL = Postgres = postgres).

---

## Graph Presentation Strategy

### Context Window Format

When surfacing memories to MIRA, present them in a hierarchical structure that makes relationships immediately visible:

```
=== SURFACED MEMORIES ===

[Primary Memory]
ID: a7b3c9d2
Text: "Decided to use JSONB arrays for bidirectional link storage in MIRA's memory system"
Importance: 0.82
Created: 2025-10-05

  ├─ [^ Linked Memory - link type: motivated_by | confidence: 0.91]
  │  ID: e5f1g8h4
  │  Text: "Want to avoid expensive JOIN queries during memory graph traversal"
  │  Importance: 0.75
  |
  |    |
  |    └─ [^ Linked Memory - link type: motivated_by | confidence: 0.88]
  |        ID: 9261f32c
  |        Text: "MIRA's internal graph is expansive"
  |        Importance: 0.83
  │
  └─ [^ Linked Memory - link type: was_context_for | confidence: auto]
     ID: 5914f11b
     Text: "Taylor is rebuilding MIRA's lt_memory system with focus on performance"
     Importance: 0.88

[Primary Memory]
ID: 93a96ed6
Text: "PostgreSQL pgvector extension provides efficient approximate nearest neighbor search"
Importance: 0.79
Created: 2025-10-04

  └─ [^ Linked Memory - link type: instance_of | confidence: 0.87]
     ID: c4d5e6f7
     Text: "Benchmarked pgvector IVFFlat index - achieved <50ms search on 100k vectors"
     Importance: 0.71
```

### Key Presentation Principles

1. **Visual Hierarchy**: Indent linked memories to show they're expansions of primary memories
2. **Link Type Visibility**: Show link type prominently - it's critical reasoning metadata
3. **Confidence Scores**: Show for LLM-classified links; mark automatic links as "auto"
4. **Limit Depth**: Display limited levels deep to avoid context window bloat
5. **Metadata Completeness**: Include importance, ID, creation date for full context

### Reranking Implications

After traversal, before presentation:

1. **Type-based weighting**:
   - `conflicts`, `invalidated_by` → highest priority (critical for accuracy)
   - `supersedes` → high priority (versioning matters)
   - `causes`, `motivated_by` → medium-high (reasoning context)
   - `instance_of`, `was_context_for` → medium (supporting context)
   - `shares_entity` → lower (unless entity is central to query)

2. **Confidence filtering**: For LLM-classified links, filter by minimum confidence threshold

3. **Importance inheritance**: Linked memories should be weighted by both their own importance and the importance of the primary memory they're linked from

4. **Deduplication**: If a memory appears both as primary (from vector search) and linked (from traversal), show it once in the higher-priority position

---

## Implementation Checklist

### Extraction Phase
- [ ] LLM classification prompt includes all 6 types + null with clear decision criteria
- [ ] Run NER on extracted memory text to identify entities
- [ ] Create `shares_entity:{name}` links for memories with common entities
- [ ] Parse MIRA's responses for memory reference tags to identify explicitly cited memories
- [ ] Create `was_context_for` links from explicitly referenced memories → newly extracted memories

### Storage Phase
- [ ] Store link type, confidence, reasoning, created_at in JSONB link objects
- [ ] Store entity names in link metadata for `shares_entity` links
- [ ] Maintain bidirectional links (outbound and inbound arrays)

### Retrieval Phase
- [ ] Vector similarity search finds primary memories
- [ ] Link traversal expands from primary memories with appropriate depth limit
- [ ] Rerank combined results by type weight + importance + confidence
- [ ] Format for context window with visual hierarchy
- [ ] Include link type, confidence, importance for each memory

### Classification Tuning
- [ ] Set confidence threshold for link inclusion
- [ ] Default to `null` when uncertain - optimize for precision over recall
- [ ] Monitor classification consistency across extraction runs

---

## Design Philosophy

**Sparse, High-Confidence LLM Links**: Classify only relationships that require semantic understanding and enable specific reasoning patterns. Better to miss weak links than create noisy ones.

**Dense Automatic Links**: Create cheap structural links (entity co-occurrence, conversational context) that provide foundational graph structure for traversal.

**Reasoning Over Structure**: Link types exist primarily to enable MIRA's reasoning. If a link type doesn't unlock a specific cognitive move, it's just prompt noise.

**Visual Clarity**: The graph presentation in MIRA's context must be immediately scannable - relationship types should jump out visually.
