# System Blueprint: Bucket-Based Conversation Organization

**Version**: 1.0
**Last Updated**: 2025-09-29
**System**: MIRA Conversation Management
**Related**: ADR_bucket_based_conversations.md

---

## Executive Summary

This blueprint describes a bucket-based conversation organization system that replaces linear topic detection with a self-organizing topic hierarchy. Messages are assigned to persistent topic buckets that survive conversation gaps, with async janitor processes handling consolidation and an on-demand retrieval system for resurrecting old topics.

**Key Innovation**: Self-healing bucket assignment where MIRA makes fast, imperfect classifications that correct themselves within 1-2 conversation turns through vector similarity matching.

---

## System Overview

```
SESSION START (User returns after gap):
┌─────────────────────────────────────────────────────────────┐
│  Context Restoration (ONE TIME)                              │
│  - Load last N raw messages                                  │
│  - Load active bucket summaries                              │
│  - Include self-healing hints if applicable                  │
│  - Build conversation context                                │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
                 [Context Loaded]

ACTIVE CONVERSATION (Context stays loaded):
┌─────────────────────────────────────────────────────────────┐
│                    User sends message                        │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  MIRA Processing (uses pre-loaded context)                   │
│  - Assigns message to bucket (new or existing)               │
│  - May execute bucket commands (mv, pin, merge)              │
│  - Generates response                                        │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Post-Processing                                             │
│  - If new bucket: vector similarity check                    │
│  - Update bucket message count, last_updated                 │
│  - Trigger summary if threshold reached                      │
│  - Queue similarity hints for next turn if needed            │
└──────────────────────────────────────────────────────────────┘
     │                                    │
     │                                    │ If similarity hint queued,
     │                                    │ inject into context for
     │                                    │ next message only
     │                                    ▼
     └────────► Continue conversation with
                updated context (same session)

BACKGROUND (Independent of conversation):
┌─────────────────────────────────────────────────────────────┐
│  Async Janitor (runs every 6 hours)                          │
│  - Consolidate similar buckets                               │
│  - Archive low-value buckets                                 │
│  - Detect velocity anomalies (life changes)                  │
│  - Promote ephemeral buckets that grew                       │
│  - Clean daily ephemeral buckets                             │
└─────────────────────────────────────────────────────────────┘
```

**Key Point**: Context restoration happens ONCE at session start. During active conversation, context stays loaded and is only updated incrementally (new messages added, similarity hints injected). The system does not rebuild context from scratch on every message.

---

## Core Concepts

### Bucket Definition

A **bucket** is a persistent collection of messages organized around a coherent topic or conversation thread.

**Properties**:
- `id`: Unique identifier (e.g., `pets_advice_001`, `mira_development_001`)
- `description`: Human-readable topic summary
- `created_at`: Initial creation timestamp
- `last_updated`: Last message added to bucket
- `message_count`: Total messages in bucket
- `status`: `active`, `archived`, `ephemeral`
- `priority`: `high`, `normal`, `low`
- `metadata`: JSON blob for extensibility
  - `return_frequency`: How often user comes back to this topic
  - `velocity_score`: Recent message acceleration
  - `starred`: High-affinity bucket (frequently returned to)
  - `life_change_detected`: Boolean flag from velocity detector

### Bucket Lifecycle States

```
┌──────────┐     Message count < 3      ┌────────────┐
│          │     Age < 24 hours          │            │
│ Ephemeral├────────────────────────────►│  Archived  │
│          │     No return visits        │            │
└────┬─────┘                             └────────────┘
     │
     │ Message count > 5
     │ or Velocity spike
     │
     ▼
┌──────────┐     Regular growth          ┌────────────┐
│          │                              │            │
│  Active  ├─────────────────────────────►│   Starred  │
│          │     High return rate         │            │
└────┬─────┘                              └──────┬─────┘
     │                                           │
     │ No activity > 30 days                     │
     │ Low message count                         │
     │                                           │
     ▼                                           │
┌──────────┐                                     │
│          │◄────────────────────────────────────┘
│ Archived │      Rare but still accessible
│          │
└──────────┘
```

### Message-to-Bucket Relationship

Each message has bucket metadata:
```json
{
  "bucket_id": "pets_advice_001",
  "new_bucket": false,
  "bucket_return": true,
  "bucket_commands": ["mv puppy_training_001 pets_advice_001"]
}
```

---

## Component Specifications

### 1. Bucket Janitor

**Purpose**: Async process that maintains bucket health through consolidation, archival, and optimization.

**Execution Frequency**: Every 6 hours (configurable)

**Operations**:

#### A. Consolidation
```
For each active bucket B:
  Find similar buckets S where similarity(B, S) > CONSOLIDATION_THRESHOLD
  If S exists and S.priority >= B.priority:
    Move B's messages to S
    Update S.summary with B's context
    Delete B
    Log: "Consolidated {B.id} into {S.id} (similarity: {score})"
```

**Consolidation Threshold**: 0.90 (90% semantic similarity)

#### B. Archival
```
For each bucket B:
  If B.status == "ephemeral" and B.age > 24 hours:
    Move to daily summary: "misc_conversations_{date}"
    Delete B

  If B.status == "active" and B.last_updated > 30 days and B.message_count < 3:
    B.status = "archived"
    Compress to summary-only storage

  If B.status == "active" and B.last_updated > 90 days and B.return_frequency < 0.1:
    B.status = "archived"
    Keep accessible but not in active set
```

#### C. Velocity Detection
```
For each bucket B created in last 7 days:
  velocity_score = (B.message_count / B.age_in_days) * conversation_percentage

  If velocity_score > LIFE_CHANGE_THRESHOLD:
    B.metadata["life_change_detected"] = true
    B.priority = "high"

    # Detect related sub-topics
    Create sub-bucket detection:
      If multiple new buckets share semantic space with B:
        Group as sub-buckets of B

    # Decay competing old topics
    For old_buckets in same semantic space:
      If old_bucket.last_updated > 14 days:
        Schedule for faster archival
```

**Life Change Threshold**:
- Velocity score > 15 (e.g., 45 messages in 3 days = 15)
- AND conversation percentage > 40%

#### D. Ephemeral Promotion
```
For each ephemeral bucket E:
  If E.message_count > 5:
    Convert to active bucket
    Generate proper bucket ID
    Log: "Promoted ephemeral {E.id} to active"
```

---

### 2. Self-Healing Assignment

