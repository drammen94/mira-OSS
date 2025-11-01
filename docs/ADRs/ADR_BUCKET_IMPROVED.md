Architecture Decision Record: Bucket-Based Conversation Organization

Status: Approved for Implementation
Date: 2025-10-12
Decision Makers: Taylor (Three Pixel Drift)
Supersedes: Using topic_changed for topic organization and summarization
Retains: topic_changed as optional metadata for raw message window trimming
Context
The Problem with Binary Topic Detection

The current system uses a binary topic_changed flag to create conversation summaries. This has fundamental flaws:

	Real conversations aren't linear - users circle back to topics after tangents
	Binary classification is too sensitive - minor shifts trigger new summaries
	No completion awareness - can't distinguish one-off questions from substantive discussions
	Fragmented context - returning to a topic after interruption creates duplicate summaries
	Time-dependent assumptions - breaks during vacations or irregular usage

Production evidence:

	Conversations with 100+ messages generated 40+ tiny summaries
	Topics discussed across 5-8 exchanges split into 3-4 separate summaries
	No way to retrieve "all conversations about X" as coherent thread

Separate Concerns: Organization vs Flow Preservation

	Buckets organize messages into topics over weeks/months for summary loading
	topic_changed marks natural boundaries for raw message window trimming

These solve different problems. Buckets replace topic_changed for topic organization, but the flag remains useful for context window management.

Retrospective marking: Topic boundaries are clearer in hindsight. Rather than real-time prediction, MIRA can retroactively mark boundaries after a few exchanges: ``. This provides more accurate detection while maintaining benefits for window trimming.
Decision

Implement a bucket-based conversation organization system where:

	Messages are assigned to persistent topic buckets
	Buckets accumulate context across conversation gaps
	Summaries are bucket-scoped and update incrementally
	Context loading is bucket-aware rather than time-based
	An async janitor maintains bucket health out-of-band
	MIRA manages buckets with minimal cognitive overhead

Core Architecture
Bucket Assignment (Real-Time)

MIRA's heuristic: "Will we discuss this topic again in future conversations?"

	Yes → Create/assign to real bucket (e.g., arduino_projects, mira_architecture)
	No → Use ephemeral bucket (e.g., ephemeral_001) for one-off questions

Why MIRA doesn't see all buckets:
Keeping exhaustive bucket lists in context is expensive and distracting. MIRA makes fast, good-enough decisions with partial information. Janitor reconciles later when it can see everything.

Bucket syntax in analysis block:
```

xml


### Hierarchical Context Loading

**Why hierarchy matters**: Loading many bucket summaries risks model confusion - attention drifts to irrelevant topics.

**PRIMARY CONTEXT**: Full summaries for buckets in last N messages
- "Focus on these topics when responding"
- Active conversation scaffolding

**BACKGROUND CONTEXT**: Full summaries for starred/pinned buckets
- "Reference if relevant to current discussion"
- Maintains awareness without demanding focus

**AVAILABLE TOPICS**: Metadata only (ID, last_updated, message_count)
- Shows what exists without consuming tokens
- Can be loaded on-demand via tool

### Session Startup Audit Receipt

At session start, display janitor activity since last session:

NOTIFICATION: LAST SESSION ENDED AT 13:24:51
JANITOR ACTIVITY SINCE LAST SESSION:

	Merged 'cpp_homework_helpers' into 'programming_help' (98% similar, 8 messages)
	Archived 'temp_arduino_question' (ephemeral, 24+ hours old)
	Promoted 'mira_architecture' to active (15 messages in 3 days)


**Design constraints:**
- One sentence justification per action (keeps 8B model honest)
- Max 5 items shown (if >5, summarize: "Consolidated 12 buckets - see full log")
- Zero cognitive load on MIRA (just information, no action required)
- Allows catching janitor mistakes before they pollute summaries

---

## Janitor Operations (Async)

**Purpose**: Out-of-band maintenance with zero cognitive load during conversation

**Execution frequency**: Every 6 hours (configurable)

### A. Consolidation

```python
For each bucket B:
  similar_buckets = vector_search(B, threshold=0.90)
  
  For each S in similar_buckets:
	if S.priority >= B.priority:
	  merge_buckets(source=B, dest=S)
	  log_action(f"Merged {B.id} into {S.id} - {justification}")

Threshold: 0.90 (90% semantic similarity)
B. Archival

# Ephemeral cleanup
if bucket.status == "ephemeral" and bucket.age > 24_hours:
  archive_to_daily_summary(bucket, date)
  delete(bucket)

# Stale bucket archival
if bucket.status == "active" and bucket.last_updated > 30_days and bucket.message_count < 3:
  bucket.status = "archived"
  compress_to_summary_only(bucket)

# Low-return archival
if bucket.status == "active" and bucket.last_updated > 90_days:
  bucket.status = "archived"
  # Still accessible, just not in active set

C. Ephemeral Promotion

if bucket.status == "ephemeral" and bucket.message_count > 5:
  promote_to_active(bucket)
  log_action(f"Promoted {bucket.id} to active - unexpected depth")

Why: Handles "trivial question became important discussion" case
D. Priority Boosting (Simple Version)

# High activity = higher priority, nothing else
for bucket in created_last_7_days:
  if bucket.messages_per_day > 10:
	bucket.priority = "high"

What we're NOT doing: Velocity detection that auto-demotes other buckets or infers "life changes." Just promote active buckets, leave everything else alone.
Bucket Management Tools

MIRA needs visibility into bucket state for informed decisions:

bucket_status tool: Shows current active buckets, sizes, and suggested merges

ACTIVE BUCKETS (12):
- arduino_projects: 47 messages, last updated 2h ago
- mira_architecture: 156 messages, last updated 15m ago
- cpp_homework: 8 messages, last updated 3d ago

