# LT_Memory: A Decay-Based Long-Term Memory System

*Technical architecture for the LocalLLaMA and self-hosting community*

---

## The Problem with AI Memory Today

Most AI memory implementations follow the same pattern: accumulate facts into a profile document, periodically rewrite it with an LLM to keep it coherent, retrieve the whole thing on every request. This approach has specific failure modes that compound over time.

**Context collapse.** A monolithic profile treats "prefers dark mode" the same as "mother passed away last March." Both get equal weight, equal chance of being included or dropped during rewrites. There's no mechanism to distinguish foundational context from transient preferences.

**No decay model.** Information that was relevant six months ago stays relevant forever—or gets arbitrarily dropped during a rewrite. Real memory doesn't work this way. The restaurant you visited last week matters more than the one from two years ago, unless the old one keeps coming up in conversation.

**All-or-nothing retrieval.** You either include the entire profile (context bloat) or summarize it (information loss). There's no way to surface specific memories based on current conversation relevance while leaving others dormant.

**No relationship tracking.** "Got promoted to senior engineer" and "Took on the authentication system rewrite" are stored as isolated facts. The system can't recognize that one caused the other, or that a new fact might invalidate an old one.

**Calendar-time decay penalizes irregular usage.** If you implement simple time-based decay (older = less important), users who take a two-week vacation return to find their memories degraded. The system punished them for not using it.

LT_Memory addresses these through discrete memory objects, relationship links, and activity-based decay.

---

## Core Architecture: Discrete Memory Objects

Instead of a monolithic document, LT_Memory stores individual memory objects with explicit metadata:

```
Memory {
    id: UUID
    text: string                          # The actual memory content
    importance_score: float [0.0-1.0]     # Computed, not assigned

    # Access tracking
    access_count: int
    last_accessed: timestamp

    # Activity-based aging (not calendar-based)
    activity_days_at_creation: int
    activity_days_at_last_access: int

    # Temporal context
    expires_at: timestamp | null          # Hard expiration date
    happens_at: timestamp | null          # For events/deadlines

    # Relationship graph
    inbound_links: [{source_id, type, confidence, reasoning}]
    outbound_links: [{target_id, type, confidence, reasoning}]
}
```

**Why discrete beats monolithic:**

1. **Granular scoring.** Each memory earns its importance through demonstrated relevance, not through surviving a rewrite.
2. **Selective retrieval.** Surface only memories relevant to current context. A conversation about cooking doesn't need your career history.
3. **Independent decay.** Frequently accessed memories stay alive. Unused ones fade. No arbitrary culling during rewrites.
4. **Relationship preservation.** Links between memories survive as explicit data, not implicit context an LLM might lose.

---

## The Decay System

This is the core of LT_Memory. The philosophy: **memories must earn their keep through demonstrated relevance.**

### Activity-Based Aging

The system tracks `cumulative_activity_days`—the total number of distinct days a user has engaged with the system. Decay calculations use this counter, not calendar time.

A memory accessed 5 times over 30 activity days scores identically whether those 30 days span one month or six months. Users aren't penalized for vacations, weekends, or irregular usage patterns.

Each memory snapshots the user's `cumulative_activity_days` at creation and at last access. Decay formulas calculate deltas from these snapshots, not from wall-clock timestamps.

### The Scoring Formula

Importance is computed from four components, then normalized via sigmoid to the 0.0-1.0 range. The authoritative implementation lives in `lt_memory/scoring_formula.sql`.

#### 1. Value Score (Access Frequency with Momentum Decay)

```
effective_access_count = access_count × 0.95^(activity_days_since_last_access)
access_rate = effective_access_count / MAX(7, activity_days_since_creation)
value_score = LN(1 + access_rate / 0.02) × 0.8
```

**What this does:**
- Access count decays 5% per inactive activity day (the "momentum" fades)
- Normalized against memory age, with a 7-day floor to prevent new-memory spikes
- Baseline access rate of 0.02 means 1 access per 50 activity days is "average"
- Logarithmic scaling rewards consistent access over one-time spikes

**Why 5% decay?** Aggressive enough that a memory unused for 60 activity days loses ~95% of its access momentum. Conservative enough that a week's gap doesn't devastate a well-used memory.

#### 2. Hub Score (Network Centrality)

```
if inbound_links == 0:
    hub_score = 0.0
elif inbound_links <= 10:
    hub_score = inbound_links × 0.04
else:
    hub_score = 0.4 + (inbound_links - 10) × 0.02 / (1 + (inbound_links - 10) × 0.05)
```

Memories that other memories reference are conceptual anchors—they're worth preserving even if not directly accessed recently. But returns diminish sharply after 10 links: the 100th inbound reference adds far less value than the 2nd.

**Why diminishing returns?** Prevents runaway importance from over-linking. A memory connected to everything is probably too generic to be useful.

#### 3. Recency Boost (Exponential Fade)

```
recency_boost = 1.0 / (1.0 + activity_days_since_last_access × 0.03)
```

Recently accessed memories get boosted. At 33 activity days stale, this multiplier drops to ~0.5. It never reaches zero—a memory can always be revived through access.

**Why activity days here too?** Same principle: don't punish users for taking breaks.

#### 4. Temporal Multiplier (Calendar-Based)

This is the exception—temporal events must use wall-clock time because deadlines don't pause for vacations.