**Purpose**: Correct bucket misassignments through vector similarity hints.

**Trigger**: Immediately after MIRA creates a new bucket

**Algorithm**:
```
1. MIRA creates new bucket: "puppy_training_001"
2. System embeds bucket description
3. Vector search finds similar buckets:
   - "pets_advice_001" (similarity: 0.92)
   - "dogs_general_001" (similarity: 0.88)

4. If similarity > SUGGESTION_THRESHOLD (0.85):
   Inject into next system context:
   "Note: Your new bucket 'puppy_training_001' is 92% similar to
    existing 'pets_advice_001'. If same topic, add to your next
    <mira:analysis>:
    <mira:bucket>mv puppy_training_001 pets_advice_001</mira:bucket>"

5. MIRA sees hint on next turn and either:
   - Executes mv command (agrees they're same)
   - Ignores (intentionally separate topics)

6. System learns from MIRA's choice
```

**Suggestion Threshold**: 0.85 (85% similarity)

**Command Syntax**:
- `mv <source> <dest>` - Move all messages from source to dest, delete source
- `merge <A> <B> <new_name>` - Combine A and B into new bucket
- `split <source> <dest1> <dest2>` - Reassign messages from source
- `pin <bucket_id>` - Load bucket into context window

---

### 3. Context Restoration

**Purpose**: Load appropriate context when user starts a new session.

**Trigger**: User sends first message after session gap (detected via Valkey cache expiry or explicit session boundary)

**Frequency**: ONCE per session - not on every message during active conversation

**Algorithm**:
```python
def restore_context(user_id):
    """
    Restore conversation context with hierarchical priority structure.

    Uses explicit section labels (PRIMARY/BACKGROUND/AVAILABLE) to help
    model attention focus on active conversation while maintaining
    awareness of background topics.
    """
    context = {
        'raw_messages': [],
        'primary_buckets': [],      # Full summaries, actively discussed
        'background_buckets': [],   # Full summaries, high-affinity topics
        'available_buckets': [],    # Metadata only, load on demand
        'self_healing_hints': []
    }

    # 1. Load last N raw messages (immediate continuity)
    # Uses topic_changed metadata for intelligent boundary detection
    context['raw_messages'] = get_raw_messages_for_context(user_id, target_count=15)

    # 2. Extract buckets mentioned in recent messages
    recent_bucket_ids = extract_bucket_ids(context['raw_messages'])

    # 3. PRIMARY CONTEXT: Load full summaries for active buckets
    for bucket_id in recent_bucket_ids:
        bucket = get_bucket(bucket_id)
        context['primary_buckets'].append({
            'id': bucket.id,
            'description': bucket.description,
            'summary': bucket.summary,  # Full summary
            'message_count': bucket.message_count,
            'last_updated': bucket.last_updated
        })

    # 4. BACKGROUND CONTEXT: Starred buckets get full summaries
    starred = get_starred_buckets(user_id, limit=3)
    for bucket in starred:
        if bucket.id not in recent_bucket_ids:
            context['background_buckets'].append({
                'id': bucket.id,
                'description': bucket.description,
                'summary': bucket.summary,  # Full summary for starred
                'message_count': bucket.message_count,
                'last_updated': bucket.last_updated
            })

    # 5. AVAILABLE CONTEXT: Other buckets as metadata only
    # These can be loaded on-demand via pin command
    other_buckets = get_other_active_buckets(
        user_id,
        exclude_ids=recent_bucket_ids + [b.id for b in starred],
        limit=5
    )
    for bucket in other_buckets:
        context['available_buckets'].append({
            'id': bucket.id,
            'description': bucket.description,
            # NO SUMMARY - just metadata to show what's available
            'message_count': bucket.message_count,
            'last_updated': bucket.last_updated
        })

    # 6. Check for pending self-healing hints
    hints = get_pending_bucket_hints(user_id)
    context['self_healing_hints'] = hints

    return context
```

---

### Raw Message Window Management

**Problem**: Blindly loading the last N messages can create jarring cuts mid-discussion, fragmenting conversational flow.

**Example of the Problem**:
```
Messages 1-185: [dropped]
Message 186 [a1b2c3d4]: "What about shedding?"
Message 187 [e5f6g7h8]: "Golden Retrievers shed heavily in spring and fall..."
Message 188 [i9j0k1l2]: "Thanks! Switching topics - can you help with my CSS bug?"
Message 189 [m3n4o5p6]: "Sure, what's the issue?"
...
Message 200 [q7r8s9t0]: "The div is still misaligned"
```

Loading messages 186-200 includes an incomplete dog discussion fragment mixed with the CSS discussion - confusing and disjointed.

**Solution**: Use retrospective `topic_changed` markers to identify clean boundaries.

**Why Retrospective Marking?**

Topic boundaries are clearer in hindsight. MIRA can recognize after a few exchanges that message 188 was the actual topic shift, then mark it retroactively:

```
Message 201 [u1v2w3x4]: "Try using flexbox instead"
<mira:topic_boundary_marker message_id="i9j0k1l2" />
```

System updates message `i9j0k1l2` metadata with `topic_changed=true`. Future context loading will trim from that message forward, preserving complete conversational flow.

**Implementation**:

```python
def get_raw_messages_for_context(user_id, target_count=15):
    """
    Load recent messages, preferring clean conversational boundaries.

    Uses topic_changed metadata to avoid cutting mid-discussion when possible.
    This is separate from bucket organization - it's about preserving flow
    coherence in the raw message window.
    """
    # Fetch more than needed to find boundaries
    candidates = get_last_n_messages(user_id, N=target_count + 10)

    # Find most recent topic boundary within range
    for i in range(len(candidates) - target_count, len(candidates)):
        if i >= 0 and candidates[i].metadata.get('topic_changed'):
            # Found a boundary - return from here forward
            return candidates[i:]

    # No boundary found in range, just return last N
    return candidates[-target_count:]
```

**With topic_changed markers**, the same scenario would load messages 188-200, providing a complete conversational unit about the CSS issue without the confusing dog discussion fragment.

**Message ID Format**:

Each message gets an 8-character UUID prefix for identification:

```python
import uuid

def create_message(user_id, role, content):
    return Message(
        id=str(uuid.uuid4())[:8],  # "a3f7b2c1"
        user_id=user_id,
        role=role,
        content=content,
        # ...
    )
```

**Message ID Visibility**:

MIRA must see message IDs to mark boundaries. Format in system context:

