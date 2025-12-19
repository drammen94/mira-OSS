# Provider Architecture: Offline & Multi-Provider Support

*Technical architecture for LLM provider abstraction and offline operation*

---

## Overview

MIRA supports multiple LLM providers through a unified abstraction layer. The system is designed to run entirely offline using local models, with automatic failover from cloud providers to local inference when needed.

---

## Supported Providers

### Primary Provider (Default)
- **Anthropic API** (`anthropic`)
  - File: `clients/llm_provider.py` (lines 1-50)
  - Uses native `anthropic.Anthropic` SDK
  - Full feature support including extended thinking, prompt caching

### Third-Party Providers (OpenAI-Compatible)
All routing through `GenericOpenAIClient`:

| Provider | Default Endpoint | Use Case |
|----------|-----------------|----------|
| **Groq** | `https://api.groq.com/openai/v1/chat/completions` | Execution model (fast, simple tools) |
| **OpenRouter** | `https://openrouter.ai/api/v1/chat/completions` | Multi-provider routing |
| **Ollama (Local)** | `http://localhost:11434/v1/chat/completions` | Emergency fallback, offline operation |

### Implementation Files

| File | Purpose |
|------|---------|
| `clients/llm_provider.py` | Main provider interface |
| `utils/generic_openai_client.py` | Third-party adapter |
| `config/config.py` | Provider configuration |

---

## Provider Abstraction

### Unified Interface

**File**: `clients/llm_provider.py`

Application code should ONLY use:

```python
response = llm.generate_response(
    messages=[...],
    endpoint_url="<optional_third_party_endpoint>",  # Routes to generic provider
    model_override="<optional_model_id>",
    api_key_override="<optional_api_key>",
    stream=False
)
```

**Critical Design Principle** (lines 15-50):
> Never instantiate `GenericOpenAIClient` directly. The unified interface handles routing automatically.

### Request Routing (lines 698-748)

```python
def _generate_non_streaming(self, ...):
    if self._is_failover_active():
        # Route to emergency fallback
        endpoint_url = config.api.emergency_fallback_endpoint
        model_override = config.api.emergency_fallback_model
        api_key_override = self.emergency_fallback_api_key

    if endpoint_url:  # Generic provider
        generic_client = GenericOpenAIClient(...)
        return generic_client.messages.create(...)
    else:  # Anthropic
        message = self.anthropic_client.beta.messages.create(...)
```

### GenericOpenAIClient Adapter

**File**: `utils/generic_openai_client.py` (lines 78-144)

- Accepts Anthropic-format inputs
- Converts to OpenAI format (lines 254-362)
- Returns Anthropic-compatible response objects (lines 364-429)

---

## Emergency Failover Mechanism

### Configuration

**File**: `config/config.py` (lines 42-47)

```python
class ApiConfig(BaseModel):
    emergency_fallback_enabled: bool = True
    emergency_fallback_endpoint: str = "http://localhost:11434/v1/chat/completions"
    emergency_fallback_api_key_name: Optional[str] = None  # Local providers need no key
    emergency_fallback_model: str = "qwen3:1.7b"
    emergency_fallback_recovery_minutes: int = 5
```

### Activation (lines 873-889)

```python
except anthropic.APIError as e:
    if self.emergency_fallback_enabled:
        self.logger.error(f"Anthropic error: {e} - activating emergency failover")
        self._activate_failover()
        # Disable thinking for fallback providers (not supported)
        kwargs['thinking_enabled'] = False
        return self._generate_non_streaming(
            messages, tools,
            endpoint_url=config.api.emergency_fallback_endpoint,
            model_override=config.api.emergency_fallback_model,
            api_key_override=self.emergency_fallback_api_key,
            **kwargs
        )
```

### Class-Level Failover State (lines 205-371)

```python
_failover_active = False  # Shared across all LLMProvider instances
_failover_lock = threading.Lock()
_recovery_timer: Optional[threading.Timer] = None

@classmethod
def _activate_failover(cls):
    """Activate emergency failover for all users."""
    with cls._failover_lock:
        cls._failover_active = True
        # Schedule recovery test in 5 minutes

@classmethod
def _test_recovery(cls):
    """Clear failover flag to retry Anthropic."""
    with cls._failover_lock:
        cls._failover_active = False
```

**Behavior**:
- When Anthropic fails, **all traffic routes to fallback** (global state)
- Recovery tested every 5 minutes (configurable)
- Failover checked at lines 689, 900 before making requests

