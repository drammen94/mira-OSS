# Working Memory System Overview

## What is Working Memory?

Working Memory is MIRA's event-driven system for dynamically composing system prompts by orchestrating specialized components called "trinkets." Each trinket contributes specific context to the system prompt (time, reminders, memories, tool guidance, etc.), and the Working Memory system coordinates their contributions through CNS events to create a coherent, contextually-aware prompt for each conversation turn.

## Architecture Overview

### Directory Structure
```
working_memory/
├── core.py                  # Event-driven orchestrator
├── composer.py             # System prompt assembly
└── trinkets/               # Specialized context providers
    ├── base.py            # Base trinket class
    ├── time_manager.py    # Date/time context
    ├── reminder_manager.py # Active reminders
    ├── proactive_memory_trinket.py # Surfaced memories
    ├── tool_guidance_trinket.py    # Tool usage hints
    └── workflow_trinket.py         # Workflow guidance
```

## Core Components

### 1. **Working Memory Core** (`core.py`)

The central orchestrator that coordinates trinkets through events:

**Key Responsibilities**:
- Managing trinket registration
- Routing update requests to specific trinkets
- Collecting trinket content via events
- Triggering prompt composition
- Publishing composed prompts back to CNS

**Event Subscriptions**:
- `ComposeSystemPromptEvent`: Triggers full prompt composition
- `UpdateTrinketEvent`: Routes updates to specific trinkets
- `TrinketContentEvent`: Collects content from trinkets

**Core Methods**:
- `register_trinket()`: Adds trinket to the registry
- `_handle_compose_prompt()`: Orchestrates full prompt generation
- `_handle_update_trinket()`: Routes events to target trinkets
- `_handle_trinket_content()`: Collects and stores trinket output
- `publish_trinket_update()`: External trigger for specific trinket updates

### 2. **System Prompt Composer** (`composer.py`)

Handles the assembly of trinket contributions into a coherent prompt:

**Configuration**:
```python
@dataclass
class ComposerConfig:
    section_order: List[str] = [
        'base_prompt',
        'datetime_section',
        'active_reminders',
        'tool_guidance',
        'relevant_memories',
        'workflow_guidance',
        'temporal_context'
    ]
    section_separator: str = "\n\n"
    strip_empty_sections: bool = True
```

**Key Features**:
- Ordered section assembly
- Empty section filtering
- Whitespace normalization
- Flexible section addition
- Support for dynamic sections

**Operations**:
- `set_base_prompt()`: Sets the foundation prompt
- `add_section()`: Adds/updates a named section
- `compose()`: Assembles final prompt in order
- `clear_sections()`: Resets for new composition

### 3. **Event-Aware Trinket Base** (`trinkets/base.py`)

Abstract base class for all trinkets:

**Core Pattern**:
1. Receives `UpdateTrinketEvent` with context
2. Generates content via `generate_content()`
3. Publishes `TrinketContentEvent` with results
4. Working Memory collects and composes

**Required Implementation**:
```python
class MyTrinket(EventAwareTrinket):
    def _get_variable_name(self) -> str:
        return "my_section_name"
    
    def generate_content(self, context: Dict) -> str:
        # Generate and return content
        return "Section content"
```

**Lifecycle**:
- Auto-registers with Working Memory on init
- Handles updates via event system
- Publishes content when available
- Supports cleanup operations

## Trinket Implementations

### Time Manager (`time_manager.py`)
**Purpose**: Provides current date/time context
- **Section**: `datetime_section`
- **Content**: User's local time + UTC time
- **Update**: Fresh timestamp on every request
- **Context**: Uses user's timezone from auth

### Reminder Manager (`reminder_manager.py`)
**Purpose**: Shows active reminders
- **Section**: `active_reminders`
- **Content**: Today's and overdue reminders
- **Update**: Queries reminder_tool database
- **Context**: User-specific reminders only

### Proactive Memory Trinket (`proactive_memory_trinket.py`)
**Purpose**: Displays relevant long-term memories
- **Section**: `relevant_memories`
- **Content**: Memories grouped by importance
- **Update**: Receives memories via context
- **Context**: Pre-surfaced by memory relevance service

### Tool Guidance Trinket (`tool_guidance_trinket.py`)
**Purpose**: Provides tool usage hints
- **Section**: `tool_guidance`
- **Content**: Enabled tools and usage tips
- **Update**: Tool repository publishes hints
- **Context**: Based on conversation relevance

### Workflow Trinket (`workflow_trinket.py`)
**Purpose**: Active workflow guidance
- **Section**: `workflow_guidance`
- **Content**: Current step, prerequisites, guidance
- **Update**: Workflow manager provides state
- **Context**: Active workflow progress