```
=== RECENT MESSAGES ===
[a1b2c3d4] User: What about shedding?
[e5f6g7h8] Assistant: Golden Retrievers shed heavily in spring and fall...
[i9j0k1l2] User: Thanks! Switching topics - can you help with my CSS bug?
[m3n4o5p6] Assistant: Sure, what's the issue?
```

**Retrospective Marking Logic**:

```python
def handle_topic_boundary_markers(user_id, boundary_markers):
    """
    Retroactively mark messages as topic boundaries.

    Args:
        user_id: Current user
        boundary_markers: List of message IDs to mark (e.g., ["i9j0k1l2"])
    """
    for message_id in boundary_markers:
        # Only allow marking within recent window (prevents arbitrary reach-back)
        target_msg = find_recent_message(
            user_id=user_id,
            message_id=message_id,
            window=25  # Last 25 messages
        )

        if target_msg:
            target_msg.metadata['topic_changed'] = True
            target_msg.save()
            logger.info(f"Marked message {message_id} as topic boundary")
        else:
            # Not found or outside window - log but don't fail request
            logger.warning(
                f"Cannot mark message {message_id}: "
                f"not found in recent window (user: {user_id})"
            )
```

**Key Points**:
- **Retrospective**: MIRA marks boundaries after recognizing them in hindsight
- **Windowed**: Can only mark messages in last 25 messages (prevents arbitrary historical edits)
- **Orthogonal to buckets**: Messages 188-200 might span multiple buckets, but form one conversational flow
- **Optional**: If no `topic_changed` markers exist, system falls back to simple last-N behavior
- **Low cost**: Metadata identified during natural conversation flow
- **Flow preservation**: Maintains conversational coherence in the raw message window
- **Non-blocking**: Failed marking (message not found) doesn't break the request

---

**Context Structure Philosophy**:

The context is organized in three tiers to minimize confusion:

1. **PRIMARY CONTEXT**: Full summaries for buckets actively being discussed (appeared in last 15 messages)
2. **BACKGROUND CONTEXT**: Full summaries for starred/high-affinity buckets (frequently returned to)
3. **AVAILABLE CONTEXT**: Metadata only - shows what topics exist without loading full content

This structure keeps the model's attention focused on active conversation while maintaining awareness of available topics for potential `pin` commands.

**Constants**:
- `N` (raw messages): 15
- Starred bucket limit: 3
- Available buckets limit: 5 (metadata only)

**System Prompt Formatting**:

The three-tier context structure gets formatted into the system prompt with explicit hierarchical labels:

```
=== PRIMARY CONTEXT - Active Topics ===
Focus on these topics when responding. These are currently being discussed.

Bucket: mira_development_001
Topic: Bucket-based conversation architecture
Summary: Designing self-organizing conversation system with bucket janitor,
         velocity detection for life changes, and self-healing assignments...
Messages: 47 | Last active: 2 min ago

Bucket: window_cleaning_001
Topic: Business operations and scheduling
Summary: Ongoing business discussions including customer management, pricing
         strategies, and operational logistics...
Messages: 89 | Last active: 5 min ago

=== BACKGROUND CONTEXT - High-Affinity Topics ===
Reference these topics if relevant to current discussion.

Bucket: annika_family_001
Topic: Family updates and plans
Summary: General family discussions, weekend plans, life updates...
Messages: 34 | Last active: 2 days ago

=== AVAILABLE TOPICS - Load on Demand ===
These topics are available but not loaded. Use bucket_search_tool or
pin command to load if needed.

- python_help_001: Code debugging and patterns (45 msgs, last: 1 week ago)
- web_dev_001: CSS/JS frontend issues (67 msgs, last: 3 days ago)
- cooking_recipes_001: Meal planning (12 msgs, last: 5 days ago)
- hardware_setup_001: Server and display config (19 msgs, last: 2 weeks ago)
- memory_testing_001: Memory system experiments (28 msgs, last: 1 week ago)
```

**Key Design Principles**:

1. **Explicit hierarchy**: Section headers make priority crystal clear
2. **Attention guidance**: "Focus on these" vs "Reference if relevant" vs "Available but not loaded"
3. **Reduce confusion**: Model sees active conversation topics first, background second
4. **Metadata awareness**: AVAILABLE section shows what exists without consuming tokens on summaries
5. **On-demand loading**: Model can request full context via `pin` command when needed

**During Active Conversation**:
Once context is restored at session start, it remains loaded for the entire session. Subsequent messages in the same continuum:
- Use the already-loaded context
- Append new user/assistant messages incrementally
- May have similarity hints injected temporarily (removed after processing)
- Do NOT trigger full context restoration

Context is only rebuilt when:
- User returns after session timeout (Valkey cache expired)
- Explicit session boundary detected
- User executes `pin` command to load additional bucket (adds to PRIMARY CONTEXT)

---

### 4. Bucket Search Tool

**Purpose**: Allow MIRA to search for and resurrect old conversation buckets on-demand.

**Tool Interface**:
```python
def bucket_search_tool(query: str, user_id: str) -> dict:
    """
    Search for buckets matching semantic query.

    Args:
        query: Natural language search query
        user_id: Current user

    Returns:
        {
            "buckets_found": [
                {
                    "id": "italy_trip_001",
                    "description": "Italy vacation planning",
                    "summary": "Discussed Rome, Florence, Venice...",
                    "message_count": 45,
                    "last_active": "2024-08-15",
                    "relevance_score": 0.94
                }
            ],
            "instruction": "To load, output: <mira:bucket>pin italy_trip_001</mira:bucket>"
        }
    """

    # Embed query
    query_embedding = embed_text(query)

    # Semantic search across all buckets
    results = vector_search(
        user_id=user_id,
        query_embedding=query_embedding,
        top_k=5,
        include_archived=True
    )

    return format_search_results(results)
```

**Pin Mechanism**:
When MIRA outputs `<mira:bucket>pin {bucket_id}</mira:bucket>`:
1. System reloads conversation context with that bucket
2. Optionally: Reload current turn so MIRA can respond with full context
3. Update bucket's `last_updated` and increment `return_frequency`

---

### 5. Velocity Detector

**Purpose**: Detect life transitions through message velocity anomalies.

**Execution**: Runs as part of Janitor cycle

