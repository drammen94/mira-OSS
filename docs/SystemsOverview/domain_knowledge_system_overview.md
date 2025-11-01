# Domain Knowledge System Overview

**Status**: Core implementation complete, pending integration and testing
**Created**: 2025-10-13
**Purpose**: Enable context-specific memory blocks that users can selectively enable/disable

---

## What Are Domain Knowledge Blocks?

Domain knowledge blocks are user-controlled, context-specific memory containers that inject persistent knowledge into MIRA's system prompt. Unlike working memory (which surfaces relevant past conversations) or long-term memory (which stores factual memories), domain blocks represent **domain-specific knowledge that remains active while the user is in that context**.

### Example Use Cases

**"Work" Domain Block**
- Active during work hours
- Contains: Current project context, team member names, TPS report format preferences, recurring meeting schedules
- When enabled: MIRA knows about your work projects, teammates, and professional context
- When disabled: Work context doesn't pollute personal conversations

**"Michigan Trip Planning" Domain Block**
- Active while planning a specific trip
- Contains: Travel dates, destinations, budget constraints, restaurant recommendations, packing lists
- When enabled: MIRA helps with trip logistics, remembers your preferences
- When disabled: Trip context removed once planning is complete

**"Home Automation" Domain Block**
- Active when managing smart home
- Contains: Device names, room layouts, automation routines, energy preferences
- When enabled: MIRA knows your device topology and can help control your home
- When disabled: Smart home context not injected

---

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         MIRA                                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  System Prompt Builder                                  │ │
│  │  • Base system prompt                                   │ │
│  │  • Working memory trinkets                              │ │
│  │  • Domain knowledge trinket ← Injects enabled blocks    │ │
│  └────────────────────────────────────────────────────────┘ │
│                           ↓                                  │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Conversation Orchestrator                              │ │
│  │  • Processes user messages                              │ │
│  │  • Buffers messages for domain blocks (every 10 msgs)   │ │
│  │  • Generates responses                                  │ │
│  └────────────────────────────────────────────────────────┘ │
│                           ↓                                  │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Domain Knowledge Service                               │ │
│  │  • Manages block lifecycle (create/enable/disable)      │ │
│  │  • Batches messages (10 at a time)                      │ │
│  │  • Sends batches to Letta → Sleeptime agents update     │ │
│  │  • Fetches updated blocks for injection                 │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                           ↓
              ┌────────────────────────┐
              │    Letta Cloud API     │
              │  (Background Service)  │
              │                        │
              │  Sleeptime Agents:     │
              │  • user_123:work       │
              │  • user_123:trip       │
              │  • user_456:work       │
              │  Automatically update  │
              │  blocks based on msgs  │
              └────────────────────────┘
```

### Letta's Role

Letta operates as a **background service** - it's not visible to users and doesn't control MIRA's experience. Letta provides:

1. **Sleeptime Agents**: Background workers that analyze conversation transcripts and update memory blocks
2. **Block Storage**: Structured memory blocks with labels, descriptions, and character limits
3. **Smart Updates**: Agents use LLMs to consolidate, organize, and update block content intelligently

MIRA remains in full control:
- User creates/enables/disables blocks through MIRA's UI
- MIRA decides when blocks inject into prompts
- MIRA batches and sends messages to Letta
- User data stays in MIRA's database

---

## User Experience

### Creating a Domain Block

```
User: "I want to create a domain knowledge block for my work context"

MIRA calls: POST /actions
{
  "domain": "domain_knowledge",
  "action": "create",
  "data": {
    "domain_label": "work",
    "domain_name": "Work",
    "block_description": "Professional context including current projects, teammates, and work preferences"
  }
}

Result: Domain block created, disabled by default
```

### Enabling a Domain Block

```
User: "Enable my work domain"

MIRA calls: POST /actions
{
  "domain": "domain_knowledge",
  "action": "enable",
  "data": {
    "domain_label": "work"
  }
}

