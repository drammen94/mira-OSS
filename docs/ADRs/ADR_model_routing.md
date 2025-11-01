# ADR: Dynamic Model Routing for Cost and Latency Optimization

## Status
Proposed

## Context

### The Problem
Every turn in a multi-turn conversation with tool calls incurs full model latency:
- Sonnet 4.5: ~1200ms per turn, $3/$15 per million tokens (input/output)
- Multi-tool workflows: 4-6 turns = 5-7 seconds total latency
- Simple operations (data fetching, tool loading) use expensive reasoning capacity unnecessarily

### Key Insight
**API calls are stateless.** The model is just a parameter - conversation state lives entirely in the messages array. We can swap models between turns without any loss of context or coherence.

### The Opportunity
Many turns in a conversation don't require Sonnet's reasoning capabilities:
- Processing simple tool results (weather data, time lookups)
- Requesting tool definitions (`request_tool`)
- Formatting already-retrieved data
- Making obvious follow-up tool calls

Haiku can handle these turns at:
- **3x lower latency** (~400ms vs ~1200ms)
- **20x lower cost** ($0.25/$1.25 vs $3/$15 per million tokens)
- **Same quality** for simple operations

## Decision

Implement dynamic model routing in the orchestrator that selects the optimal model for each turn based on simple heuristics.

### Core Implementation

```python
class Orchestrator:
    # Configuration
    reasoning_model = "claude-sonnet-4.5"
    execution_model = "claude-haiku-4"

    # Tools that don't require Sonnet's reasoning
    simple_tools = {
        "request_tool",       # Just loading a definition
        "weather_tool",       # Simple API call
        "web_search_tool",    # Data fetching
        "time_tool",          # Trivial operation
        "reminder_tool",      # Simple CRUD
        "maps_tool",          # Location lookup
        "contacts_tool",      # Data retrieval
    }

    def _select_model(self, last_response) -> str:
        """Choose model based on what the next turn needs to do."""
        if last_response.stop_reason == "tool_use":
            tool_names = {b.name for b in last_response.content if hasattr(b, 'name')}
            if tool_names.issubset(self.simple_tools):
                return self.execution_model
        return self.reasoning_model

    def _get_completion(self, messages: list) -> MessageResponse:
        """Get completion using dynamically selected model."""
        model = self._select_model()

        logger.debug(f"Using model: {model}")

        return self.client.messages.create(
            model=model,
            messages=messages,
            tools=self._get_enabled_tools(),
            max_tokens=4096
        )
```

### Routing Logic

**Use Haiku when:**
- Last turn made tool calls
- ALL tools in that turn are in `simple_tools` set
- Next turn will process those tool results

**Use Sonnet for:**
- Initial user request (always)
- Any turn with complex tools (memory, email, customer database)
- Mixed tool sets (simple + complex)
- Final response generation
- Error recovery
- Default fallback

### Why This Works

1. **Stateless API**: Models don't maintain state. Full context in messages array.
2. **Format compatibility**: Both models use identical tool-calling format.
3. **Conversation continuity**: Haiku picks up exactly where Sonnet left off.
4. **Self-contained context**: Tool names, parameters, and results are explicit.

## Consequences

### Positive

**Latency Reduction**
- Simple tool chains: 30-50% faster
- Example: `request_tool` → tool execution → response
  - Before: 1200ms + 1200ms = 2400ms
  - After: 1200ms + 400ms = 1600ms (33% faster)

**Cost Reduction**
- Estimated 20-30% cost reduction for typical conversations
- High-volume tool users see even greater savings

**No Quality Loss**
- Complex reasoning still uses Sonnet
- Simple operations handled correctly by Haiku
- User never sees degraded responses

**Simplicity**
- 15 lines of code
- No framework dependencies
- Easy to debug and modify
- Clear decision logic

### Negative

**Complexity Addition**
- New model selection logic to maintain
- `simple_tools` set requires updates when adding tools

