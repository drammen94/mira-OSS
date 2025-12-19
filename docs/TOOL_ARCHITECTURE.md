# Tool Architecture

*Technical architecture for MIRA's tool system with detailed implementation examples*

---

## Overview

MIRA's tool system provides a plugin architecture where tools are self-contained components that can access user data, interact with external services, and integrate with the LLM conversation flow. Tools are discovered automatically, registered via configuration, and invoked through a centralized repository.

---

## Base Tool Class

**File**: `tools/repo.py` (lines 40-233)

### Required Implementation

Every tool must inherit from `Tool(ABC)` and implement:

```python
class MyTool(Tool):
    name = "my_tool"
    description = "Human-readable description"
    anthropic_schema = {
        "name": "my_tool",
        "description": "Description for LLM",
        "input_schema": {
            "type": "object",
            "properties": {...},
            "required": [...],
            "additionalProperties": False
        }
    }

    def run(self, **params) -> Dict[str, Any]:
        """Execute the tool with provided parameters."""
        return {"success": True, "data": {...}}
```

### Required Attributes

| Attribute | Type | Purpose |
|-----------|------|---------|
| `name` | `str` | Unique identifier (e.g., "reminder_tool") |
| `description` | `str` | Human-readable description |
| `anthropic_schema` | `Dict` | Anthropic API schema definition |

### Abstract Method

```python
@abstractmethod
def run(self, **params) -> Dict[str, Any]:
    """Execute the tool with provided parameters."""
    raise NotImplementedError("Tool subclasses must implement the run method")
```

### Properties Available to All Tools

| Property | Lines | Purpose |
|----------|-------|---------|
| `user_id` | 100-101 | Current user ID from context |
| `user_data_path` | 103-107 | User-specific data directory |
| `db` | 109-114 | User-scoped database access via `UserDataManager` |
| `logger` | 78 | Pre-configured logger |

### Optional Methods

| Method | Purpose |
|--------|---------|
| `validate_config(config) -> Dict` | Custom config validation (classmethod) |
| `get_dependencies() -> List[str]` | Returns tool dependencies (default: empty list) |
| `get_metadata() -> Dict` | Introspection of run() signature and docstring |
| `get_formatted_description() -> str` | Human-readable metadata |
| `is_available() -> bool` | For gated tools - runtime availability check |

### Built-in File Operations

```python
make_dir(path: str) -> Path
get_file_path(filename: str) -> Path
open_file(filename: str, mode: str) -> file
file_exists(filename: str) -> bool
```

---

## Tool Registration & Discovery

### ConfigRegistry

**File**: `tools/registry.py`

Tools register their configuration via Pydantic models:

```python
from tools.registry import registry
from pydantic import BaseModel, Field

class ReminderToolConfig(BaseModel):
    enabled: bool = Field(default=True, description="...")

registry.register("reminder_tool", ReminderToolConfig)
```

### Auto-Discovery

**File**: `tools/repo.py` (lines 557-595)

```python
ToolRepository.discover_tools(package_path="tools.implementations")
```

**Process**:
1. Uses `pkgutil.iter_modules()` to find all modules in package
2. `_process_module()` inspects each module for concrete Tool subclasses
3. Filters: must have `name` attribute, not abstract, defined in that module
4. Registers class for lazy instantiation via `register_tool_class()`

### Auto-Registration Fallback

When a tool initializes without a registered config, `Tool.__init__()` (lines 77-97) automatically creates a default config using Pydantic's `create_model()`.

---

## Tool Enable/Disable

### Enable Tool (lines 300-317)

```python
ToolRepository.enable_tool(name: str)
```
- Validates tool exists
- Auto-enables dependencies recursively
- Prevents enabling gated tools (they self-determine availability)
- Triggers `_update_tool_guidance()`

### Disable Tool (lines 319-329)

```python
ToolRepository.disable_tool(name: str)
```
- Removes from enabled set
- Triggers `_update_tool_guidance()`

### Gated Tools (lines 281-298)

```python
ToolRepository.register_gated_tool(tool_name: str)
```

Gated tools self-determine availability via `is_available()` method. Checked at invocation time, not pre-enabled. Example: `domaindoc_tool` is available only if enabled domaindocs exist.

### Essential Tools