## Event Flow

### System Prompt Composition Flow
```
CNS Orchestrator → ComposeSystemPromptEvent
    ↓
Working Memory receives event
    ↓
Clear previous sections (preserve base)
    ↓
For each registered trinket:
    → Publish UpdateTrinketEvent
    → Trinket generates content
    → Trinket publishes TrinketContentEvent
    → Working Memory collects content
    ↓
Composer assembles sections in order
    ↓
SystemPromptComposedEvent → CNS Orchestrator
```

### External Trinket Update Flow
```
External System (e.g., Memory Service) → Has new data
    ↓
Working Memory.publish_trinket_update("ProactiveMemoryTrinket", context)
    ↓
UpdateTrinketEvent → Target Trinket
    ↓
Trinket processes and publishes content
```

## Key Design Principles

### 1. **Event-Driven Architecture**
- All coordination through CNS events
- No direct trinket-to-trinket communication
- Synchronous event handling for simplicity
- Clear event contracts

### 2. **Separation of Concerns**
- Trinkets: Generate specific content
- Composer: Assembles sections
- Core: Orchestrates via events
- Each component has single responsibility

### 3. **Flexible Composition**
- Trinkets publish only when they have content
- Empty sections automatically filtered
- Configurable section ordering
- Support for dynamic sections

### 4. **User Context Awareness**
- All trinkets receive user_id in events
- User-specific data automatically scoped
- Timezone and preferences respected
- Complete user isolation

## Integration Points

### With CNS Orchestrator
- Receives `ComposeSystemPromptEvent` to start
- Publishes `SystemPromptComposedEvent` with result
- All communication via event bus

### With Memory Systems
- Memory relevance service triggers ProactiveMemoryTrinket
- Memories passed via update context
- No direct memory system dependency

### With Tool System
- Tool repository triggers ToolGuidanceTrinket
- Tool hints passed via context
- Updates on tool relevance changes

### With Workflow Manager
- Workflow state triggers WorkflowTrinket
- Active workflow context provided
- Updates on workflow progression

## Configuration

Composer configuration in `ComposerConfig`:
- `section_order`: Defines prompt section sequence
- `section_separator`: String between sections (default: "\n\n")
- `strip_empty_sections`: Remove empty content (default: True)

## Benefits

1. **Modularity**: Easy to add/remove/modify trinkets
2. **Maintainability**: Clear separation of concerns
3. **Flexibility**: Dynamic prompt composition
4. **Consistency**: Ordered, predictable output
5. **Performance**: Synchronous, efficient updates
6. **Extensibility**: Simple to add new context types

## Creating a New Trinket

### Step 1: Create Trinket Class
```python
from working_memory.trinkets.base import EventAwareTrinket

class MyCustomTrinket(EventAwareTrinket):
    def _get_variable_name(self) -> str:
        return "my_custom_section"
    
    def generate_content(self, context: Dict[str, Any]) -> str:
        # Your logic here
        data = self._fetch_data(context.get('user_id'))
        if not data:
            return ""  # No content = no section
        
        return f"# My Custom Section\n{self._format_data(data)}"
```

### Step 2: Register in Initialization
```python
# In your initialization code
my_trinket = MyCustomTrinket(event_bus, working_memory)
```

### Step 3: Add to Section Order
```python
# In ComposerConfig
section_order = [
    'base_prompt',
    'datetime_section',
    'my_custom_section',  # Add your section
    # ... other sections
]
```

## Example Composed Prompt

```
You are MIRA, an AI assistant...
[Base prompt content]

# Current Date and Time
The current date and time is December 5, 2024 3:45 PM PST (UTC: December 5, 2024 11:45 PM).

# Active Reminders
You have the following active reminders:
- Call John about project update (Due: Today 5:00 PM)
- Team standup meeting (Due: Tomorrow 9:00 AM)

# Tool Guidance
The following tools are currently available:
- calendar_tool: Manage calendar events
- email_tool: Send and receive emails

# Relevant Long-term Memories
The following memories from previous conversations may be relevant:

## High Importance Memories
- mem_123 - User prefers morning meetings before 10 AM
- mem_456 - Project deadline is December 15th

*These memories are automatically surfaced based on conversation context.*
```

## Troubleshooting

### Common Issues

1. **Missing Sections**: Check if trinket is registered and generating content
2. **Wrong Order**: Verify section name matches `ComposerConfig.section_order`
3. **Empty Prompts**: Ensure base prompt is set and trinkets are updating
4. **Stale Content**: Verify events are being published and received