**Scoring Algorithm**:
```python
def calculate_velocity_score(bucket):
    """
    Detect unusual message acceleration indicating life change.

    Returns score where > 15 with high conversation percentage
    suggests major life context shift.
    """
    age_in_days = (utc_now() - bucket.created_at).days
    if age_in_days == 0:
        age_in_days = 0.5  # Treat same-day as half-day

    # Messages per day
    velocity = bucket.message_count / age_in_days

    # What % of recent messages belong to this bucket?
    recent_messages = get_user_messages_since(
        bucket.user_id,
        since=utc_now() - timedelta(days=7)
    )

    bucket_messages_recent = count_messages_in_bucket(
        bucket.id,
        since=utc_now() - timedelta(days=7)
    )

    conversation_percentage = (
        bucket_messages_recent / len(recent_messages)
    )

    # Combined score
    velocity_score = velocity * conversation_percentage * 100

    return velocity_score, conversation_percentage
```

**Thresholds**:
- Life change: `velocity_score > 15` AND `conversation_percentage > 0.4`
- High growth: `velocity_score > 8`
- Normal: `velocity_score < 5`

**Actions on Detection**:
```python
if velocity_score > 15 and conversation_percentage > 0.4:
    bucket.metadata["life_change_detected"] = True
    bucket.priority = "high"

    # Create sub-bucket watch
    enable_sub_bucket_detection(bucket.id)

    # Accelerate archival of competing old topics
    old_buckets = find_semantically_similar_buckets(
        bucket.id,
        exclude_recent=True
    )
    for old_bucket in old_buckets:
        if old_bucket.last_updated > 14_days_ago:
            old_bucket.archive_priority = "high"
```

---

## Algorithms

### Similarity Matching

**Purpose**: Determine if two buckets represent the same topic.

**Implementation**:
```python
def calculate_bucket_similarity(bucket_a, bucket_b):
    """
    Calculate semantic similarity between two buckets.

    Returns similarity score [0.0, 1.0]
    """
    # Use bucket description + recent messages
    text_a = bucket_a.description + " " + " ".join(
        [m.content for m in bucket_a.get_recent_messages(5)]
    )

    text_b = bucket_b.description + " " + " ".join(
        [m.content for m in bucket_b.get_recent_messages(5)]
    )

    # Embed
    embedding_a = embed_text(text_a)
    embedding_b = embed_text(text_b)

    # Cosine similarity
    similarity = cosine_similarity(embedding_a, embedding_b)

    return similarity
```

**Thresholds**:
- `>= 0.90`: Definitely same topic, auto-consolidate
- `0.85 - 0.89`: Suggest to MIRA (self-healing)
- `< 0.85`: Distinct topics

---

### Context Loading Priority

**Purpose**: Decide which buckets to load on session start.

**Scoring**:
```python
def calculate_bucket_priority(bucket, recent_messages):
    """
    Score bucket for context loading priority.
    Higher score = more likely to load.
    """
    score = 0

    # Appears in recent messages? (highest weight)
    if bucket.id in extract_bucket_ids(recent_messages):
        score += 50

    # Starred bucket?
    if bucket.metadata.get("starred", False):
        score += 30

    # High return frequency?
    return_freq = bucket.metadata.get("return_frequency", 0)
    score += return_freq * 20

    # Recently active?
    days_since_update = (utc_now() - bucket.last_updated).days
    if days_since_update < 7:
        score += 20
    elif days_since_update < 30:
        score += 10

    # Life change detected?
    if bucket.metadata.get("life_change_detected", False):
        score += 25

    # Message count (indicates depth)
    score += min(bucket.message_count / 10, 10)

    return score

# Load top-scoring buckets
def select_buckets_for_context(user_id, recent_messages):
    all_buckets = get_active_buckets(user_id)

    scored = [
        (bucket, calculate_bucket_priority(bucket, recent_messages))
        for bucket in all_buckets
    ]

    # Sort by score, take top 5
    scored.sort(key=lambda x: x[1], reverse=True)
    return [bucket for bucket, score in scored[:5]]
```

---

## Data Structures

### Bucket Schema

```sql
CREATE TABLE conversation_buckets (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'active',  -- active, archived, ephemeral
    priority VARCHAR(50) DEFAULT 'normal',  -- high, normal, low
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INT DEFAULT 0,
    summary TEXT,
    metadata JSONB,  -- {starred, return_frequency, velocity_score, life_change_detected}
    embedding VECTOR(1536),  -- For similarity search

    INDEX idx_user_status (user_id, status),
    INDEX idx_last_updated (last_updated),
    INDEX idx_embedding USING ivfflat (embedding vector_cosine_ops)
);
```

### Message Schema Requirements

**Message ID Format**:

Messages must have an 8-character UUID prefix for retrospective topic boundary marking:

```python
# Example message creation
import uuid

message = Message(
    id=str(uuid.uuid4())[:8],  # "a3f7b2c1" - 8 char hex
    user_id=user_id,
    role=role,
    content=content,
    # ...
)
```

**Message Bucket Metadata**:

Added to existing `messages` table metadata JSON:
```json
{
  "bucket_id": "pets_advice_001",
  "new_bucket": false,
  "bucket_return": true,
  "bucket_commands": ["mv source_bucket dest_bucket"],
  "ephemeral": false,
  "topic_changed": true
}
```

**Notes**:
- `topic_changed` is independent of bucket assignment - used for raw message window trimming to preserve conversational flow
- Set retrospectively via `<mira:topic_boundary_marker message_id="..." />` tags
- Message IDs must be visible to MIRA in conversation context for retrospective marking

### Janitor State

```sql
CREATE TABLE janitor_state (
    user_id VARCHAR(255) PRIMARY KEY,
    last_run TIMESTAMP,
    buckets_consolidated INT DEFAULT 0,
    buckets_archived INT DEFAULT 0,
    life_changes_detected INT DEFAULT 0,
    next_run TIMESTAMP
);
```

---

## Integration with Existing System

### Changes to MIRA's System Prompt

**Remove**:
```
2. Evaluate topic continuity: If related (thematic, emotional, contextual
   continuity), include <mira:topic_changed=false />. If substantive shift
   in topic or context, write <mira:topic_changed=true />.
```