```
if happens_at is in the future:
    if days_until <= 1:  multiplier = 2.0
    elif days_until <= 7:  multiplier = 1.5
    elif days_until <= 14: multiplier = 1.2
    else: multiplier = 1.0
elif happens_at is in the past:
    if days_since <= 14:
        multiplier = 0.8 × (1.0 - days_since/14) + 0.1
    else:
        multiplier = 0.1
```

Events happening tomorrow get doubled importance. Past events decay to 0.1 multiplier over two weeks—still surfaceable if relevant, but not proactively pushed.

### Sigmoid Normalization

```
raw_score = (value_score + hub_score) × recency_boost × temporal_multiplier
importance = 1.0 / (1.0 + EXP(-(raw_score - 2.0)))
```

The sigmoid with center 2.0 maps typical memories to ~0.5 importance. This creates a natural distribution: most memories cluster around the middle, with outliers at both ends.

### Hard Expiration

If `expires_at` is set and more than 5 days past, importance drops to 0.0 immediately. A 5-day grace period allows linear decay from 1.0 to 0.0 for memories that just expired—useful for "event passed but might still be discussed" scenarios.

### Auto-Archival Threshold

Memories with `importance_score ≤ 0.001` are automatically archived. At this threshold, a memory has:
- Essentially no access momentum remaining
- Few or no inbound links
- Not been accessed in many activity cycles
- No upcoming temporal relevance

Archived memories aren't deleted—they're moved to cold storage, retrievable if explicitly searched but never proactively surfaced.

---

## Relationship Links

Memories connect through typed, bidirectional links:

| Type | Meaning |
|------|---------|
| `conflicts` | Mutually exclusive information |
| `supersedes` | Explicitly updates older information |
| `causes` | Direct causal relationship |
| `instance_of` | Specific example of a general pattern |
| `invalidated_by` | Empirical disproof of a prior belief |
| `motivated_by` | Explains reasoning behind another memory |

### Bidirectional Storage

When A links to B, both records update atomically:
- A's `outbound_links` gains B
- B's `inbound_links` gains A

Both sides store identical metadata: link type, confidence score, and reasoning text.

**Why bidirectional?** Hub score requires counting inbound links. Traversal from either direction should work without joins. The denormalization cost (storing twice) is worth the query simplicity.

### Link Creation

An LLM classifies potential relationships between semantically similar memories. Links are only created when classification confidence exceeds 0.7. The default is `null` (no link)—sparse, high-confidence links beat dense, noisy ones.

### Dead Link Cleanup

Links can point to archived or deleted memories. Rather than proactively scanning, the system uses heal-on-read: dead links discovered during traversal are cleaned up opportunistically. Acceptable variance is 1-2 phantom links per memory—the overhead of proactive scanning isn't worth the marginal consistency gain.

---

## Design Rationale

### Activity Days vs Calendar Days

**Considered:** Simple timestamp-based decay, tracking last_accessed as a datetime.

**Rejected because:** A user who engages daily for a month then takes two weeks off would return to degraded memories. The system should measure engagement depth, not wall-clock elapsed time.

**Tradeoff:** Requires tracking user activity days as a separate counter. Adds complexity but fundamentally changes the decay fairness model.

### JSONB Arrays vs Junction Tables

**Considered:** Separate `memory_links` junction table with foreign keys.

**Rejected because:** Bidirectional updates require two inserts plus integrity checks. JSONB arrays enable atomic append to both sides in a single transaction. Hub score calculation via `jsonb_array_length()` is trivially fast.

**Tradeoff:** Denormalized data. Links stored twice. No referential integrity enforcement (hence lazy cleanup). Worth it for operational simplicity.

### Sigmoid vs Linear Normalization

**Considered:** Linear scaling with min/max bounds.

**Rejected because:** Linear creates artificial cliffs at bounds and doesn't naturally cluster average memories. Sigmoid creates smooth S-curve where most memories naturally fall in 0.3-0.7 range, with outliers requiring genuine extreme scores.

**Tradeoff:** Sigmoid center (2.0) must be tuned. If average raw scores drift, the distribution skews. Current constants assume typical access patterns; heavy users might need recalibration.

### Diminishing Hub Returns vs Linear

**Considered:** Each inbound link adds equal value.

**Rejected because:** Gaming potential—a memory linking to everything would become immortal. Also, being referenced by 100 memories probably means the content is too generic to be useful.

**Tradeoff:** The knee at 10 links and decay rate beyond it are empirical. Might need adjustment for different usage patterns.

---

## The "Just Talk Normal" Philosophy

All this machinery serves a simple goal: **users shouldn't think about memory.**

They don't tag things as important. They don't curate a profile. They don't worry about what the AI remembers or forgets. They just talk.

The system handles the rest:
- Extracts memories from conversations automatically
- Scores importance based on demonstrated usage patterns
- Surfaces relevant context when needed
- Lets irrelevant information fade naturally
- Maintains relationship context without user intervention

Complexity in the implementation enables simplicity in the interface. The scoring formula has 15 tuned constants and a 100-line SQL expression. The user experience is: just talk normal.

---

*Implementation: `lt_memory/scoring_formula.sql` (authoritative), `lt_memory/models.py` (data structures), `lt_memory/db_access.py` (persistence)*
