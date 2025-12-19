# CLI Interface

*Technical documentation for MIRA's command-line interfaces*

---

## Overview

MIRA provides CLI interfaces for interactive chat, server management, and administrative operations. The primary CLI is a Rich-based terminal chat application that communicates with the FastAPI backend.

---

## Primary CLI: talkto_mira.py

**File**: `talkto_mira.py` (551 lines)

### Entry Point (lines 506-544)

```python
def main():
    parser = argparse.ArgumentParser(description="MIRA Chat")
    parser.add_argument('--headless', type=str, help="One-shot message")
    parser.add_argument('--show-key', action='store_true', help="Display API key and exit")
```

### Usage Modes

| Mode | Command | Purpose |
|------|---------|---------|
| Interactive | `python talkto_mira.py` | REPL chat interface |
| Headless | `python talkto_mira.py --headless "message"` | One-shot query, returns JSON |
| Show Key | `python talkto_mira.py --show-key` | Display API key for curl/API usage |

### Interactive Slash Commands (lines 405-451)

| Command | Action |
|---------|--------|
| `/tier [fast\|balanced\|nuanced]` | Set LLM model tier preference |
| `/status` | Show current tier setting |
| `/clear` | Clear conversation history |
| `/help` | Display help information |
| `quit`, `exit`, `bye` | Exit the CLI |

---

## Server Entry Point: main.py

**File**: `main.py` (lines 474-546)

### Command-Line Arguments

```python
parser = argparse.ArgumentParser(description='MIRA - AI Assistant with persistent memory')
parser.add_argument('--firehose', action='store_true',
                   help='Enable firehose mode: log all LLM API calls for debugging')
```

| Command | Purpose |
|---------|---------|
| `python main.py` | Start FastAPI server |
| `python main.py --firehose` | Enable LLM API call logging to `firehose_output.json` |

### Firehose Mode (lines 478-486)

```python
config.system._firehose_enabled = args.firehose
if args.firehose:
    logger.info("Firehose mode enabled - LLM API calls will be logged to firehose_output.json")
```

---

## Admin/Developer Scripts

### Memory Operations

**File**: `junk_drawer/run_memory_consolidation.py`

```bash
python run_memory_consolidation.py extract      # Run extraction/consolidation
python run_memory_consolidation.py refine       # Run memory refinement
python run_memory_consolidation.py consolidate  # Run similarity-based consolidation
  --user ID                                     # Specific user ID
  --similarity 0.85                             # Similarity threshold (default 0.85)
  --stable-days 7                               # Skip new memories (default 7 days)
  --max-clusters 10                             # Max clusters to process (default 10)
  --force                                       # Process all memories regardless of age
  --verbose                                     # Show detailed output
```

### Entity Garbage Collection

**File**: `junk_drawer/run_entity_gc.py`

```bash
python run_entity_gc.py --dry-run               # Preview only
python run_entity_gc.py --user-id <uuid>        # Specific user
python run_entity_gc.py --all-users             # All users
  --dormancy-days 60                            # Custom dormancy threshold
```

### User Management Scripts

**Location**: `scripts/`

| Script | Purpose |
|--------|---------|
| `get_magic_link.py <email>` | Generate magic link token for user login |
| `query_user_messages.py <email>` | Debug user messages and continuums |
| `create_user_with_magic_link.py <email>` | Create new user account |
| `import_contacts.py <user_id> <file>` | Import contacts to encrypted DB |
| `migrate_schemas.py` | Database schema migrations |
| `migrate_domaindocs.py` | Domain document migrations |

---

## CLI Implementation

### Framework

- **Argument parsing**: `argparse` (Python standard library)
- **Terminal UI**: `Rich` library for panels, colored text, formatting

### Interactive Chat REPL

**Function**: `chat_loop()` (lines 375-471)

**Features**:
- Real-time rendering with Rich panels
- Animated thinking indicator (`ThinkingAnimation` class, lines 313-361)
- Window resize handling via `SIGWINCH` signal (line 386-387)
- Splash screen with ASCII animation on startup (lines 166-233)
- Status bar showing current tier

**Input handling**:
```python
user_input = console.input("[cyan]>[/cyan] ")
```

**History tracking**:
```python
history: List[Tuple[str, str]] = []  # [(user_msg, mira_msg), ...]
```

---

## Authentication

### Token Retrieval

**Flow** (lines 487-503, 533-539):

```python
def show_api_key() -> None:
    """Display the MIRA API key and exit."""
    token = get_api_key('mira_api')  # Fetches from Vault
    print(f"\nYour MIRA API Key: {token}\n")

# In main():
try:
    token = get_api_key('mira_api')
except Exception as e:
    console.print(f"[red]Failed to get API token: {e}[/red]")
    sys.exit(1)
```

### Token Source