Result:
- Block enabled in database
- DomainKnowledgeTrinket injects block into system prompt
- Messages start buffering for Letta updates
```

### Automatic Learning (Background)

As the user conversations with MIRA (with Work domain enabled):

```
Message 1: "I'm working on the authentication refactor today"
Message 2: "The deadline is next Friday"
...
Message 10: "Sarah approved the PR"

→ MIRA buffers all 10 messages
→ After message 10, batch sent to Letta's work sleeptime agent
→ Agent analyzes messages and updates Work block:
   "Current project: Authentication refactor
    Deadline: [specific date]
    Team: Sarah (approver)
    Status: PR approved"
```

Next conversation, the updated block is in MIRA's system prompt automatically.

### Disabling a Domain Block

```
User: "Disable work domain"

MIRA:
1. Flushes any buffered messages to Letta (don't lose updates)
2. Marks block as disabled in database
3. Block no longer injected into system prompt

Result: Work context gone from future conversations
```

---

## Message Batching Strategy

Messages are sent to Letta in batches for efficiency:

**Triggers for sending batch:**
1. **Every 10 messages** (user + assistant pairs count as 2)
2. **When domain disabled** (flush remaining buffer)
3. **When domain deleted** (flush remaining buffer)

**Format sent to Letta:**
```json
[
  {"role": "user", "content": "I'm working on the auth refactor"},
  {"role": "assistant", "content": "Great, what's the current status?"},
  {"role": "user", "content": "PR is ready for review"},
  ...
]
```

Letta's sleeptime agent processes the batch and updates the block asynchronously.

---

## System Prompt Injection

When a domain block is enabled, it's injected into the system prompt:

```xml
<domain_knowledge>
Context-specific knowledge blocks currently active:

<work description="Professional context including current projects, teammates, and work preferences">
Current project: Authentication refactor using OAuth2
Team members: Sarah (tech lead), Mike (backend), Jessica (frontend)
Deadline: October 20, 2025
Status: PR #247 approved, pending merge
Code style: Type hints required, pytest for all public methods
Meeting schedule: Daily standup at 9 AM, sprint planning Mondays
</work>

</domain_knowledge>
```

This appears in MIRA's context for every message while the block is enabled.

---

## Technical Components

### 1. Service Layer
**File**: `cns/services/domain_knowledge_service.py`

**Responsibilities:**
- Wrap letta-client SDK
- Manage block lifecycle (CRUD operations)
- Buffer messages for batching
- Flush buffers to Letta API
- Retrieve block content for injection

**Key Methods:**
- `create_domain_block()` - Create new domain + sleeptime agent
- `enable_domain()` - Enable block (start buffering messages)
- `disable_domain()` - Disable block (flush buffer first)
- `buffer_message()` - Add message to buffer, auto-flush at 10
- `get_block_content()` - Retrieve current block content from Letta

### 2. Database Schema
**File**: `scripts/create_domain_knowledge_schema.sql`

**Table**: `domain_knowledge_blocks`
```sql
CREATE TABLE domain_knowledge_blocks (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    domain_label VARCHAR(100) NOT NULL,  -- e.g., "work", "trip"
    domain_name VARCHAR(255) NOT NULL,   -- e.g., "Work", "Michigan Trip"
    block_description TEXT NOT NULL,
    agent_id VARCHAR(255) NOT NULL,      -- Letta agent ID
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, domain_label)
);
```

### 3. System Prompt Integration
**File**: `working_memory/trinkets/domain_knowledge_trinket.py`

**Responsibilities:**
- Fetch enabled domain blocks for user
- Format as XML for system prompt injection
- Integrate with working memory event system

**Generated Content:**
```xml
<domain_knowledge>
Context-specific knowledge blocks currently active:

