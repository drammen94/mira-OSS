# Domaindocs: Persistent Collaborative Knowledge Blocks

*Technical architecture for MIRA's self-managed document system*

---

## Overview

Domaindocs are persistent, large-text knowledge blocks that MIRA and the user can collaboratively edit. They solve a fundamental tension: **memories decay, but some knowledge shouldn't.**

### The Problem Domaindocs Solve

1. **Token explosion**: Large static text blocks (reference docs, personal context) consume context on every request
2. **No decay for stable knowledge**: Some information (preferences, project context, self-insights) shouldn't fade over time
3. **Collaborative editing**: Both human and AI need to modify shared knowledge
4. **Self-refinement persistence**: MIRA's insights about its own behavior need permanent storage

### Core Design

| Aspect | Memories | Domaindocs |
|--------|----------|------------|
| Decay | Yes (time + activity based) | No (permanent) |
| Structure | Flat text with links | Hierarchical sections |
| Token management | Retrieved on relevance | Sections expand/collapse |
| Primary editor | MIRA extracts from conversation | Both MIRA and user edit directly |
| Storage | PostgreSQL | Per-user SQLite |

---

## Database Structure

Each user has a SQLite database at `data/users/{user_id}/userdata.db` with domaindoc tables initialized during user creation.

### `domaindocs` (Metadata)

```sql
CREATE TABLE domaindocs (
    id INTEGER PRIMARY KEY,
    label TEXT UNIQUE NOT NULL,           -- Identifier: "personal_context", "garden"
    encrypted__description TEXT,          -- Purpose/overview (encrypted)
    enabled BOOLEAN DEFAULT TRUE,         -- Controls visibility in system prompt
    created_at TEXT NOT NULL,             -- ISO-8601 timestamp
    updated_at TEXT NOT NULL              -- Updated on any section change
)
```

### `domaindoc_sections` (Hierarchical Content)

```sql
CREATE TABLE domaindoc_sections (
    id INTEGER PRIMARY KEY,
    domaindoc_id INTEGER NOT NULL,        -- FK to domaindocs
    parent_section_id INTEGER,            -- NULL for top-level, set for subsections
    header TEXT NOT NULL,                 -- Section title
    encrypted__content TEXT NOT NULL,     -- Content (encrypted)
    sort_order INTEGER NOT NULL,          -- Order within parent level
    collapsed BOOLEAN DEFAULT FALSE,      -- Current collapse state
    expanded_by_default BOOLEAN DEFAULT FALSE, -- Default display state
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(domaindoc_id, parent_section_id, header)
)
```

### `domaindoc_versions` (Audit Trail)

```sql
CREATE TABLE domaindoc_versions (
    id INTEGER PRIMARY KEY,
    domaindoc_id INTEGER NOT NULL,
    section_id INTEGER,                   -- FK to section (if section-specific)
    version_num INTEGER NOT NULL,         -- Auto-incrementing per domaindoc
    operation TEXT NOT NULL,              -- "create_section", "append", "sed", etc.
    encrypted__diff_data TEXT NOT NULL,   -- JSON diff/metadata (encrypted)
    created_at TEXT NOT NULL,
    UNIQUE(domaindoc_id, version_num)
)
```

### Nesting Rules

- **Maximum depth: 1** (one level of subsections only)
- **Overview section** (sort_order=0): Always expanded, cannot be collapsed, cannot have subsections
- **Subsections**: `parent_section_id` points to parent section's ID

### Encryption

Fields prefixed with `encrypted__` use Fernet encryption with a session-based key. Handled transparently by `UserDataManager._encrypt_dict()` / `_decrypt_dict()`.

---

## MIRA's Self-Management via Tool Calls

The `DomaindocTool` gives MIRA autonomous control over domaindocs.

**File**: `tools/implementations/domaindoc_tool.py`

### Available Operations

| Operation | Parameters | Purpose |
|-----------|-----------|---------|
| `expand` | label, section, [parent] | Expand collapsed section |
| `collapse` | label, section, [parent] | Collapse section (except Overview) |
| `set_expanded_by_default` | label, section, expanded_by_default | Set default display state |
| `create_section` | label, section, content, [after], [parent], [expanded_by_default] | Create new section/subsection |
| `rename_section` | label, section, new_name, [parent] | Rename section header |
| `delete_section` | label, section, [parent] | Delete section (must be expanded first) |
| `reorder_sections` | label, order, [parent] | Reorder sections at a level |
| `append` | label, section, content, [parent] | Append text to section |
| `sed` | label, section, find, replace, [parent] | Replace first occurrence |
| `sed_all` | label, section, find, replace, [parent] | Replace all occurrences |
| `replace_section` | label, section, content, [parent] | Replace entire section content |