**Add**:
```
## Conversation Buckets

Messages are organized into topic buckets. When responding, assign to
appropriate bucket:

{DYNAMIC_HIERARCHICAL_CONTEXT}

**Assignment Rules:**
- Continuing current topic → use same bucket
- Returning to previous topic → use that bucket
- Genuinely new topic → create new bucket: `topic_name_XXX`
- One-off question unlikely to recur → use `ephemeral_XXX`

**In your <mira:analysis> block, add:**
<mira:bucket_id="existing_bucket_id" />
OR
<mira:bucket_id="new_topic_name_XXX" new_bucket="true" />

**Optionally, mark topic boundaries retrospectively:**

If looking back at recent messages (you can see their [message_id] prefixes), you
recognize a natural topic boundary, mark it:

<mira:topic_boundary_marker message_id="i9j0k1l2" />

You can mark multiple boundaries if several shifts occurred:

<mira:topic_boundary_marker message_id="i9j0k1l2" />
<mira:topic_boundary_marker message_id="q7r8s9t0" />

This helps preserve conversational flow when trimming the raw message window -
completely separate from bucket assignment. Only mark messages within the visible
conversation window (last ~20 messages).

**Self-Healing Hints:**
{DYNAMIC_SIMILARITY_HINTS}

If a hint suggests merging buckets and you agree, add:
<mira:bucket>mv source_bucket dest_bucket</mira:bucket>

Focus on conversation, not bucket management. System handles optimization.
```

**Dynamic Hierarchical Context Format**:
```
=== PRIMARY CONTEXT - Active Topics ===
Focus on these topics when responding. These are currently being discussed.

Bucket: mira_development_001
Topic: Bucket-based conversation architecture
Summary: [full summary text]
Messages: 47 | Last active: 2 min ago

=== BACKGROUND CONTEXT - High-Affinity Topics ===
Reference these topics if relevant to current discussion.

Bucket: window_cleaning_001
Topic: Business operations
Summary: [full summary text]
Messages: 89 | Last active: 1 day ago

=== AVAILABLE TOPICS - Load on Demand ===
These topics are available but not loaded. Use pin command if needed.

- python_help_001: Code debugging (45 msgs, last: 1 week ago)
- web_dev_001: Frontend issues (67 msgs, last: 3 days ago)
- cooking_recipes_001: Meal planning (12 msgs, last: 5 days ago)
```

**Rationale for Hierarchical Structure**:

This three-tier approach prevents model confusion when many buckets exist:

1. **PRIMARY CONTEXT** gets full summaries and explicit "focus on these" instruction
2. **BACKGROUND CONTEXT** gets full summaries but "reference if relevant" instruction
3. **AVAILABLE TOPICS** shows metadata only - no summaries loaded until requested

This structure:
- Directs model attention to active conversation
- Reduces token usage on inactive topics
- Maintains awareness of available context
- Enables on-demand loading via `pin` command

---

### Changes to Orchestrator

**File**: `cns/services/orchestrator.py`

**Current (lines 363-364)**:
```python
if parsed_tags['topic_changed']:
    unit_of_work.mark_metadata_updated()
```

**Replace with**:
```python
# Extract bucket assignment and flow markers
bucket_id = parsed_tags.get('bucket_id')
new_bucket = parsed_tags.get('new_bucket', False)
bucket_commands = parsed_tags.get('bucket_commands', [])
topic_boundary_markers = parsed_tags.get('topic_boundary_markers', [])

if bucket_id:
    # Update message metadata
    assistant_msg_obj.metadata['bucket_id'] = bucket_id
    assistant_msg_obj.metadata['new_bucket'] = new_bucket

# Handle retrospective topic boundary marking (independent of buckets)
if topic_boundary_markers:
    for message_id in topic_boundary_markers:
        # Find message in recent window (last 25 messages)
        target_msg = repository.find_recent_message(
            user_id=user_id,
            message_id=message_id,
            window=25
        )

        if target_msg:
            target_msg.metadata['topic_changed'] = True
            repository.update_message(target_msg)
            logger.info(f"Marked message {message_id} as topic boundary")
        else:
            # Not found or outside window - log warning but don't fail
            logger.warning(
                f"Cannot mark message {message_id}: "
                f"not found in recent window (user: {user_id})"
            )

if bucket_id:
    # Handle bucket commands (mv, pin, merge, split)
    if bucket_commands:
        for command in bucket_commands:
            bucket_manager.execute_command(command, user_id)

    # Update bucket state
    bucket_manager.add_message_to_bucket(bucket_id, assistant_msg_obj, user_id)

    # Check if new bucket needs similarity check
    if new_bucket:
        similar_buckets = bucket_manager.find_similar_buckets(bucket_id, user_id)
        if similar_buckets:
            # Queue hint for next conversation turn
            bucket_manager.queue_similarity_hint(bucket_id, similar_buckets, user_id)

    # Check if bucket needs summary
    bucket = bucket_manager.get_bucket(bucket_id, user_id)
    if bucket.should_summarize():
        summary_generator.update_bucket_summary(bucket, user_id)
```

---

### Changes to Tag Parser

**File**: `cns/services/tag_parser.py`

**Add Method**:
```python
def extract_topic_boundary_markers(self, response_text: str) -> List[str]:
    """
    Extract retrospective topic boundary markers from response.

    Parses tags like:
    <mira:topic_boundary_marker message_id="i9j0k1l2" />

    Returns:
        List of message IDs to mark as boundaries (e.g., ["i9j0k1l2", "q7r8s9t0"])
    """
    pattern = r'<mira:topic_boundary_marker\s+message_id="([a-f0-9]{8})"\s*/>'
    matches = re.findall(pattern, response_text)
    return matches
```

---

### Changes to Summary Generator

**File**: `cns/services/summary_generator.py`

**Add Method**:
```python
def update_bucket_summary(self, bucket: Bucket, user_id: str) -> None:
    """
    Generate or update bucket summary.

    For existing summaries: (existing_summary + new_messages) = updated_summary
    For new summaries: Generate from all bucket messages
    """
    messages = self.repository.get_bucket_messages(bucket.id, user_id)

    if bucket.summary:
        # Incremental update
        new_messages = [m for m in messages if m.created_at > bucket.last_summarized]

        prompt = f"""
        Existing summary: {bucket.summary}

        New messages:
        {self._format_messages_for_llm(new_messages)}

        Update the summary to incorporate new information while maintaining
        brevity. Focus on key decisions, outcomes, and context.
        """
    else:
        # First summary
        prompt = f"""
        Summarize this conversation thread:
        {self._format_messages_for_llm(messages)}

        Capture key points, decisions, and context. Be concise.
        """

    updated_summary = self.llm_provider.generate_response(...)

    bucket.summary = updated_summary
    bucket.last_summarized = utc_now()
    bucket.save()
```

---