---

## Offline Operation

### Requirements for Running Without Internet

1. **Local LLM Model** (Ollama or compatible)
   - Default: `qwen3:1.7b` (lightweight, ~2GB)
   - Must be running on `http://localhost:11434`

2. **No API Keys Required**
   - Anthropic key: Only needed if using Anthropic
   - Fallback key: Optional (line 259-266)
   - Groq key: Only for execution model (optional)

3. **Local Embeddings** (local-first architecture)
   - File: `clients/hybrid_embeddings_provider.py`
   - Uses `MongoDB/mdbr-leaf-ir-asym` (768-dim, local inference)
   - No remote API calls required

4. **Database Requirements**
   - PostgreSQL (required, not cloud-dependent)
   - Valkey for caching (optional)

### Code Evidence (lines 254-272)

```python
self.emergency_fallback_enabled = config.api.emergency_fallback_enabled
self.emergency_fallback_api_key = None

if self.emergency_fallback_enabled:
    # API key is OPTIONAL for local providers like Ollama
    if config.api.emergency_fallback_api_key_name:
        try:
            self.emergency_fallback_api_key = get_api_key(...)
        except Exception as e:
            self.logger.warning(f"Failed to get emergency fallback API key: {e}")

    # Log fallback configuration
    if self.emergency_fallback_api_key:
        self.logger.info(f"Emergency fallback enabled with API key")
    else:
        self.logger.info(f"Emergency fallback enabled (no API key - local provider)")
```

---

## Provider Feature Parity

### Features That Work Across All Providers

| Feature | Support |
|---------|---------|
| Text generation | All providers |
| Tool definitions | Converted for OpenAI format |
| System prompts | Converted to OpenAI format |
| Max tokens / temperature | All providers |
| Response streaming | Anthropic only |

### Features NOT Supported on Generic Providers

**Code reference** (lines 728-748):

```python
# Filter out code_execution (Anthropic server-side tool)
filtered_tools = [tool for tool in tools if tool.get("type") != "code_execution_20250825"]

# Strip cache_control from remaining tools
generic_tools = [{k: v for k, v in tool.items() if k != "cache_control"} for tool in filtered_tools]

# Strip container_upload blocks from messages (Files API not supported)
messages = self._strip_container_uploads_from_messages(messages)
```

| Feature | Anthropic | Generic Providers |
|---------|-----------|-------------------|
| Extended Thinking | ✓ | ✗ Disabled automatically |
| Prompt Caching | ✓ | ✗ Stripped |
| Files API | ✓ | ✗ `container_upload` blocks stripped |
| Code Execution | ✓ | ✗ Filtered out |
| Streaming | ✓ | ✗ Non-streaming only |
| Thinking Blocks | ✓ | ✗ Stripped during conversion |

### Format Conversion (generic_openai_client.py)

| Anthropic Format | OpenAI Format | Generic Support |
|------------------|---------------|-----------------|
| `text` block | `content: str` | ✓ |
| `tool_use` block | `tool_calls` array | ✓ Converted |
| `tool_result` block | `role: "tool"` message | ✓ Converted |
| `thinking` block | N/A | ✗ Stripped |
| `cache_control` | N/A | ✗ Stripped |

---

## Provider Configuration

### Configuration Parameters

**File**: `config/config.py` (lines 14-57)

```python
# Main model (reasoning)
model: str = "claude-opus-4-5-20251101"

# Execution model (fast, simple tools)
execution_model: str = "openai/gpt-oss-20b"
execution_endpoint: str = "https://api.groq.com/openai/v1/chat/completions"
execution_api_key_name: str = "groq_key"
simple_tools: List[str] = ["reminder_tool", "punchclock_tool", ...]

# Fingerprint/analysis (memory retrieval queries)
analysis_enabled: bool = True
analysis_endpoint: str = "https://api.groq.com/openai/v1/chat/completions"
analysis_model: str = "openai/gpt-oss-20b"

# Emergency fallback
emergency_fallback_enabled: bool = True
emergency_fallback_endpoint: str = "http://localhost:11434/v1/chat/completions"
emergency_fallback_model: str = "qwen3:1.7b"
```

### Dynamic Model Selection (lines 914-922)