Configured in `config.tools.essential_tools`. Enabled at startup via `enable_tools_from_config()` (lines 597-620).

---

## Tool Execution Flow

### Invocation (lines 390-444)

```python
ToolRepository.invoke_tool(name: str, params: Dict) -> Dict
```

**Flow**:
1. Validate tool exists (KeyError if not)
2. Check invocability:
   - If enabled: allow
   - If gated: call `is_available()` at runtime
   - Otherwise: RuntimeError
3. Parse params (string JSON → dict)
4. Get fresh tool instance: `get_tool(name)`
5. Call `tool.run(**params)`
6. Publish trinket update to `ToolLoaderTrinket` (if not `invokeother_tool`)
7. Return result dict

### Instance Creation (lines 331-388)

```python
ToolRepository.get_tool(name: str) -> Tool
```

- Creates **new instance per request** (no caching)
- Dependency injection resolves constructor parameters:
  - `LLMBridge` / `LLMProvider`
  - `ToolRepository`
  - `WorkingMemory`

---

## User Data Access

### User Data Path (lines 103-107)

```python
@property
def user_data_path(self) -> Path:
    from utils.userdata_manager import get_user_data_manager
    user_data = get_user_data_manager(self.user_id)
    return user_data.get_tool_data_dir(self.name)
```

**Resulting directory**: `data/users/{user_id}/tools/{tool_name}/`

### Database Access (lines 109-114)

```python
@property
def db(self):
    current_user_id = self.user_id
    if not self._db or self._db.user_id != current_user_id:
        self._db = get_user_data_manager(current_user_id)
    return self._db
```

**Available Methods**:
- `select(table, where_clause=None, params=None) -> List[Dict]`
- `insert(table, data_dict) -> row_id`
- `update(table, update_dict, where_clause, params) -> rows_affected`
- `delete(table, where_clause, params) -> rows_deleted`
- `execute(sql_string, params) -> query_result`
- `create_table(table_name, schema_string)`

All queries automatically scoped by user_id via contextvars + RLS.

---

## Implemented Tools

**Location**: `tools/implementations/`

| Tool Name | File | Purpose |
|-----------|------|---------|
| `reminder_tool` | `reminder_tool.py` | SQLite-based reminders with contact linking |
| `contacts_tool` | `contacts_tool.py` | Contact management (CRUD operations) |
| `pager_tool` | `pager_tool.py` | Virtual pager messaging with federation |
| `web_tool` | `web_tool.py` | Web search (Kagi), fetch, HTTP requests |
| `email_tool` | `email_tool.py` | Email sending/receiving |
| `getcontext_tool` | `getcontext_tool.py` | Access to working memory context |
| `continuum_tool` | `continuum_tool.py` | Access to conversation continuum |
| `domaindoc_tool` | `domaindoc_tool.py` | Domain documentation management |
| `invokeother_tool` | `invokeother_tool.py` | Meta-tool for dynamic tool loading |
| `maps_tool` | `maps_tool.py` | Map/location functionality |
| `kasa_tool` | `kasa_tool.py` | Smart home device control |
| `punchclock_tool` | `punchclock_tool.py` | Time tracking |
| `weather_tool` | `weather_tool.py` | Weather information |

---

## Detailed Tool Examples

### 1. reminder_tool

**File**: `tools/implementations/reminder_tool.py`

**Purpose**: SQLite-based reminder management with contact integration

#### Configuration (lines 40-44)

```python
class ReminderToolConfig(BaseModel):
    enabled: bool = Field(default=True)
registry.register("reminder_tool", ReminderToolConfig)
```

#### Database Schema (lines 124-145)

```sql
reminders table:
- id (TEXT PRIMARY KEY)
- encrypted__title (TEXT NOT NULL)
- encrypted__description (TEXT)
- reminder_date (TEXT NOT NULL - ISO 8601)
- created_at (TEXT NOT NULL)
- updated_at (TEXT NOT NULL)
- completed (INTEGER 0/1)
- completed_at (TEXT)
- contact_uuid (TEXT - FK to contacts.id)
- encrypted__additional_notes (TEXT)
- category (TEXT - "user" or "internal")

Indexes:
- idx_reminders_date
- idx_reminders_completed
- idx_reminders_contact
- idx_reminders_category
```

#### Operations (via `run()`, line 179)