JANITOR SUGGESTIONS:
- Merge 'cpp_homework' into 'programming_help'? (similarity: 0.91)
- Archive 'temp_lighting_question'? (ephemeral, 2d old)

Manual commands (via tool or special syntax):

	pin <bucket_id> - Load bucket into BACKGROUND context
	unpin <bucket_id> - Remove from background
	archive <bucket_id> - Force archival
	show <bucket_id> - Display full bucket summary

Time-Independent Context Loading

The problem: Wall clock time is meaningless for conversation continuity

	User on vacation for a week → time gap is irrelevant
	Irregular usage patterns → time-based rules break

Context restoration uses conversation footprint:

def load_context():
  # Immediate continuity
  raw_messages = get_last_n_messages(n=15)
  
  # Detect active buckets
  active_bucket_ids = extract_buckets_from(raw_messages)
  primary_summaries = load_summaries(active_bucket_ids)
  
  # Add pinned/starred buckets
  background_summaries = load_summaries(user.pinned_buckets)
  
  # Metadata for everything else
  available_metadata = list_all_buckets(metadata_only=True)
  
  return {
	"raw": raw_messages,
	"primary": primary_summaries,
	"background": background_summaries,
	"available": available_metadata
  }

Benefits:

	Works identically after 1 day or 6 months
	Vacation-proof
	Bounded context size regardless of gaps

What We're NOT Building (Yet)

These are deferred until proven necessary:

	Automated life transition detection - Complex heuristics, high false positive risk
	Self-healing injection during conversation - Adds cognitive load we're trying to avoid
	Cross-bucket message references - Adds complexity, unclear if needed
	Automatic topic demotion - Risk of incorrectly archiving active projects

Philosophy: Build the minimal system. Add complexity only when pain points emerge in production.
Migration Strategy
Phase 1: Parallel Running (Weeks 1-2)

	Keep existing topic_changed system
	Add bucket assignment alongside
	Janitor operates on new buckets only
	Compare summary quality

Phase 2: Bucket-First (Weeks 3-4)

	New conversations use buckets exclusively
	Map existing summaries to buckets (best effort)
	Deprecate topic_changed for organization

Phase 3: Full Migration (Week 5+)

	Remove old system
	Janitor manages all conversations
	Monitor bucket health metrics

Success Criteria

The system succeeds when:

	Active bucket count stays bounded (10-20) after 6 weeks
	Users can reference "that dog discussion" and system retrieves complete thread
	No manual bucket management required for normal use
	System handles vacation gaps without degradation
	Janitor consolidations are >90% correct (based on audit receipts)
	Context loading time remains <500ms

Monitoring

Track these metrics:

	Bucket proliferation rate (new buckets/day)
	Janitor consolidation accuracy (manual reversals as proxy)
	Ephemeral promotion frequency
	Context loading latency
	User-initiated bucket commands (high rate = UI/UX problem)

Open Questions for Implementation

	Optimal N for raw message window (15? 20? 25?)
	Bucket summary format (telegraphic bullets? narrative paragraph?)
	Vector embedding model for similarity detection
	Janitor failure handling (bad merge, rollback mechanism?)
	Bucket naming conventions (user-friendly vs systematic IDs?)

Implementation Note: Segment Collapse Bucket Assignment

**Critical Design Requirement**: Collapsed segment sentinels must carry bucket assignment metadata for context loading to work correctly.

**Problem**: The bucket-based context loader relies on identifying "recently active buckets" from the last N messages in the raw message window. When segments collapse, those messages are replaced by sentinel summaries. Without bucket metadata in the sentinels, the system loses visibility into which topics were recently discussed.

**Solution**: Assign buckets during segment collapse, not per-message.

**Implementation Approach**:

1. **During Segment Collapse** (`cns/services/segment_collapse_handler.py`):
   - Fetch user's existing active buckets from database (bucket_id, name, last_updated, message_count)
   - Pass bucket list to summary generator alongside conversation context

2. **Extend Summary Generation** (`cns/services/summary_generator.py`):
   - Add `existing_buckets` parameter to `generate_summary()`
   - Include bucket assignment instructions in segment summary prompts
   - LLM output format: synopsis + display_title + **bucket_assignment tag**
   - Return tuple: `(Message, Optional[List[str]])` where second element is assigned bucket IDs

3. **Prompt Additions** (`config/prompts/segment_summary_*.txt`):
   - Provide existing buckets list in context
   - Instruct LLM: "Assign this segment to the most relevant existing bucket(s), or create a new bucket ID if none fit well"
   - Output tag: `<mira:bucket_assignment>bucket_id1,bucket_id2</mira:bucket_assignment>`
   - Guidance on ephemeral vs persistent bucket decisions

4. **Tag Parser Extension** (`utils/tag_parser.py`):
   - Add `BUCKET_ASSIGNMENT_PATTERN` regex
   - Extract bucket IDs from LLM response during tag parsing

5. **Store in Sentinel Metadata**:
   - Collapsed sentinel stores: `metadata['buckets_discussed'] = ['bucket_id1', 'bucket_id2']`
   - Context loader reads this field from sentinels in raw message window
   - Enables "load full summaries for buckets in last N messages" logic

**Why This Design**:
- Segment-level assignment is semantically correct: segments are atomic conversation units
- LLM already has full context during summarization - no extra inference cost
- Holistic judgment: LLM sees entire segment, not just individual message
- Efficient: one bucket decision per segment, not per message
- Aligns with segment-as-organizational-unit architecture

References

	Current system: cns/services/orchestrator.py
	Summary generation: cns/services/summary_generator.py
	Tag parsing: cns/services/tag_parser.py