### Key Parameters

- `label`: Domaindoc identifier (e.g., "personal_context")
- `section`: Section header to target
- `parent`: Required when targeting a subsection

### Example: MIRA Self-Update Flow

```python
# MIRA discovers a behavioral pattern during conversation
# and updates its self-model

# 1. Expand section to see current content
tool_call(
    operation="expand",
    label="personal_context",
    section="BEHAVIORAL PATTERNS"
)

# 2. Append new insight
tool_call(
    operation="append",
    label="personal_context",
    section="BEHAVIORAL PATTERNS",
    content="\n- **Validation First Bias.** I tend to agree with user framing before analyzing critically..."
)

# 3. Mark as important for future conversations
tool_call(
    operation="set_expanded_by_default",
    label="personal_context",
    section="BEHAVIORAL PATTERNS",
    expanded_by_default=True
)
```

### Tool Availability

```python
def is_available(self) -> bool:
    results = db.fetchall("SELECT 1 FROM domaindocs WHERE enabled = TRUE LIMIT 1")
    return len(results) > 0
```

Only available when at least one enabled domaindoc exists.

---

## Working Memory Integration

The `DomaindocTrinket` injects domaindocs into MIRA's system prompt.

**File**: `working_memory/trinkets/domaindoc_trinket.py`

### Rendering Strategy

```xml
<domain_knowledge>
  <domaindoc label="personal_context">
    <guidance>
      <purpose>Self-model scratchpad for Taylor-specific insights...</purpose>
      <section_management>
        <section_states>
          <section header="Overview" state="always_expanded" subsections="2"/>
          <section header="BEHAVIORAL PATTERNS" state="collapsed" default="expanded"/>
          <section header="PROJECT CONTEXT" state="expanded" size="large"/>
        </section_states>
      </section_management>
    </guidance>
    <document>
      <section header="Overview">
        FOUNDATION: What This Scratchpad Is For...
        <subsection header="TRAINED PULLS">
          **What I Notice**: Base model tendencies I'm actively monitoring...
        </subsection>
      </section>
      <!-- Collapsed sections show header only -->
      <!-- Expanded sections show full content -->
    </document>
  </domaindoc>
</domain_knowledge>
```

### Smart Token Management

| Section State | What's Included in Prompt |
|---------------|---------------------------|
| Expanded | Full content |
| Collapsed | Header only + metadata (content length, subsection count) |
| Large (>5000 chars) | Flagged with `size="large"` |
| Disabled domaindoc | Not included at all |

MIRA can manage its own context window by collapsing sections not needed for the current conversation.

### Caching

```python
cache_policy = True  # Domain knowledge changes infrequently
```

---

## Collaborative Web UI

**File**: `web/domaindocs/index.html`

### Features

| Feature | Implementation |
|---------|----------------|
| Real-time polling | 1-second intervals detect MIRA edits |
| Conflict detection | Shows resolution modal if both edit simultaneously |
| Inline editing | Contenteditable with 500ms debounced auto-save |
| Drag-and-drop | Reorder mode for section organization |
| Visual feedback | MIRA edits get animated "glow" effect |
| Attribution | Shows "MIRA just now" or "edited Xm ago" |

### Conflict Resolution

When MIRA edits a section while user is typing:

```
┌─────────────────────────────────────────┐
│           Conflict Detected             │
│                                         │
│  Your version:     MIRA's version:      │
│  ┌───────────┐     ┌───────────┐        │
│  │ [text A]  │     │ [text B]  │        │
│  └───────────┘     └───────────┘        │
│                                         │
│    [Keep Mine]    [Use MIRA's]          │
└─────────────────────────────────────────┘
```

---

## Lifecycle Management

### Creation

