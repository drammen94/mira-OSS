# Working Memory Trinket System

*Technical architecture for dynamic system prompt composition*

---

## Overview

Trinkets are event-driven components that passively reflect system state in Claude's context. Each trinket generates a content section for the system prompt, enabling dynamic context injection without tight coupling between components.

**Core Design Principle**: Trinkets publish content through an event-driven architecture, allowing the system prompt to be composed from independent, self-managing components.

---

## Base Class: EventAwareTrinket

**File**: `working_memory/trinkets/base.py` (lines 26-135)

### Required Implementation

Every trinket must inherit from `EventAwareTrinket` and implement:

```python
class MyTrinket(EventAwareTrinket):
    cache_policy: bool = False  # Optional: enables prefix caching

    def _get_variable_name(self) -> str:
        """Return the section name for system prompt."""
        return "my_section_name"

    def generate_content(self, context: Dict[str, Any]) -> str:
        """Generate content for this section. Return empty string if no content."""
        return "<my_section>...</my_section>"
```

### Constructor Signature (lines 41-56)

```python
def __init__(self, event_bus: 'EventBus', working_memory: 'WorkingMemory'):
    self.event_bus = event_bus
    self.working_memory = working_memory
    self._variable_name: str = self._get_variable_name()
    self.working_memory.register_trinket(self)  # Auto-registration
```

### Core Methods

| Method | Lines | Purpose |
|--------|-------|---------|
| `handle_update_request(event)` | 70-98 | Receives update events, generates content, publishes via `TrinketContentEvent` |
| `_persist_to_valkey(content)` | 100-120 | Persists trinket content to Valkey for API access |
| `generate_content(context)` | 122-135 | **Abstract** - subclasses must implement |

### Properties Available to Subclasses

- `self.event_bus` - For publishing events
- `self.working_memory` - For accessing other trinkets
- `self._variable_name` - Section identifier

---

## Implemented Trinkets

All trinkets located in `working_memory/trinkets/`:

| Trinket | File | Variable Name | Cache Policy | Purpose |
|---------|------|---------------|--------------|---------|
| **TimeManager** | `time_manager.py` | `datetime_section` | `False` | Current date/time with user timezone |
| **ManifestTrinket** | `manifest_trinket.py` | `conversation_manifest` | `True` | Conversation segment manifest |
| **DomaindocTrinket** | `domaindoc_trinket.py` | `domaindoc` | `True` | Domain knowledge documents |
| **ProactiveMemoryTrinket** | `proactive_memory_trinket.py` | `relevant_memories` | `False` | Surfaced memories from LT_Memory |
| **ReminderManager** | `reminder_manager.py` | `active_reminders` | `False` | Active and overdue reminders |
| **ToolLoaderTrinket** | `tool_loader_trinket.py` | `tool_hints` | `False` | Dynamic tool loading state |
| **ToolGuidanceTrinket** | `tool_guidance_trinket.py` | `tool_guidance` | `False` | Tool usage hints |
| **PunchclockTrinket** | `punchclock_trinket.py` | `punchclock_status` | `False` | Active time tracking sessions |
| **GetContextTrinket** | `getcontext_trinket.py` | `context_search_results` | `False` | Context search results |

### Trinket Details

#### TimeManager (`time_manager.py:1-48`)
- Generates formatted datetime with user timezone conversion
- Output format: `<current_datetime>...</current_datetime>`

#### ManifestTrinket (`manifest_trinket.py:1-212`)
- Formats segments grouped by date
- Shows time ranges for collapsed segments, "ACTIVE" for current segment
- Output format: `<conversation_manifest>...</conversation_manifest>`

#### DomaindocTrinket (`domaindoc_trinket.py:1-231`)
- Reads from SQLite storage via `UserDataManager`
- Supports one level of nesting (sections with subsections)
- Collapsed sections show only headers; expanded show full content
- Large section threshold: `LARGE_SECTION_CHARS = 5000` (line 20)
- Output format: `<domain_knowledge>...</domain_knowledge>`

#### ProactiveMemoryTrinket (`proactive_memory_trinket.py:1-178`)
- Caches memories between updates (line 21)
- Formats memories as XML with nested `linked_memories` elements
- Supports recursive nested memory display with max depth
- Public method: `get_cached_memories()` (lines 27-37)
- Output format: `<surfaced_memories>...</surfaced_memories>`

#### ReminderManager (`reminder_manager.py:1-200`)
- Fetches reminders from ReminderTool
- Separates user reminders vs internal reminders
- Separates overdue and today's reminders
- Output format: `<active_reminders>...</active_reminders>`

#### ToolLoaderTrinket (`tool_loader_trinket.py:1-275`)
- Tracks available tools and loaded tools with state info
- Uses `LoadedToolInfo` dataclass (lines 16-23)
- Handles lifecycle actions: initialize, tool_loaded, tool_unloaded, tool_used, fallback_mode, cleanup_completed
- Subscribes to `TurnCompletedEvent` for auto-cleanup (line 59)
- Fallback tools cleaned after 1 turn; regular tools after idle threshold
- Output format: `<tool_loader>...</tool_loader>`