| Operation | Purpose |
|-----------|---------|
| `add_reminder` | Create reminder with optional contact linking |
| `get_reminders` | Query by date_type (today/tomorrow/upcoming/past/all/overdue/date/range) |
| `mark_completed` | Mark reminder done |
| `update_reminder` | Modify existing |
| `delete_reminder` | Remove |

#### Key Implementation Details

**Date parsing** (lines 788-852):
- Handles ISO 8601 format
- Natural language: "tomorrow", "in 3 weeks", "next Monday"
- Timezone conversion: user's local time → UTC for storage

**Contact lookup** (lines 854-881):
- Case-insensitive partial name matching
- Returns contact UUID for linking

**Data access pattern**:
```python
# Create/update/delete
self.db.insert('reminders', reminder)
self.db.update('reminders', update_data, 'id = :id', {'id': reminder_id})
self.db.delete('reminders', 'id = :id', {'id': reminder_id})

# Read
self.db.select('reminders')  # All
self.db.select('reminders', 'completed = 0')  # Filtered
```

---

### 2. contacts_tool

**File**: `tools/implementations/contacts_tool.py`

**Purpose**: Contact management with UUID-based linking

#### Database Schema

```sql
contacts table:
- id (TEXT PRIMARY KEY - UUID)
- encrypted__name (TEXT)
- encrypted__email (TEXT)
- encrypted__phone (TEXT)
- encrypted__street (TEXT)
- encrypted__city (TEXT)
- encrypted__state (TEXT)
- encrypted__zip (TEXT)
- encrypted__pager_address (TEXT)
- created_at (TEXT)
- updated_at (TEXT)
```

#### Operations (via `run()`, line 173)

| Operation | Purpose |
|-----------|---------|
| `add_contact` | Create new contact (unique names, case-insensitive) |
| `get_contact` | Search by UUID or name |
| `list_contacts` | Get all contacts |
| `delete_contact` | Remove (with partial-match confirmation) |
| `update_contact` | Modify fields |
| Batch | `add_contact` with `contacts` JSON array |

#### Identifier Resolution (lines 115-171)

Resolution order:
1. UUID exact match
2. Exact name match (case-insensitive)
3. Name starts-with match
4. Name contains match

**Ambiguity handling**: Returns top 10 matches, requires UUID to disambiguate.

**Duplicate prevention**: Case-insensitive name comparison (lines 248-258).

---

### 3. pager_tool

**File**: `tools/implementations/pager_tool.py`

**Purpose**: Virtual pager messaging with federation, trust management, and location pins

#### Configuration (lines 47-53)

```python
class PagerToolConfig(BaseModel):
    enabled: bool = Field(default=True)
    default_expiry_hours: int = Field(default=24)
    max_message_length: int = Field(default=300)
    ai_distillation_enabled: bool = Field(default=True)
registry.register("pager_tool", PagerToolConfig)
```

#### Database Schema

**pager_devices**:
```sql
- id (TEXT PRIMARY KEY - format: PAGER-XXXX)
- encrypted__name (TEXT)
- encrypted__description (TEXT)
- created_at, last_active (TEXT)
- active (INTEGER 0/1)
- device_secret (TEXT)
- device_fingerprint (TEXT - SHA256 hash)
```

**pager_messages**:
```sql
- id (TEXT PRIMARY KEY - format: MSG-XXXXXXXX)
- sender_id, recipient_id (TEXT - FK to pager_devices)
- encrypted__content, encrypted__original_content (TEXT)
- ai_distilled (INTEGER 0/1)
- priority (INTEGER 0/1/2)
- location (TEXT - JSON)
- sent_at, expires_at (TEXT)
- delivered, read (INTEGER 0/1)
- read_at (TEXT)
- sender_fingerprint, message_signature (TEXT)
```

**pager_trust** (Trust-on-First-Use):
```sql
- id (TEXT PRIMARY KEY)
- trusting_device_id, trusted_device_id (TEXT)
- trusted_fingerprint (TEXT)
- encrypted__trusted_name (TEXT)
- first_seen, last_verified (TEXT)
- trust_status (TEXT - "trusted", "revoked", "conflicted")
```

#### Operations (via `run()`, line 419)