### New Component: BucketManager

**File**: `cns/services/bucket_manager.py` (new file)

```python
class BucketManager:
    """Manages bucket lifecycle and operations."""

    def __init__(self, repository, vector_store):
        self.repository = repository
        self.vector_store = vector_store

    def get_or_create_bucket(self, bucket_id: str, user_id: str,
                            description: str = None) -> Bucket:
        """Get existing bucket or create new one."""
        ...

    def add_message_to_bucket(self, bucket_id: str, message: Message,
                             user_id: str) -> None:
        """Add message to bucket, update counts and timestamps."""
        ...

    def find_similar_buckets(self, bucket_id: str, user_id: str,
                            threshold: float = 0.85) -> List[Tuple[str, float]]:
        """Find buckets similar to given bucket."""
        ...

    def execute_command(self, command: str, user_id: str) -> bool:
        """Execute bucket command (mv, pin, merge, split)."""
        ...

    def get_active_buckets_for_context(self, user_id: str,
                                      recent_messages: List[Message]) -> List[Bucket]:
        """Get buckets to load into conversation context."""
        ...
```

---

## Operational Flows

### Flow 1: New Message Processing

```
SESSION START:
1. User returns to conversation after gap

2. Context Restoration (ONE TIME):
   - Load last 15 messages
   - Load active bucket summaries: [mira_dev_001, window_cleaning_001]
   - Check for pending similarity hints: None
   - Context now loaded for entire session

CONVERSATION TURN 1:
3. User sends: "Should I get a Golden Retriever?"

4. MIRA processes (uses pre-loaded context):
   - Sees active buckets: [mira_dev_001, window_cleaning_001]
   - Determines this is new topic
   - Creates: <mira:bucket_id="dog_breeds_001" new_bucket="true" />
   - Responds: "Golden Retrievers are great family dogs..."

5. Post-processing:
   - System creates bucket "dog_breeds_001"
   - Embeds bucket description
   - Finds similar buckets: ["pets_advice_001" (0.91)]
   - Queues similarity hint for next turn
   - Adds user/assistant messages to conversation context

CONVERSATION TURN 2 (same session):
6. User sends: "What about shedding?"

7. MIRA processes (context already loaded, just updated):
   - Context includes previous exchange + similarity hint injected:
     "Note: 'dog_breeds_001' is 91% similar to 'pets_advice_001'.
      If same topic: <mira:bucket>mv dog_breeds_001 pets_advice_001</mira:bucket>"
   - Sees hint, agrees they're same topic
   - Outputs: <mira:bucket>mv dog_breeds_001 pets_advice_001</mira:bucket>
   - Outputs: <mira:bucket_id="pets_advice_001" />
   - Responds: "Golden Retrievers shed quite a bit..."

8. Post-processing:
   - Execute mv command (merge buckets)
   - Update pets_advice_001 message count
   - Delete dog_breeds_001
   - Clear similarity hint from context
   - Add messages to conversation

[Conversation continues with same loaded context, no re-fetching]
```

---

### Flow 2: Context Restoration After Vacation

```
SCENARIO: User last active 7 days ago (vacation with no internet)

SESSION START:
1. User sends: "Hey, I'm back"

2. Context Restoration algorithm triggers (detecting session start):
   - Load last 15 messages (all from 7+ days ago)
   - Extract bucket IDs from those messages:
     - "mira_dev_001" (discussing buckets)
     - "vacation_planning_001" (trip logistics)

   - Load those bucket summaries:
     - mira_dev_001: "Designed bucket-based conversation system..."
     - vacation_planning_001: "Planned trip to Austin, booked hotel..."

   - Load starred buckets (not in recent messages):
     - window_cleaning_001: [Business operations]
     - annika_family_001: [Family matters]

   - Context now loaded for session

3. MIRA processes first message (using restored context):
   - Sees last conversation was about MIRA architecture
   - Sees vacation was planned but no indication it happened yet
   - Responds naturally: "Welcome back! How was Austin?"

4. Conversation continues with this loaded context
   - No re-fetching on subsequent messages in same session
   - No time-based assumptions needed
   - System loaded relevant context from conversation footprint

KEY INSIGHT: 7-day gap handled identically to 1-hour gap. Context restoration
is footprint-based, not time-based.
```

---

### Flow 3: Life Transition Detection

```
Day 1: User starts new job at windmill company
  - MIRA creates "windmill_tech_001"
  - 5 messages
  - Janitor sees: velocity = 5/1 = 5 (normal)

Day 3:
  - windmill_tech_001 now has 25 messages
  - Janitor calculates:
    - velocity = 25/3 = 8.3 messages/day
    - conversation_percentage = 25/30 = 83%
    - velocity_score = 8.3 * 0.83 * 100 = 689 (HIGH!)
  - Flags as potential life change
  - Promotes to high priority
  - Enables sub-bucket detection

Day 5:
  - windmill_tech_001 now has 100 messages
  - Several sub-buckets detected:
    - windmill_safety_001
    - windmill_maintenance_001
    - windmill_turbine_types_001
  - Janitor confirms life change
  - Accelerates archival of old "window_cleaning_001" bucket
    (last_updated > 14 days, competing semantic space)

Day 30:
  - window_cleaning_001 archived (gracefully)
  - windmill_tech_001 is now core bucket with sub-hierarchy
  - System has naturally reorganized around new life context
```

---

### Flow 4: Retrospective Topic Boundary Marking

```
SESSION IN PROGRESS (conversation already loaded):

Message 185 [a1b2c3d4]: "What about shedding?"
Message 186 [e5f6g7h8]: "Golden Retrievers shed heavily..."
Message 187 [i9j0k1l2]: "Thanks! Now about my CSS - the div won't align"
Message 188 [m3n4o5p6]: "Let me help with that..."
Message 189 [q7r8s9t0]: "Try using flexbox"
Message 190 [u1v2w3x4]: "Still not working"

At this point, MIRA recognizes message 187 was a clear topic shift (dog → CSS).

MIRA's response (Message 191):
  <mira:analysis>
    <mira:bucket_id="web_dev_001" />
    <mira:topic_boundary_marker message_id="i9j0k1l2" />
  </mira:analysis>

  "Let me check the flexbox syntax. Can you share your CSS?"

System processing:
1. Assigns message 191 to web_dev_001 bucket
2. Finds message i9j0k1l2 in recent window
3. Updates message 187 metadata: topic_changed=true
4. Logs: "Marked message i9j0k1l2 as topic boundary"

FUTURE SESSION (user returns days later):
  Context restoration calls get_raw_messages_for_context(user_id, target_count=15)

  Candidates: messages 177-191 (15 messages)
  Scans for topic_changed markers
  Finds: message 187 [i9j0k1l2] has topic_changed=true

  Returns: messages 187-191 (5 messages, all CSS discussion)

  Result: Clean conversational flow about CSS bug, no dog discussion fragments
```