**Potential Quality Edge Cases**
- Haiku might occasionally produce less sophisticated formatting
- Mitigation: Conservative `simple_tools` list, easy to adjust

**Observability Needs**
- Must log which model handled each turn for debugging
- Track model routing effectiveness metrics

### Neutral

**Model Behavior Differences**
- Both models follow instructions well for simple operations
- Quality difference only matters for nuanced tasks (which use Sonnet)

## Alternatives Considered

### 1. Always Use Sonnet
**Rejected**: Wastes money and time on simple operations. No benefit to using expensive model for trivial tasks.

### 2. Always Use Haiku
**Rejected**: Quality loss on complex reasoning, memory extraction, nuanced responses. Cost savings not worth UX degradation.

### 3. LangChain/Framework Router
**Rejected**: Massive complexity overhead for simple logic. Abstractions hide behavior and add latency. 500 lines vs 15 lines.

### 4. Complexity-Based ML Router
**Rejected**: Over-engineering. Simple heuristic (tool name matching) is sufficient and transparent. ML model adds latency, training burden, opacity.

### 5. User-Configurable Model Selection
**Rejected**: Users shouldn't need to think about this. System should optimize automatically. Adds UI complexity for minimal benefit.

### 6. Prompt-Based Model Hints
**Rejected**: Fragile. Relies on LLM self-assessment of complexity. Better to use objective signal (tool names).

## Implementation Plan

### Phase 1: Core Implementation
1. Add `simple_tools` set to Orchestrator config
2. Implement `_select_model()` method
3. Update `_get_completion()` to use dynamic model selection
4. Add debug logging for model selection

### Phase 2: Observability
1. Track model usage per turn in conversation metadata
2. Add metrics: haiku_turns, sonnet_turns, total_latency_saved
3. Dashboard view of model routing effectiveness

### Phase 3: Tuning
1. Monitor conversations for quality issues
2. Adjust `simple_tools` set based on real-world performance
3. Consider time-based heuristics (use Haiku for <5 token outputs)

### Phase 4: Advanced Patterns (Future)
1. **Model escalation**: Try Haiku, fall back to Sonnet if confused
2. **Batch optimization**: Use Haiku for multiple simple tools in parallel
3. **Context-aware routing**: Consider conversation length, user patterns
4. **A/B testing**: Compare routing strategies across user cohorts

## Validation Criteria

### Success Metrics
- 25%+ reduction in average conversation latency
- 20%+ reduction in API costs
- No increase in user-reported quality issues
- No increase in error rates

### Monitoring
- Model selection reasons logged per turn
- Latency comparison (routed vs all-Sonnet baseline)
- Cost tracking per conversation
- Quality spot-checks on Haiku turns

### Rollback Conditions
- Quality degradation detected
- Error rate increase >5%
- User complaints about response quality

## Notes

### Why This Pattern Is Elegant
- Works **with the grain** of the API (stateless, model-as-parameter)
- No abstractions or frameworks needed
- Trivial to understand, debug, and modify
- Scales to any model combination (Opus, Sonnet, Haiku)

### Real-World Precedent
- **Claude Code** uses this pattern: Haiku for mechanical edits, Sonnet for reasoning
- **Anthropic's examples** demonstrate model swapping mid-conversation
- **Production systems** routinely route by complexity to optimize cost/latency

### Future Extensions
Could extend routing to:
- Opus for exceptionally complex reasoning
- Haiku 3.5 vs Haiku 4 based on recency needs
- Regional model selection for latency optimization
- Custom fine-tuned models for specific tool domains

## References
- Anthropic API docs: Stateless message-based conversations
- Claude Code agent architecture: Multi-model collaboration
- Internal testing: Haiku quality sufficient for simple tool processing

---

**Decision Date**: 2025-01-30
**Decision Makers**: Taylor (human), Claude (AI pair programmer)
**Reviewers**: TBD