<work>...</work>
<trip>...</trip>
</domain_knowledge>
```

### 4. Actions API
**File**: `cns/api/actions.py`

**Endpoints:**
```
POST /actions
{
  "domain": "domain_knowledge",
  "action": "create|enable|disable|delete|list|get",
  "data": {...}
}
```

**Actions:**
- `create` - Create new domain block
- `enable` - Enable block (inject into prompt)
- `disable` - Disable block (remove from prompt)
- `delete` - Delete block and agent
- `list` - Get all blocks (enabled + disabled)
- `get` - Get specific block content

---

## Integration Points

### Required Integration Steps

**1. Database Migration**
```bash
psql -U mira_admin -d mira_app -f scripts/create_domain_knowledge_schema.sql
```

**2. Register Trinket** (in `working_memory/core.py` or initialization file)
```python
from working_memory.trinkets.domain_knowledge_trinket import DomainKnowledgeTrinket

# During working memory initialization
domain_knowledge_trinket = DomainKnowledgeTrinket(event_bus, working_memory)
```

**3. Hook into Orchestrator** (in `cns/services/orchestrator.py`)
```python
from cns.services.domain_knowledge_service import get_domain_knowledge_service

# After processing message
domain_service = get_domain_knowledge_service()
if domain_service:
    domain_service.buffer_message(user_id, "user", user_message)
    domain_service.buffer_message(user_id, "assistant", assistant_response)
```

**4. Add Letta API Key to Vault**
```python
from clients.vault_client import set_secret
set_secret("letta_api_key", "letta_<your_api_key>")
```

---

## Configuration

### Environment Variables
None required - API key stored in Vault

### Constants
```python
# In domain_knowledge_service.py
BATCH_SIZE = 10  # Messages before flush
```

### Letta Configuration
- **Model**: `openai/gpt-4o-mini` (fast, cheap for block updates)
- **Agent Type**: `sleeptime_agent`
- **Block Limit**: 10,000 characters per block

---

## Usage Examples

### Example 1: Work Context

```python
# Create work domain
POST /actions
{
  "domain": "domain_knowledge",
  "action": "create",
  "data": {
    "domain_label": "work",
    "domain_name": "Work",
    "block_description": "Professional context: projects, teammates, preferences"
  }
}

# Enable work domain
POST /actions
{
  "domain": "domain_knowledge",
  "action": "enable",
  "data": {"domain_label": "work"}
}

# Have 10+ conversations about work...
# Block updates automatically in background

# Disable when done working for the day
POST /actions
{
  "domain": "domain_knowledge",
  "action": "disable",
  "data": {"domain_label": "work"}
}
```

### Example 2: Trip Planning

```python
# Create trip domain
POST /actions
{
  "domain": "domain_knowledge",
  "action": "create",
  "data": {
    "domain_label": "michigan_trip",
    "domain_name": "Michigan Trip Planning",
    "block_description": "Travel plans: dates, destinations, budget, activities"
  }
}

# Enable while planning
POST /actions
{
  "domain": "domain_knowledge",
  "action": "enable",
  "data": {"domain_label": "michigan_trip"}
}

# Plan trip with MIRA...
# Block learns: dates, hotels, restaurants, packing list

# After trip is booked, disable
POST /actions
{
  "domain": "domain_knowledge",
  "action": "disable",
  "data": {"domain_label": "michigan_trip"}
}