| Operation | Lines | Purpose |
|-----------|-------|---------|
| `register_device` | 518-577 | Create pager device |
| `register_username` | 605-700 | Register federated username |
| `send_message` | 846-999 | Send local or federated message |
| `get_received_messages` | 1001-1084 | Query inbox |
| `get_sent_messages` | 1086-1143 | Query sent messages |
| `mark_message_read` | 1145-1202 | Mark as read |
| `get_devices` | 1204-1236 | List pager devices |
| `deactivate_device` | 1238-1287 | Disable device |
| `cleanup_expired` | 1289-1319 | Remove expired messages |
| `list_trusted_devices` | 1451-1492 | Show TOFU trust store |
| `revoke_trust` | 1494-1527 | Delete trust relationship |
| `send_location` | 1577-1634 | Send location pin |

#### Key Implementation Features

**Dependency Injection**: Constructor takes `LLMProvider` for AI message distillation.

**Trust-on-First-Use (TOFU)** (lines 1359-1449):
- Tracks sender fingerprints
- Detects impersonation attempts
- Maintains trust relationships

**Message Distillation** (lines 1321-1357):
- LLM condenses long messages to `max_message_length`
- Stores original content for reference

**Federated Messaging**:
- Routes to Lattice discovery daemon for cross-server delivery
- Security-bounded write-only access for federation adapter (lines 288-382)

**Message Signature**: HMAC using device secret for authenticity.

---

## Tool Loading Integration

### ToolLoaderTrinket

**File**: `working_memory/trinkets/tool_loader_trinket.py`

**State Management**:
- `available_tools: Dict[str, str]` - Not yet loaded
- `loaded_tools: Dict[str, LoadedToolInfo]` - Currently active
- `essential_tools: List[str]` - Always enabled
- `current_turn: int` - For idle tracking

**Automatic Cleanup**:
- Subscribes to `TurnCompletedEvent`
- Fallback tools: cleaned after 1 turn
- Regular tools: cleaned after `idle_threshold` turns
- Essential tools: never auto-cleaned

### InvokeOtherTool Integration

**Initialization**: Publishes tool hints to ToolLoaderTrinket:
```python
{
    "action": "initialize",
    "available_tools": {tool_name: description, ...},
    "essential_tools": [...]
}
```

**Lifecycle events**:
- `"tool_loaded"` - When tool dynamically loaded
- `"tool_unloaded"` - When tool unloaded
- `"tool_used"` - When tool invoked

### ToolGuidanceTrinket

**File**: `working_memory/trinkets/tool_guidance_trinket.py`

Collects `tool_hints` from enabled tools and formats as XML for LLM guidance.

---

## Anthropic Schema Structure

Every tool defines `anthropic_schema`:

```python
anthropic_schema = {
    "name": "tool_name",
    "description": "Human description for LLM",
    "input_schema": {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "..."},
            "param2": {"type": "integer", "enum": [0, 1, 2]}
        },
        "required": ["param1"],
        "additionalProperties": False
    }
}
```

---

## Tool Configuration via Registry

### Pydantic Config Models

```python
from pydantic import BaseModel, Field

class MyToolConfig(BaseModel):
    enabled: bool = Field(default=True, description="Enable this tool")
    some_setting: int = Field(default=10, description="Custom setting")

registry.register("my_tool", MyToolConfig)
```

### Configuration API

Config accessed via API endpoints for validation and defaults. All tools have at minimum an `enabled: bool` field.

---

## Summary

| Aspect | Implementation |
|--------|----------------|
| **Base Interface** | Abstract `Tool` class, `run(**params) -> Dict` |
| **Registration** | Pydantic `ConfigRegistry` + auto-discovery |
| **Enable/Disable** | `enable_tool()`, `disable_tool()`, gated tools use `is_available()` |
| **User Isolation** | Fresh instance per request, auto-scoped DB via contextvars |
| **Data Storage** | User-scoped SQLite via `self.db`, files via `user_data_path` |
| **Tool Count** | 13 implemented tools |
| **Dynamic Loading** | InvokeOtherTool + ToolLoaderTrinket |
| **Execution** | `ToolRepository.invoke_tool()` → `Tool.run()` |

---

*Implementation: `tools/repo.py` (base class, repository), `tools/registry.py` (configuration), `tools/implementations/` (tool implementations), `working_memory/trinkets/tool_loader_trinket.py` (dynamic loading)*
