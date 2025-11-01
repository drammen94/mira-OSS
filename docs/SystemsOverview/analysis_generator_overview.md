# Analysis Generator System Overview

## What is the Analysis Generator?

The Analysis Generator is MIRA's pre-processing service that solves the "one-turn-behind" problem in conversational AI memory systems. It runs a fast LLM call before the main response to generate an evolved "semantic touchstone" - a contextual anchor that bridges past conversation history with the current user input. This touchstone enables accurate, contextually-relevant memory retrieval by predicting what memories will be needed BEFORE the main model runs, similar to branch prediction in modern CPUs.

## The One-Turn-Behind Problem

### Traditional Memory Retrieval Problem
In conventional conversational AI systems with memory:
1. User sends message → System searches memories based on previous state
2. System generates response → Extracts new context for next turn
3. Result: Memory retrieval is always one turn behind the actual conversation

### Example Scenario
```
Turn 1: User: "I'm working on that Python script"
        AI: [Searches memories with previous context, finds nothing relevant]
        Response: "What Python script are you working on?"

Turn 2: User: "The API integration one we discussed yesterday"
        AI: [NOW searches for "Python script" memories - one turn late!]
        Response: "Ah yes, the script with the async issues..."
```

### The Solution: Predictive Touchstone Generation
The Analysis Generator breaks this cycle by:
1. Running a fast model (llama-3.1-8b) BEFORE memory retrieval
2. Generating a touchstone that predicts the semantic context
3. Using this touchstone to retrieve relevant memories immediately
4. Passing enriched context to the main model

## Architecture Overview

### Core Components

```
User Message Arrives
    ↓
Analysis Generator (Fast Model)
    ↓
Touchstone Generation
    ↓
Memory Retrieval (Using Touchstone)
    ↓
Main Model (With Relevant Memories)
    ↓
Response
```

### File Structure
```
cns/services/
├── analysis_generator.py      # Main touchstone generation service
├── memory_relevance_service.py # Uses touchstone for memory search
└── orchestrator.py            # Coordinates the pipeline

config/prompts/
├── analysis_generation_system.txt  # System prompt for touchstone generation
└── analysis_generation_user.txt    # User prompt template

utils/
└── tag_parser.py              # Extracts touchstone from responses
```

## The Touchstone Concept

### What is a Touchstone?
A touchstone is a structured semantic summary that captures:
- **Narrative**: The conversation's flow and emotional tone
- **Entities**: Key people, technologies, concepts (current + recurring)
- **Relationship Context**: What MIRA has learned about the user
- **Temporal Context**: Time-based continuity markers
- **Conversational Intent**: What the user is trying to accomplish
- **Semantic Hooks**: Variations for connecting to memories

### Touchstone Structure
```json
{
  "narrative": "User is debugging the async Python script we worked on yesterday, frustrated with race conditions in the API integration.",
  "entities": "Python async/await, API integration, race conditions, debugging session, yesterday's work",
  "relationship_context": "User is a developer who prefers practical examples, has been struggling with async concepts, works on integration projects",
  "temporal_context": "continuing from yesterday's debugging session",
  "conversational_intent": "resolve the race condition issue in their API integration script",
  "semantic_hooks": ["async debugging patterns", "API race conditions", "Python concurrency issues"]
}
```

### Evolution Across Turns
Touchstones evolve to maintain continuity:
- Each turn builds on the previous touchstone
- Preserves relationship understanding
- Tracks conversation arc
- Maintains entity context

## Core Implementation

### 1. **Analysis Generator** (`cns/services/analysis_generator.py`)

The main service for touchstone generation:

**Key Methods**:
- `generate_analysis()`: Main entry point called by orchestrator
- `_build_context_messages()`: Extracts recent conversation pairs
- `_format_continuum_context()`: Formats context for analysis

**Processing Pipeline**:
1. Extract previous touchstone from continuum metadata
2. Build context from recent message pairs (configurable count)
3. Call fast model with specialized prompts
4. Parse JSON response (with repair fallback)
5. Generate embedding for touchstone
6. Store in continuum metadata for next turn

**Features**:
- Uses fast model for low latency (< 1 second typical)
- Robust JSON parsing with repair capability
- Embedding generation for future similarity search
- Graceful degradation on failure

### 2. **Memory Relevance Service Integration**

How touchstone enables memory retrieval:

```python
# In orchestrator.py
touchstone = self.analysis_generator.generate_analysis(continuum, text_for_context)

# Touchstone is required for memory retrieval
if touchstone:
    surfaced_memories = self.memory_relevance_service.get_relevant_memories(
        embedded_msg,
        touchstone=touchstone
    )
```

**Benefits**:
- Memories are contextually relevant to current input
- Semantic hooks improve retrieval precision
- Relationship context ensures personalization
- No lag in memory relevance

### 3. **Continuum Integration**

Touchstones are stored in continuum metadata:

```python
# Storage
continuum.set_last_touchstone(touchstone, embedding_list)

# Retrieval for evolution
previous_touchstone = continuum.last_touchstone
```

**Persistence**:
- Touchstone stored in continuum metadata
- Embedding stored for future similarity operations
- Survives cache expiration
- Enables cross-session continuity

## Configuration

Key settings in `config.api`:
```python
analysis_enabled: bool = True              # Toggle analysis generation
analysis_endpoint: str = "groq.com/..."    # Fast inference endpoint
analysis_model: str = "llama-3.1-8b"       # Fast model selection
analysis_context_pairs: int = 5            # Recent conversation pairs
analysis_timeout: int = 10                 # Seconds before timeout
```

## Prompts