# Later: delete the domain entirely
POST /actions
{
  "domain": "domain_knowledge",
  "action": "delete",
  "data": {"domain_label": "michigan_trip"}
}
```

---

## Benefits

**1. Context Separation**
- Work context doesn't pollute personal conversations
- Trip planning context removed when done
- Clean separation between life domains

**2. Persistent Domain Knowledge**
- MIRA remembers project names, teammate names
- No need to re-explain context each conversation
- Knowledge persists across sessions

**3. User Control**
- Enable/disable domains on demand
- Delete domains when no longer relevant
- Full transparency into what's active

**4. Automatic Learning**
- No manual memory management
- Letta agents intelligently consolidate information
- Blocks stay organized and up-to-date

**5. Scalability**
- Multiple domains per user
- Domains don't interfere with each other
- Low overhead (10k char limit per block)

---

## Limitations & Considerations

**1. Cloud Dependency**
- Requires Letta API key
- Block updates happen via Letta cloud
- Alternative: Extract Letta OSS components (future work)

**2. Update Latency**
- Blocks update asynchronously (not real-time)
- 10-message batching means slight delay
- Acceptable for domain knowledge (not time-critical)

**3. Block Size Limits**
- 10,000 characters per block
- Letta agents auto-condense when approaching limit
- Should be sufficient for most domain contexts

**4. Cost**
- Letta API calls (likely minimal with gpt-4o-mini)
- Batch updates keep costs low
- Monitor Letta usage dashboard

---

## Future Enhancements

**1. OSS Extraction**
- Extract Letta's block + sleeptime agent code
- Run locally with Ollama
- Full offline capability

**2. Manual Block Editing**
- UI for users to manually edit block content
- Useful for corrections or additions
- API already supports `get` action for retrieval

**3. Block Templates**
- Pre-built templates for common domains
- "Work", "Project", "Event Planning", etc.
- Quick setup with reasonable defaults

**4. Block Sharing**
- Share domain blocks between users
- Team-wide "Project X" knowledge block
- Requires multi-user block ownership

**5. Block Analytics**
- Show block update frequency
- Display when blocks were last updated
- Transparency into learning behavior

---

## Testing Plan

### Unit Tests
- Block CRUD operations
- Message buffering logic
- Flush triggers (10 messages, disable, delete)

### Integration Tests
- Create domain → send messages → verify Letta call
- Enable domain → verify prompt injection
- Disable domain → verify buffer flush + removal

### End-to-End Test
1. Create "test_domain" block
2. Enable block
3. Send 10 messages
4. Verify Letta API called with batched messages
5. Verify block content updated
6. Verify block injected into system prompt
7. Disable block
8. Verify block no longer in prompt
9. Delete block
10. Verify agent deleted from Letta

---

## Implementation Status

### ✅ Complete
- [x] Domain knowledge service with Letta integration
- [x] Message buffering (batch size: 10)
- [x] Database schema
- [x] Domain knowledge trinket
- [x] Actions API endpoints
- [x] Auto-flush on disable/delete

### 🚧 Pending
- [ ] Run database migration
- [ ] Register trinket in working memory
- [ ] Hook orchestrator to buffer messages
- [ ] Add Letta API key to Vault
- [ ] End-to-end testing

### 📋 Future
- [ ] Manual block editing UI
- [ ] Block templates
- [ ] OSS extraction for offline support
- [ ] Block analytics dashboard

---

## Questions & Decisions

**Q: Why Letta instead of building custom?**
A: Letta's sleeptime agents already solve the "intelligently update memory blocks" problem. Building custom would require prompting, consolidation logic, and ongoing maintenance. Letta provides this as a service.

**Q: Why 10-message batching?**
A: Balance between update frequency and API cost. Too frequent = expensive. Too infrequent = stale blocks. 10 messages ≈ 5 conversation turns is reasonable.

**Q: Can users see what's in blocks?**
A: Yes, via `get` action. Future: add UI to display/edit block content.

**Q: What if Letta API is down?**
A: Messages buffer in memory. Next batch attempt retries. Blocks remain enabled but won't update until Letta recovers. Non-critical since blocks are supplementary context.

**Q: Can blocks reference each other?**
A: Not currently, but possible future enhancement. Each block is independent.

---

## References

- [Letta AI Memory SDK](https://github.com/letta-ai/letta/tree/main/examples/ai-memory-sdk)
- [LOOSEENDS_PUNCHLIST.md](../LOOSEENDS_PUNCHLIST.md) - Domain knowledge task
- [Domain Knowledge Service](../cns/services/domain_knowledge_service.py)
- [Domain Knowledge Trinket](../working_memory/trinkets/domain_knowledge_trinket.py)
- [Actions API](../cns/api/actions.py)