#### ToolGuidanceTrinket (`tool_guidance_trinket.py:1-58`)
- Collects `tool_hints` from tools providing usage tips
- Output format: `<tool_guidance>...</tool_guidance>`

#### PunchclockTrinket (`punchclock_trinket.py:1-193`)
- Fetches sessions from `list_punchclock_sessions()`
- Partitions sessions into running, paused, completed
- Shows only 3 most recent completed sessions
- Output format: `<punchclock>...</punchclock>`

#### GetContextTrinket (`getcontext_trinket.py:1-245`)
- Handles multiple concurrent searches in same segment
- Displays success results until segment collapse
- Error/timeout messages expire after 5 turns
- Clears all state when segment collapses
- Subscribes to `TurnCompletedEvent` and `SegmentCollapsedEvent`
- Output format: `<context_search_results>...</context_search_results>`

---

## Composition System

### SystemPromptComposer

**File**: `working_memory/composer.py` (lines 37-161)

The composer assembles trinket content into a structured system prompt with prefix caching support.

#### Configuration (lines 15-34)

```python
@dataclass
class ComposerConfig:
    section_order: List[str] = field(default_factory=lambda: [
        # Cached sections (MUST be sequential first for prefix caching)
        "base_prompt",
        "conversation_manifest",
        "domaindoc",
        # Non-cached sections
        "datetime_section",
        "active_reminders",
        "punchclock_status",
        "tool_guidance",
        "tool_hints",
        "relevant_memories",
        "workflow_guidance",
        "temporal_context",
    ])
    section_separator: str = "\n\n---\n\n"
    strip_empty_sections: bool = True
```

#### Key Methods

| Method | Lines | Purpose |
|--------|-------|---------|
| `set_base_prompt(prompt)` | 58-67 | Sets base system prompt, marks as cached |
| `add_section(name, content, cache_policy)` | 69-84 | Adds/updates a section |
| `clear_sections(preserve_base)` | 86-100 | Clears sections, optionally preserves base |
| `compose()` | 102-155 | Composes final prompt, returns `{cached_content, non_cached_content}` |

#### Composition Logic (lines 102-155)