### System Prompt (`analysis_generation_system.txt`)
- Defines the touchstone generator role
- Explains importance for memory retrieval
- Provides detailed field definitions
- Includes examples and quality guidelines

### User Prompt (`analysis_generation_user.txt`)
- Template with previous narrative placeholder
- Conversation turns placeholder
- Strict output requirements

## Event Flow

### Complete Processing Pipeline
```
1. User message arrives at Orchestrator
   ↓
2. Analysis Generator called with continuum + message
   ↓
3. Previous touchstone retrieved from metadata
   ↓
4. Context built from recent message pairs
   ↓
5. Fast model generates evolved touchstone
   ↓
6. Touchstone + embedding stored in continuum
   ↓
7. Memory Relevance Service uses touchstone
   ↓
8. Relevant memories retrieved with semantic hooks
   ↓
9. Enriched context passed to main model
   ↓
10. Response generated with full context awareness
```

### Timing Comparison
```
Without Analysis Generator:
T0: User message → Search (wrong context) → Few/no relevant memories
T1: Generate response → Extract context → Store for NEXT turn
T2: Next message → NOW have right context (one turn late!)

With Analysis Generator:
T0: User message → Generate touchstone (300ms) → Search (right context) → Relevant memories → Contextual response
T1: Next message → Generate touchstone (300ms) → Search (right context) → Relevant memories → Contextual response
```

## Key Design Principles

### 1. **Predictive Context Generation**
- Don't wait for main model to extract context
- Predict semantic needs before expensive operations
- Trade small latency for massive relevance gain

### 2. **Structured Semantic Representation**
- Not just keywords but relationship understanding
- Multiple facets (narrative, entities, intent, etc.)
- Semantic hooks for retrieval flexibility

### 3. **Evolutionary Design**
- Each touchstone builds on previous
- Maintains conversation continuity
- Preserves relationship context

### 4. **Graceful Degradation**
- System continues without touchstone
- JSON repair for malformed responses
- Timeout protection

### 5. **Performance Optimization**
- Fast model for low latency
- Parallel memory search
- Cached touchstones in metadata

## Benefits

1. **Eliminates One-Turn-Behind**: Memories are always contextually relevant
2. **Maintains Relationship Continuity**: Touchstone evolution preserves understanding
3. **Improved Memory Precision**: Semantic hooks and multi-faceted search
4. **Low Latency**: Fast model adds < 500ms typically
5. **Ambient Awareness**: Every response colored by relationship understanding
6. **Future-Proof**: Touchstone embeddings enable continuum similarity search

## Integration Points

### With CNS Orchestrator
- Called early in pipeline before memory retrieval
- Blocks memory search if no touchstone
- Provides touchstone to memory service

### With Memory Systems
- Touchstone passed to ProactiveService
- Semantic hooks guide retrieval
- Intent informs ranking

### With Continuum
- Touchstone stored in metadata
- Retrieved for evolution
- Persists across sessions

### With Embeddings
- 384-dim embedding generated
- Enables future similarity search
- Stored alongside touchstone

## Future Enhancements

As noted in the code comments, touchstone embeddings enable:

### Continuum Similarity Search
```sql
-- Find continuums about similar topics
SELECT * FROM continuums
WHERE touchstone_embedding <=> query_embedding < 0.3
ORDER BY touchstone_embedding <=> query_embedding;
```

### Applications
1. **Continuum Clustering**: Group related conversations
2. **Cross-Continuum Memory**: "What have we discussed about X?"
3. **Continuum Recommendations**: Surface related past conversations
4. **Topic Evolution Tracking**: How interests change over time

## Example Walkthrough

### Turn 1: Initial Context Building
```
User: "Can you help me with my Python script?"

Analysis Generator:
- Previous touchstone: None (first exchange)
- Generates: {
    "narrative": "User requesting help with a Python script, no prior context",
    "entities": "Python programming, script assistance",
    "semantic_hooks": ["Python debugging", "code assistance", "script problems"]
  }

Memory Search: Finds general Python help memories

Response: "I'd be happy to help with your Python script. What specific issue are you encountering?"
```

### Turn 2: Context Evolution
```
User: "It's the async API integration we worked on yesterday"

Analysis Generator:
- Previous touchstone: Has "Python script" context
- Evolves to: {
    "narrative": "User returning to async API integration script from yesterday's debugging session",
    "entities": "Python async/await, API integration, yesterday's debugging session",
    "temporal_context": "continuing from yesterday",
    "semantic_hooks": ["async API debugging", "yesterday's integration work", "Python concurrency"]
  }

Memory Search: Immediately finds yesterday's debugging session memories!

Response: "Ah yes, the script where we were debugging the race condition in the token refresh logic. Have you tried the asyncio.Lock approach we discussed?"
```

## Performance Characteristics

- **Latency**: 200-500ms typical (fast model + network)
- **Success Rate**: >95% touchstone generation
- **Memory Relevance**: 3-5x improvement in precision
- **Context Window**: Minimal overhead (500 tokens)
- **Scalability**: Stateless, horizontally scalable

## Troubleshooting

### Common Issues

1. **No Touchstone Generated**
   - Check API key configuration
   - Verify endpoint accessibility
   - Check analysis_enabled setting

2. **Poor Memory Retrieval**
   - Verify touchstone quality
   - Check semantic hooks
   - Ensure embeddings generated

3. **High Latency**
   - Monitor model endpoint performance
   - Consider timeout settings
   - Check context size (analysis_context_pairs)

4. **JSON Parse Errors**
   - Enable debug logging
   - Check for markdown fences
   - Verify json_repair availability