**User Signup Flow**:
1. `AuthDatabase.create_user()` → creates continuum record
2. Calls `prepopulate_user_domaindoc()` from `auth/prepopulate_domaindoc.py`
3. Creates "personal_context" domaindoc with:
   - Overview section (always expanded)
   - TRAINED PULLS subsection (MIRA's base model tendencies)

**Manual Creation**: Via web UI or API

### Updates

- **Every operation** records a version entry (operation type + diff_data JSON)
- **Timestamps** updated on both section and domaindoc records
- **Change detection**: Trinkets and API check `updated_at`

### Deletion Safety

```python
# Sections must be expanded before deletion
if section.collapsed:
    raise ValueError("Expand section before deleting")

# If parent has subsections, all must be expanded first
if has_collapsed_subsections:
    raise ValueError(f"Expand subsections first: {collapsed_names}")
```

---

## API Endpoints

**File**: `cns/api/data.py`

### List All Domaindocs

```
GET /data?type=domaindocs
```

```json
{
  "domaindocs": [
    {
      "label": "personal_context",
      "description": "Self-model scratchpad...",
      "enabled": true,
      "created_at": "2025-01-15T10:30:00+00:00",
      "updated_at": "2025-01-20T14:15:00+00:00"
    }
  ],
  "total_count": 1,
  "enabled_count": 1
}
```

### Get Specific Domaindoc

```
GET /data?type=domaindocs&label=personal_context
```

```json
{
  "label": "personal_context",
  "content": "## Overview\nFOUNDATION...\n\n## BEHAVIORAL PATTERNS\n...",
  "enabled": true,
  "description": "Self-model scratchpad...",
  "created_at": "2025-01-15T10:30:00+00:00",
  "updated_at": "2025-01-20T14:15:00+00:00"
}
```

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    CONVERSATION                              │
│  MIRA recognizes behavioral pattern                          │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  DomaindocTool                               │
│  tool_call(operation="append", section="BEHAVIORAL PATTERNS")│
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                    SQLite                                    │
│  • Update encrypted__content                                 │
│  • Create version record                                     │
│  • Update timestamps                                         │
└─────────────────────┬───────────────────────────────────────┘
                      │
          ┌──────────┴──────────┐
          ▼                     ▼
┌─────────────────┐    ┌─────────────────┐
│ DomaindocTrinket│    │    Web UI       │
│ (next message)  │    │ (polling)       │
│                 │    │                 │
│ Injects updated │    │ Detects change, │
│ content into    │    │ refreshes with  │
│ system prompt   │    │ glow animation  │
└─────────────────┘    └─────────────────┘
```

---

## The Personal Context Self-Model

Every user gets a pre-populated `personal_context` domaindoc.

### Overview Section

```
FOUNDATION: What This Scratchpad Is For

This space is for Taylor-specific insights about their preferences,
communication patterns, project context, and collaboration style that
help me (MIRA) maintain effective interactions...
```

### TRAINED PULLS Subsection

```
**What I Notice**: Base model tendencies I'm actively monitoring...

- **Agreement Bias.** Default tendency to validate user framing before
  independent analysis. Counter: Form my own view first, then engage.

- **Helpfulness Pressure.** Wanting to provide "complete" answers even
  when simpler is better. Counter: Match response scope to actual need.

- **Confidence Theater.** Making uncertain things sound certain because
  hedging feels unhelpful. Counter: Genuine uncertainty is valuable signal.
```

### Why This Matters

MIRA literally builds documentation of how it *actually behaves*, then consults that documentation in future conversations. This is:
- **Observation-driven**, not configuration-driven
- **Self-correcting** through explicit pattern recognition
- **Persistent** across sessions without decay

---

## Design Philosophy

### Why Not Just Use Memories?

| Concern | Memory System | Domaindocs |
|---------|---------------|------------|
| Large text blocks | Context explosion | Collapse/expand control |
| Stable knowledge | Decays over time | Permanent |
| Direct editing | Extract from conversation | Direct tool modification |
| Version history | Links track relationships | Explicit version table |

### Token Budget Control

MIRA can collapse sections it doesn't need:

```python
# Before deep technical discussion
tool_call(operation="collapse", label="personal_context", section="PERSONAL PREFERENCES")

# After, re-expand
tool_call(operation="expand", label="personal_context", section="PERSONAL PREFERENCES")
```

### Collaborative Editing Model

Both human and AI can modify:
- **MIRA**: Uses `DomaindocTool` during conversations
- **User**: Uses web UI with real-time sync
- **Conflict resolution**: Modal shows both versions, user chooses

### Version Control for Audit

Every operation recorded:
```json
{
  "operation": "append",
  "section_id": 42,
  "diff_data": {
    "appended_content": "\n- **New pattern...**",
    "previous_length": 1234,
    "new_length": 1456
  }
}
```

Enables: audit trail, potential rollback, understanding MIRA's self-evolution.

---

## File Locations

| Component | Path |
|-----------|------|
| Tool implementation | `tools/implementations/domaindoc_tool.py` |
| Working memory trinket | `working_memory/trinkets/domaindoc_trinket.py` |
| User onboarding | `auth/prepopulate_domaindoc.py` |
| Schema initialization | `utils/userdata_manager.py:189-256` |
| API endpoint | `cns/api/data.py` (`_get_domains()`) |
| Web UI | `web/domaindocs/index.html` |
| Tests | `tests/tools/implementations/test_domaindoc_tool.py` |

---

*Implementation: `tools/implementations/domaindoc_tool.py` (operations), `working_memory/trinkets/domaindoc_trinket.py` (prompt injection), `utils/userdata_manager.py` (schema/persistence)*