1. Separates sections by cache policy while maintaining order
2. Groups all cached sections first (critical for Claude's prefix caching)
3. Joins sections with `section_separator`
4. Cleans up excessive whitespace (3+ newlines become 2)
5. Returns structured dict with `cached_content` and `non_cached_content` keys

---

## Orchestration: WorkingMemory

**File**: `working_memory/core.py` (lines 23-280)

The `WorkingMemory` class orchestrates trinket updates and composition.

### Event Subscriptions (lines 50-58)

```python
self.event_bus.subscribe('ComposeSystemPromptEvent', self._handle_compose_prompt)
self.event_bus.subscribe('UpdateTrinketEvent', self._handle_update_trinket)
self.event_bus.subscribe('TrinketContentEvent', self._handle_trinket_content)
```

### Key Methods

| Method | Lines | Purpose |
|--------|-------|---------|
| `register_trinket(trinket)` | 63-72 | Called by trinkets during init, stores by class name |
| `_handle_compose_prompt(event)` | 74-120 | Main orchestration - triggered by `ComposeSystemPromptEvent` |
| `_handle_update_trinket(event)` | 122-159 | Routes `UpdateTrinketEvent` to specific trinket |
| `_handle_trinket_content(event)` | 161-171 | Collects trinket content via `TrinketContentEvent` |
| `publish_trinket_update(target, context)` | 173-194 | Triggers update for specific trinket |
| `get_trinket(name)` | 206-219 | Gets trinket instance by name |
| `get_trinket_state(section_name)` | 221-251 | Gets cached state from Valkey |
| `get_all_trinket_states()` | 253-280 | Gets all trinket states from Valkey |

### Composition Flow (`_handle_compose_prompt`, lines 74-120)

```
1. Store continuum and user context
2. Personalize base prompt with user's first name
3. Set base prompt in composer
4. Clear previous sections except base
5. Publish UpdateTrinketEvent for each registered trinket
6. Compose the final prompt
7. Publish SystemPromptComposedEvent with structured content
```

### Error Handling (lines 142-157)

- Catches exceptions from individual trinket updates
- Distinguishes infrastructure failures (Database, Valkey, Connection) from logic errors
- Continues processing even if individual trinkets fail (fail-safe pattern)

---

## Trinket Registration

### Auto-Registration Pattern

Trinkets self-register by calling `self.working_memory.register_trinket(self)` in their `__init__` (base.py line 54). WorkingMemory stores trinkets in `_trinkets` dict by class name.

### Factory-Based Creation

**File**: `cns/integration/factory.py` (lines 151-188)

Trinkets created in order during factory initialization:

```python
# Lines 160-177
TimeManager(event_bus, self._working_memory)
ReminderManager(event_bus, self._working_memory)
ManifestTrinket(event_bus, self._working_memory)
ProactiveMemoryTrinket(event_bus, self._working_memory)
ToolGuidanceTrinket(event_bus, self._working_memory)
PunchclockTrinket(event_bus, self._working_memory)
DomaindocTrinket(event_bus, self._working_memory)
GetContextTrinket(event_bus, self._working_memory)

# ToolLoaderTrinket created separately (lines 182-188)
# Requires tool_repo parameter for cleanup operations
```

---

## Trinket Lifecycle

### Phase 1: Initialization
- Factory creates trinket with `event_bus` and `working_memory`
- Base `__init__` runs, calls `_get_variable_name()`, registers with working memory
- Subclasses can subscribe to additional events

### Phase 2: Update Request
- `ComposeSystemPromptEvent` published by orchestrator
- WorkingMemory publishes `UpdateTrinketEvent` for each registered trinket
- Event routed to trinket's `handle_update_request(event)`

### Phase 3: Content Generation
- Trinket calls `generate_content(context)` (abstract method)
- If content is non-empty: persists to Valkey, publishes `TrinketContentEvent`

### Phase 4: Collection
- WorkingMemory receives `TrinketContentEvent`
- Calls `composer.add_section(variable_name, content, cache_policy)`

### Phase 5: Composition
- Composer assembles all sections in configured order
- Returns `{cached_content, non_cached_content}`
- WorkingMemory publishes `SystemPromptComposedEvent`

---

## Cache Policy and Prefix Caching

### How It Works

The `cache_policy` class attribute controls whether a trinket's content should be included in the cached portion of the system prompt.

**Cached trinkets** (`cache_policy = True`):
- ManifestTrinket
- DomaindocTrinket

**Non-cached trinkets** (`cache_policy = False`, default):
- TimeManager, ProactiveMemoryTrinket, ReminderManager, ToolLoaderTrinket, ToolGuidanceTrinket, PunchclockTrinket, GetContextTrinket

### Critical Ordering Constraint

**Comment from composer.py lines 22-23:**
> "IMPORTANT: All cached trinkets (cache_policy=True) must be sequential above this line for Claude's prefix caching to work efficiently."

Cached sections appear first and together. Non-cached sections appear after. Claude's prefix caching can cache the stable prefix (cached sections), while the non-cached part changes per turn.

### Valkey Persistence (base.py lines 100-120)

Trinket state persisted to Valkey:
- Key format: `"trinkets:{user_id}"`
- Stored as hash with section_name as field key
- Value: JSON dict with `content`, `cache_policy`, `updated_at`

---

## Event Flow Diagram

```
ContinuumOrchestrator
        │
        │ publishes
        ▼
ComposeSystemPromptEvent
        │
        │ received by
        ▼
WorkingMemory._handle_compose_prompt()
        │
        │ publishes (for each trinket)
        ▼
UpdateTrinketEvent
        │
        │ routed to
        ▼
Trinket.handle_update_request()
        │
        │ calls
        ▼
Trinket.generate_content()
        │
        │ publishes
        ▼
TrinketContentEvent
        │
        │ received by
        ▼
WorkingMemory._handle_trinket_content()
        │
        │ adds to
        ▼
SystemPromptComposer
        │
        │ composes
        ▼
{cached_content, non_cached_content}
        │
        │ publishes
        ▼
SystemPromptComposedEvent
        │
        │ received by
        ▼
ContinuumOrchestrator._handle_system_prompt_composed()
```

---

## Content Formatting Standards

All trinkets use XML-style formatting for their content sections:

| Trinket | Format |
|---------|--------|
| TimeManager | `<current_datetime>...</current_datetime>` |
| ManifestTrinket | `<conversation_manifest>...</conversation_manifest>` |
| DomaindocTrinket | `<domain_knowledge>...</domain_knowledge>` |
| ProactiveMemoryTrinket | `<surfaced_memories>...</surfaced_memories>` |
| ReminderManager | `<active_reminders>...</active_reminders>` |
| ToolLoaderTrinket | `<tool_loader>...</tool_loader>` |
| ToolGuidanceTrinket | `<tool_guidance>...</tool_guidance>` |
| PunchclockTrinket | `<punchclock>...</punchclock>` |
| GetContextTrinket | `<context_search_results>...</context_search_results>` |

**Empty content rule**: Return empty string `""` when no content to display. Composer skips empty sections (composer.py lines 121-122).

---

*Implementation: `working_memory/trinkets/base.py` (base class), `working_memory/composer.py` (composition), `working_memory/core.py` (orchestration), `cns/integration/factory.py` (instantiation)*