**Key Insight**: Retrospective marking allows MIRA to identify boundaries after seeing how the conversation evolved, providing more accurate cuts than real-time prediction.

---

### Flow 5: Resurrecting Old Topic

```
1. User: "What was that restaurant in Rome we talked about?"

2. MIRA recognizes missing context:
   - No "rome" or "restaurant" bucket in active set
   - Uses bucket_search_tool("restaurant recommendations Rome")

3. Tool returns:
   {
     "buckets_found": [
       {
         "id": "italy_trip_001",
         "description": "Italy vacation planning",
         "summary": "Discussed Rome, Florence, Venice restaurants...",
         "relevance_score": 0.94
       }
     ],
     "instruction": "To load: <mira:bucket>pin italy_trip_001</mira:bucket>"
   }

4. MIRA outputs:
   <mira:bucket>pin italy_trip_001</mira:bucket>
   "Let me pull up our Italy discussion..."

5. System reloads conversation with italy_trip_001 context

6. (Optional) System regenerates MIRA's response with full context:
   "Yes! I recommended Checchino dal 1887 in Testaccio for
    authentic Roman cuisine. You were planning to visit during
    your September trip."

7. italy_trip_001 bucket stats updated:
   - last_updated = now
   - return_frequency += 1
   - If return_frequency increases, may become starred
```

---

## Edge Cases and Solutions

### Edge Case 1: Ambiguous Topic Assignment

**Scenario**: User discusses "training my dog" - could be bucket "pets_advice_001" or new bucket "dog_training_001"

**Solution**:
- MIRA makes best guess (fast, imperfect)
- If creates new bucket, similarity check triggers
- Self-healing suggestion appears next turn
- MIRA corrects if needed
- Maximum 2-turn correction cycle

### Edge Case 2: Cross-Bucket Topics

**Scenario**: User discusses "using MIRA for window cleaning scheduling" - spans two buckets

**Solution**:
- MIRA picks primary bucket (e.g., "mira_dev_001")
- Message metadata can include: `"related_buckets": ["window_cleaning_001"]`
- Both buckets' summaries can reference the discussion
- Future enhancement: Multi-bucket assignment

### Edge Case 3: Bucket Naming Conflicts

**Scenario**: User creates "python_help_001" then later "python_tips_001"

**Solution**:
- Janitor's similarity check will catch (likely > 0.90)
- Auto-consolidate during next janitor run
- If distinct topics, embeddings will reflect difference
- MIRA's naming doesn't need to be perfect - system corrects

### Edge Case 4: Ephemeral Bucket Promotion

**Scenario**: User asks random question, gets detailed multi-turn answer

**Solution**:
- Initial: ephemeral_001
- After 5+ messages: Janitor promotes to real bucket
- Generates proper descriptive bucket_id
- Preserves all messages during promotion

### Edge Case 5: Return After 6 Months

**Scenario**: User doesn't use MIRA for half a year, returns

**Solution**:
- Load last 15 messages (all 6+ months old)
- Extract bucket IDs from those messages
- Load those bucket summaries
- Starred buckets still appear
- System works identically - no time assumptions
- If user mentions new topic, new bucket created
- If user references old topic, bucket search tool works

---

## Implementation Phases

### Phase 1: Core Infrastructure (Week 1-2)

**Goals**:
- Bucket database schema
- BucketManager service
- Basic bucket assignment in orchestrator
- Update MIRA's system prompt
- **Message ID format** (8-char UUID prefix)
- **Message ID visibility in context** (prefixed format)

**Deliverables**:
- Bucket CRUD operations working
- Messages assigned to buckets with 8-char UUIDs
- Message IDs visible in conversation context
- No janitor yet (manual testing)

**Testing**:
- Create conversation with multiple topics
- Verify buckets created correctly
- Verify messages associated with buckets
- Verify message IDs displayed in context

---

### Phase 2: Self-Healing (Week 3)

**Goals**:
- Vector similarity matching
- Similarity hint injection
- Bucket command parsing (mv, pin, merge)
- Command execution

**Deliverables**:
- Self-healing corrections working
- MIRA can merge duplicate buckets
- Maximum 2-turn correction verified

**Testing**:
- Create intentionally similar buckets
- Verify hints appear
- Verify MIRA executes mv commands
- Verify buckets merge correctly

---

### Phase 3: Context Restoration (Week 4)

**Goals**:
- Context restoration algorithm
- Active bucket detection
- Starred bucket system
- Dynamic bucket list in system prompt
- **Raw message window trimming** with topic_changed markers
- **Retrospective boundary marking** tag parsing and handling

**Deliverables**:
- Login loads appropriate context
- Works after time gaps
- Bounded context size
- Message window respects topic boundaries
- MIRA can mark boundaries retrospectively

**Testing**:
- Test after various time gaps (1 hour, 1 day, 1 week)
- Verify correct buckets load
- Verify context size bounded
- Create conversation with topic shifts, verify boundary marking works
- Verify context trimming respects marked boundaries

---

### Phase 4: Bucket Janitor (Week 5-6)

**Goals**:
- Async janitor process
- Consolidation algorithm
- Archival rules
- Velocity detection
- Ephemeral cleanup

**Deliverables**:
- Janitor runs on schedule
- Buckets consolidate automatically
- Life changes detected
- System self-optimizes

**Testing**:
- Simulate 6 weeks of conversations
- Verify bucket count stays bounded
- Verify similar buckets merge
- Simulate life transition (velocity spike)

---

### Phase 5: Bucket Search Tool (Week 7)

**Goals**:
- Bucket search tool implementation
- Pin mechanism
- On-demand context loading
- Optional response regeneration

**Deliverables**:
- MIRA can search old buckets
- Pin loads context successfully
- Old topics resurrectable

**Testing**:
- Create old bucket, archive it
- Ask MIRA to recall information
- Verify search works
- Verify pin loads context

---

### Phase 6: Migration & Polish (Week 8)