```python
def _select_model(self, last_response):
    """Dynamic model selection based on last tool usage."""
    if last_response and last_response.stop_reason == "tool_use":
        tool_names = {b.name for b in last_response.content if b.type == "tool_use"}
        if tool_names and tool_names.issubset(self.simple_tools):
            return self.execution_model  # Fast model for simple tools

    return self.model  # Default to reasoning model
```

---

## Streaming Implementation

### Anthropic Streaming (lines 891-1100)

```python
def _stream_response(self, messages, tools, ...):
    """Native streaming using Anthropic SDK."""
    with self.anthropic_client.beta.messages.stream(...) as stream:
        for event in stream:
            if event.type == "text":
                yield TextEvent(content=event.text)
            elif event.delta.type == "thinking_delta":
                yield ThinkingEvent(content=event.delta.thinking)
            elif event.content_block.type == "tool_use":
                yield ToolDetectedEvent(...)

        final_message = stream.get_final_message()
        yield CompleteEvent(response=final_message)
```

### Stream Event Types

**File**: `cns/core/stream_events.py`

| Event | Purpose |
|-------|---------|
| `TextEvent` | Text content chunks |
| `ThinkingEvent` | Extended thinking blocks |
| `ToolDetectedEvent` | Tool detection |
| `ToolExecutingEvent` | Tool execution start |
| `ToolCompletedEvent` | Tool execution result |
| `ToolErrorEvent` | Tool execution error |
| `CircuitBreakerEvent` | Chain termination |
| `CompleteEvent` | Final response |
| `ErrorEvent` | Errors |
| `RetryEvent` | Retry attempts |

### Generic Provider Streaming (lines 698-748)

Generic providers use non-streaming with response conversion:
```python
return generic_client.messages.create(...)
# Then emit events synthetically in _emit_events_from_response()
```

---

## Batch API

### Anthropic Batch API Only

**File**: `lt_memory/batching.py` (lines 33-88)

```python
class BatchingService:
    def __init__(self, ..., anthropic_client: anthropic.Anthropic, ...):
        self.anthropic_client = anthropic_client

    def _submit_extraction_batch(self, user_id, chunks):
        batch = self.anthropic_client.beta.messages.batches.create(
            requests=requests
        )
```

**Uses**:
- Memory extraction
- Relationship classification

**Configuration** (`config/config.py`, lines 248-299):
```python
class BatchingConfig(BaseModel):
    api_key_name: str = "anthropic_batch_key"  # Separate from chat API
    batch_expiry_hours: int = 24
    segment_chunk_size: int = 40  # Messages per chunk
    max_retry_count: int = 3
    relationship_model: str = "claude-3-5-haiku-20241022"
```

### Generic Providers & Batch

**NOT SUPPORTED** - No batch API implementation for Groq, OpenRouter, Ollama, or local models. Batch operations require Anthropic API.

---

## API Key Management

**File**: `clients/vault_client.py`

- Requires HashiCorp Vault for credential storage
- API keys retrieved at initialization (not per-request)
- Environment variables: `VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID`

---

## Usage Examples

### Fingerprint Generation

**File**: `cns/services/fingerprint_generator.py` (lines 143-150)

```python
response = self.llm_provider.generate_response(
    messages=[{"role": "user", "content": user_message}],
    stream=False,
    endpoint_url=self.config.analysis_endpoint,
    model_override=self.config.analysis_model,
    api_key_override=self.api_key,
    system_override=self.system_prompt
)
```

### Execution Model Routing

**File**: `cns/services/llm_service.py` (lines 43-83)

```python
response = self.llm_provider.generate_response(
    messages=complete_messages,
    tools=tools,
    stream=stream,
    callback=stream_callback,
    **kwargs
)
```

---

## Offline Architecture Summary

**MIRA can run entirely offline with:**

1. ✅ Local Ollama instance running `qwen3:1.7b` (or compatible)
2. ✅ PostgreSQL database (local or remote)
3. ✅ No Anthropic API key required (automatic fallback)
4. ✅ No internet connection required for LLM inference after failover

**Architecture ensures graceful degradation:**
```
Primary (Anthropic) → Third-party (Groq/OpenRouter) → Local (Ollama)
```

**Feature degradation on fallback:**
- Extended thinking disabled
- Streaming disabled
- Batch operations unavailable
- Core conversation functionality preserved

---

*Implementation: `clients/llm_provider.py` (main interface), `utils/generic_openai_client.py` (adapter), `config/config.py` (configuration), `lt_memory/batching.py` (batch operations)*