- **HashiCorp Vault** via `clients.vault_client.get_api_key()`
- Requires environment variables: `VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID`
- Fails fast if Vault unavailable or token doesn't exist

---

## CLI-to-API Interaction

### Communication Pattern

1. CLI retrieves Bearer token from Vault
2. Makes HTTP POST requests to FastAPI server
3. Receives JSON responses
4. Renders with Rich formatting

### API Endpoints Used

| Endpoint | Method | Purpose | Line |
|----------|--------|---------|------|
| `/v0/api/chat` | POST | Send message, get response | 55 |
| `/v0/api/actions` | POST | Execute system actions | 69 |
| `/v0/api/health` | GET | Check server status | 116 |

### Request Functions

**Send message** (lines 54-65):
```python
def send_message(token: str, message: str) -> dict:
    url = f"{MIRA_API_URL}/v0/api/chat"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json={"message": message}, timeout=REQUEST_TIMEOUT)
```

**Call action** (lines 68-75):
```python
def call_action(token: str, domain: str, action: str, data: dict = None) -> dict:
    url = f"{MIRA_API_URL}/v0/api/actions"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json={
        "domain": domain, "action": action, "data": data or {}
    }, timeout=10)
```

### Action Examples

**Get tier** (lines 94-99):
```python
call_action(token, "continuum", "get_llm_tier")
```

**Set tier** (lines 102-107):
```python
call_action(token, "continuum", "set_llm_tier", {"tier": tier})
```

### Response Handling (lines 464-469)

```python
if result.get("success"):
    response = strip_emotion_tag(result.get("data", {}).get("response", ""))
    history.append((user_input, response))
else:
    error = result.get("error", {}).get("message", "Unknown error")
    history.append((user_input, f"Error: {error}"))
```

---

## Auto-Server Spawning

The CLI can automatically start the FastAPI server if not running.

### Implementation (lines 122-137, 199-200)

- Uses subprocess to launch `main.py`
- Waits for server readiness with health check polling
- Registers shutdown handler at exit (line 529)

### Health Check

```python
response = requests.get(f"{MIRA_API_URL}/v0/api/health", timeout=2)
```

---

## Configuration

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `MIRA_API_URL` | API endpoint | `http://localhost:1993` |
| `VAULT_ADDR` | Vault server address | Required |
| `VAULT_ROLE_ID` | AppRole ID for Vault | Required |
| `VAULT_SECRET_ID` | AppRole secret for Vault | Required |

### Timeouts

| Operation | Timeout |
|-----------|---------|
| Chat requests | 120 seconds |
| Action requests | 10 seconds |
| Health checks | 2 seconds |

---

## CLI vs Web Interface

| Feature | CLI | Web |
|---------|-----|-----|
| Send messages | ✓ (interactive & headless) | ✓ |
| View tier | ✓ (`/status`) | ✓ |
| Change tier | ✓ (`/tier`) | ✓ |
| Clear history | ✓ (`/clear`) | ✓ |
| Memory extraction | ✓ (admin script) | ✗ |
| Memory consolidation | ✓ (admin script) | ✗ |
| Memory refinement | ✓ (admin script) | ✗ |
| Entity GC | ✓ (admin script) | ✗ |
| Contact import | ✓ (script) | ✗ |
| Magic link generation | ✓ (script) | ✗ |
| User debugging | ✓ (query script) | ✗ |
| Server startup | ✓ (auto-spawn) | N/A |
| API key retrieval | ✓ (`--show-key`) | ✗ |
| Firehose debugging | ✓ (server flag) | ✗ |

---

## REPL Features

### What's Implemented

- Interactive chat with Rich panels
- Animated thinking indicator during LLM processing
- Window resize handling
- Splash screen on startup
- Conversation history (current session)
- Slash command processing

### What's NOT Implemented

- Command history persistence across sessions
- Tab completion
- Syntax highlighting
- Local variable inspection
- Code execution (pure chat interface)

---

## Headless Mode

### Usage

```bash
python talkto_mira.py --headless "What's the weather today?"
```

### Output Format

Returns JSON response directly to stdout, suitable for scripting:

```json
{
  "success": true,
  "data": {
    "response": "...",
    "metadata": {...}
  }
}
```

---

## Summary

| Aspect | Implementation |
|--------|----------------|
| **Primary CLI** | `talkto_mira.py` - Rich-based terminal chat |
| **Framework** | `argparse` + `Rich` library |
| **Authentication** | Bearer tokens from HashiCorp Vault |
| **API Communication** | HTTP POST/GET to FastAPI endpoints |
| **Admin Tools** | Standalone Python scripts for database operations |
| **Server Control** | Auto-spawn capability, `--firehose` debugging |
| **Interactive Features** | Slash commands, animated thinking, status display |

---

*Implementation: `talkto_mira.py` (main CLI), `main.py` (server entry), `scripts/` (admin utilities), `junk_drawer/` (memory operations)*