**Goals**:
- Migrate existing summaries to buckets
- Remove topic_changed system
- Performance optimization
- Monitoring dashboards

**Deliverables**:
- Production cutover complete
- Old system deprecated
- Monitoring in place

---

## Performance Considerations

### Vector Similarity Search
- Use approximate nearest neighbors (ANN) index
- Update embeddings async (not in request path)
- Cache similarity calculations

### Context Restoration
- Target: < 200ms to build context
- Optimize bucket query with proper indexes
- Consider bucket metadata cache (Redis)

### Janitor Execution
- Run during low-traffic periods
- Process users in batches
- Timeout protection per user
- Skip users with no recent activity

### Database Queries
```sql
-- Index for active bucket retrieval
CREATE INDEX idx_user_active_buckets
ON conversation_buckets(user_id, status, last_updated);

-- Index for similarity search
CREATE INDEX idx_bucket_embeddings
ON conversation_buckets USING ivfflat (embedding vector_cosine_ops);

-- Index for message bucket assignment
CREATE INDEX idx_message_bucket
ON messages((metadata->>'bucket_id'));
```

---

## Monitoring Metrics

Track these metrics in production:

### Bucket Health
- Active bucket count per user (target: 10-20 after 6 weeks)
- Ephemeral bucket creation rate
- Bucket consolidation rate
- Archive rate

### Self-Healing
- Similarity hint generation rate
- Hint acceptance rate (MIRA agrees and merges)
- Hint rejection rate (MIRA ignores)
- Average turns to correction

### Velocity Detection
- Life change detections per month
- False positive rate
- Time to detection (days)

### Performance
- Context restoration latency (p50, p95, p99)
- Janitor execution time per user
- Vector search latency

### User Experience
- Bucket search tool usage
- Pin command frequency
- Return to old topics (indicates successful resurrection)

---

## Open Implementation Questions

1. **Bucket Naming**: Should bucket IDs be human-readable (`dog_advice`) or UUID-based? Current design uses descriptive names with numeric suffixes.

2. **Summary Format**: Telegraphic bullets vs narrative summaries? ADR leaves open.

3. **Multi-Bucket Assignment**: Should messages ever belong to multiple buckets? Current design: no, but `related_buckets` metadata could support future expansion.

4. **Janitor Frequency**: 6 hours proposed, but could be tuned based on usage patterns.

5. **Similarity Thresholds**: 0.85 suggestion, 0.90 auto-consolidate - may need tuning in production.

6. **Sub-Bucket Hierarchy**: How deep should bucket nesting go? Current design: flat with optional grouping in metadata.

---

## Success Metrics

The bucket system succeeds if:

1. **Bounded Growth**: Active bucket count per user stays between 10-20 after 6 weeks
2. **Coherent Threads**: Users can reference "that X discussion" and get complete context
3. **Self-Organizing**: < 5% of buckets need manual intervention
4. **Time-Independent**: System functions identically after 1 day or 60 days
5. **Life Adaptability**: Velocity detector catches >80% of major life transitions within 7 days
6. **Fast Correction**: Self-healing resolves misassignments in <2 turns 90% of time

---

## Experimental Approaches

**See**: `EXPERIMENTAL_opacity_context_relevance.md` for a low-probability experiment using CSS opacity tags to signal relevance to the model's attention mechanism. Estimated 5-10% chance of success, but zero cost to test.

---

## Appendices

### Appendix A: Tag Grammar

**Bucket Commands**:
```
<mira:bucket>COMMAND args</mira:bucket>

Commands:
  mv SOURCE DEST           - Move all messages from SOURCE to DEST, delete SOURCE
  merge A B NEW_NAME       - Combine A and B into NEW_NAME
  split SOURCE DEST1 DEST2 - Reassign messages from SOURCE
  pin BUCKET_ID            - Load BUCKET_ID into context
```

**Topic Boundary Markers** (Retrospective):
```
<mira:topic_boundary_marker message_id="a3f7b2c1" />

- message_id: 8-character hex UUID of message to mark
- Can include multiple markers in single response
- Only effective for messages in recent window (last ~25 messages)
- Non-blocking: invalid IDs logged but don't fail request
```

**Bucket Assignment**:
```
<mira:bucket_id="existing_bucket_001" />
<mira:bucket_id="new_topic_001" new_bucket="true" />
```

### Appendix B: Example Bucket Evolution

```
Week 1:
- mira_dev_001 (10 msgs)
- window_cleaning_001 (25 msgs)
- weather_001 (3 msgs, ephemeral)
- random_questions_001 (2 msgs, ephemeral)

Week 2:
- mira_dev_001 (45 msgs) ⭐
- window_cleaning_001 (48 msgs) ⭐
- python_help_001 (12 msgs)
- css_bugs_001 (8 msgs)
[Ephemeral buckets cleaned daily]

Week 4:
- mira_dev_001 (120 msgs) ⭐
- window_cleaning_001 (89 msgs) ⭐
- python_help_001 (34 msgs)
- web_dev_001 (22 msgs)  [janitor merged css_bugs + js_errors]
- annika_family_001 (15 msgs)

Week 6:
- mira_dev_001 (250 msgs) ⭐
- window_cleaning_001 (145 msgs) ⭐
- web_dev_001 (67 msgs)
- python_help_001 (45 msgs)
- annika_family_001 (34 msgs)
- memory_experiments_001 (28 msgs)
- hardware_setup_001 (19 msgs)
[~12 active buckets, system stable]
```

### Appendix C: Migration from topic_changed

**Step 1**: Map existing summaries to buckets
```python
for summary in existing_summaries:
    # Extract topic from summary content
    topic = extract_topic_from_summary(summary)

    # Find or create bucket
    bucket = find_or_create_bucket(topic, user_id)

    # Associate messages with bucket
    for message_id in summary.source_message_ids:
        update_message_metadata(message_id, {"bucket_id": bucket.id})

    # Store summary in bucket
    bucket.summary = summary.content
```

**Step 2**: Enable parallel running
- New messages use buckets
- Keep topic_changed for monitoring
- Compare summary quality

**Step 3**: Full cutover
- Remove topic_changed from prompt
- Delete topic_changed parsing
- Archive old summaries

---

## Document Maintenance

This blueprint should be updated when:
- Thresholds change (similarity, velocity, etc.)
- New bucket commands added
- Algorithm modifications
- Performance optimization changes
- Edge cases discovered in production

**Version History**:
- 1.0 (2025-09-29): Initial version from pair programming session