# RouteLLM Integration Guide: Intelligent Model Routing for MIRA

## Overview

RouteLLM enables intelligent routing between language models based on query complexity, dramatically reducing costs while maintaining response quality. This guide covers integrating RouteLLM into MIRA, with a specific focus on routing between thinking and non-thinking model variants.

## The Core Problem

```
    ┌─────────────────────────────────────────────────────────┐
    │                    Cost vs Quality Dilemma              │
    ├─────────────────────────────────────────────────────────┤
    │                                                         │
    │  Powerful Models (Thinking/Opus)      $$$$$ 🧠🧠🧠🧠🧠  │
    │  ├── Excellent for complex tasks                        │
    │  └── Expensive for simple queries                       │
    │                                                         │
    │  Efficient Models (Instruct/Haiku)    $     🧠         │
    │  ├── Perfect for simple tasks                          │
    │  └── Insufficient for complex reasoning                 │
    │                                                         │
    │  Current Approach: Always use powerful model           │
    │  Result: 🔥 Burning money on "What's 2+2?" 🔥         │
    └─────────────────────────────────────────────────────────┘
```

## RouteLLM Solution Architecture

```
                        ┌─────────────────┐
                        │  User Query     │
                        └────────┬────────┘
                                 │
                        ┌────────▼────────┐
                        │  Query Router   │
                        │  (Classifier)   │
                        └────┬───────┬────┘
                             │       │
                    Complex? │       │ Simple?
                             │       │
                   ┌─────────▼───┐ ┌─▼──────────┐
                   │  Thinking    │ │  Instruct  │
                   │   Model      │ │   Model    │
                   │              │ │            │
                   │ "Let me      │ │ "The       │
                   │  think..."   │ │  answer    │
                   │              │ │  is..."    │
                   └─────────┬────┘ └─┬──────────┘
                             │        │
                        ┌────▼────────▼───┐
                        │    Response     │
                        │   (Same Quality)│
                        │   (Lower Cost) │
                        └─────────────────┘
```

## How RouteLLM Works

### 1. Query Analysis

The router examines incoming queries for complexity indicators:

```
┌─────────────────────────────────────────────────────────────┐
│                    Complexity Indicators                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  🧮 Mathematical Reasoning                                   │
│     "Prove that...", "Derive...", "Calculate complex..."    │
│                                                              │
│  🔧 Multi-Step Problem Solving                              │
│     "Design a system...", "Plan a strategy..."              │
│                                                              │
│  📊 Deep Analysis                                           │
│     "Compare and contrast...", "Evaluate tradeoffs..."      │
│                                                              │
│  💻 Complex Code Generation                                 │
│     "Implement algorithm...", "Debug this complex..."        │
│                                                              │
│  VS                                                          │
│                                                              │
│  📌 Simple Lookups                                          │
│     "What is...", "Define...", "List..."                    │
│                                                              │
│  🔢 Basic Calculations                                      │
│     "What's 15% of...", "Convert units..."                  │
│                                                              │
│  📝 Straightforward Tasks                                   │
│     "Summarize briefly...", "Translate..."                  │
└─────────────────────────────────────────────────────────────┘
```

### 2. Routing Decision Flow

```
Query Input
    │
    ▼
┌─────────────────┐
│ Feature Extract │──┐
└─────────────────┘  │
                     │    ┌──────────────────┐
                     ├───►│ Length Analysis  │
                     │    └──────────────────┘
                     │    ┌──────────────────┐
                     ├───►│ Keyword Matching │
                     │    └──────────────────┘
                     │    ┌──────────────────┐
                     └───►│ Pattern Detection│
                          └──────────────────┘
                                   │
                          ┌────────▼────────┐
                          │   Classifier    │
                          │  (Trained Model) │
                          └────────┬────────┘
                                   │
                          ┌────────▼────────┐
                          │ Confidence Score │
                          │   0.0 - 1.0     │
                          └────────┬────────┘
                                   │
                              Threshold
                               (0.5)
                              ┌───┴───┐
                              │       │
                         <0.5 │       │ ≥0.5
                              │       │
                        ┌─────▼─┐   ┌─▼──────┐
                        │Instruct│   │Thinking│
                        └────────┘   └────────┘
```

## Training Process Overview

### Phase 1: Data Collection

```
┌─────────────────────────────────────────────────────┐
│              Preference Data Pipeline                │
├─────────────────────────────────────────────────────┤
│                                                      │
│  1. Query Pool        2. Parallel Generation        │
│  ┌──────────┐        ┌─────────┐  ┌─────────┐     │
│  │"Explain  │───────►│Thinking │  │Instruct │     │
│  │quantum..."│        │ Model   │  │  Model  │     │
│  └──────────┘        └────┬────┘  └────┬────┘     │
│                            │             │          │
│                      3. Responses        │          │
│                      ┌─────▼─────┐ ┌────▼────┐    │
│                      │"Let me    │ │"Quantum  │    │
│                      │think about│ │computing │    │
│                      │this step  │ │uses..."  │    │
│                      │by step..."│ └──────────┘    │
│                      └───────────┘                  │
│                            │             │          │
│                      4. Judgment (Sonnet)          │
│                      ┌─────────────────┐           │
│                      │  Which response  │           │
│                      │  better serves   │           │
│                      │  the user need?  │           │
│                      └────────┬────────┘           │
│                               │                     │
│                      5. Training Label              │
│                      ┌────────▼────────┐           │
│                      │ Thinking Wins: 1 │           │
│                      │ Instruct Wins: 0 │           │
│                      └─────────────────┘           │
└─────────────────────────────────────────────────────┘
```

### Phase 2: Classifier Training

The classifier learns patterns from preference data:

```
Training Data                     Learned Patterns
─────────────                     ────────────────

"Prove that..." → 1               Complex reasoning → Thinking
"What is..." → 0                  Simple lookup → Instruct
"Design a..." → 1                 Creative/Planning → Thinking
"List 5..." → 0                   Enumeration → Instruct
"Debug this..." → 1               Problem solving → Thinking
"Translate..." → 0                Basic task → Instruct

                    ┌─────────────┐
                    │  Classifier │
                    │   Learns:   │
                    │ • Keywords  │
                    │ • Patterns  │
                    │ • Context   │
                    └─────────────┘
```

## Integration with MIRA

### Architecture Integration Points

```
┌─────────────────────────────────────────────────────────┐
│                      MIRA System                         │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐         ┌─────────────┐              │
│  │ Orchestrator │────────►│ LLMProvider │              │
│  └──────────────┘         └──────┬──────┘              │
│                                   │                      │
│                           ┌───────▼────────┐            │
│                           │  RouteDecider  │ NEW!       │
│                           └───────┬────────┘            │
│                                   │                      │
│                    ┌──────────────┴──────────────┐      │
│                    │                             │      │
│              ┌─────▼──────┐            ┌────────▼───┐   │
│              │  Thinking   │            │  Instruct  │   │
│              │   Client    │            │   Client   │   │
│              └─────────────┘            └────────────┘   │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │                Tool Ecosystem                   │    │
│  │  • Works identically with both model types      │    │
│  │  • No changes needed to existing tools          │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

### Cost Optimization Profile

```
                Cost Savings Visualization

    100% ┤ ████ Current (Always Thinking)
         │ ████
     80% ┤ ████
         │ ████
     60% ┤ ████ ░░░░
         │ ████ ░░░░ After RouteLLM
     40% ┤ ████ ░░░░ (40-60% reduction)
         │ ████ ░░░░
     20% ┤ ████ ░░░░
         │ ████ ░░░░
      0% └─────────────────────────────
           Cost  Quality

    ░ = Savings  █ = Expense  ✓ = Maintained
```

## Implementation Strategy

### Phase 1: Foundation
1. Set up RouteLLM dependency
2. Create router configuration schema
3. Build preference data collection pipeline

### Phase 2: Training
1. Collect 5,000-10,000 preference examples
2. Use Sonnet as automated judge
3. Train classifier on collected data
4. Validate on held-out test set

### Phase 3: Integration
1. Add router to LLMProvider
2. Implement streaming compatibility
3. Add cost tracking metrics
4. Deploy with conservative threshold

### Phase 4: Optimization
1. Monitor routing decisions
2. Collect user feedback
3. Retrain with production data
4. Adjust thresholds based on metrics

## Performance Expectations

```
┌──────────────────────────────────────────────────────┐
│              Expected Outcomes                        │
├──────────────────────────────────────────────────────┤
│                                                       │
│  Query Type          │ Routing │ Cost  │ Quality     │
│  ────────────────────┼─────────┼───────┼────────────│
│  "What's 2+2?"       │Instruct │  -95% │  Same      │
│  "List colors"       │Instruct │  -95% │  Same      │
│  "Explain recursion" │Thinking │   0%  │  Optimal   │
│  "Design system"     │Thinking │   0%  │  Optimal   │
│  "Debug complex code"│Thinking │   0%  │  Optimal   │
│  "Translate hello"   │Instruct │  -95% │  Same      │
│                                                       │
│  Overall Impact:                                      │
│  • 40-60% cost reduction                             │
│  • <5% quality degradation                           │
│  • 100% tool compatibility                           │
│  • Transparent to end users                          │
└──────────────────────────────────────────────────────┘
```

## Key Considerations

### When to Use Thinking Model
- Multi-step reasoning required
- Complex problem decomposition
- Mathematical proofs
- System design and architecture
- Deep code analysis
- Ethical or philosophical questions

### When to Use Instruct Model
- Factual lookups
- Simple calculations
- Basic translations
- List generation
- Format conversions
- Straightforward summaries

### Edge Cases
The router should err on the side of quality:
- Ambiguous complexity → Route to thinking
- User explicitly requests reasoning → Override to thinking
- High-stakes decisions → Route to thinking
- Time-sensitive simple queries → Route to instruct

## Future Enhancements

1. **Multi-Model Routing**: Expand beyond binary to route among 3+ models
2. **Dynamic Thresholds**: Adjust routing based on user preferences
3. **Feedback Loop**: Learn from user satisfaction signals
4. **Cost Budgets**: Per-user or per-session cost targets
5. **Explanation Mode**: Show users why a particular model was chosen

## Conclusion

RouteLLM integration provides intelligent, cost-effective model selection while maintaining response quality. By training a classifier on preference data judged by Sonnet, MIRA can automatically route queries to the most appropriate model, achieving significant cost savings without compromising user experience.

The key is starting with quality preference data and conservative thresholds, then optimizing based on real-world usage patterns. This approach ensures that complex queries still receive the deep reasoning they require while simple queries avoid unnecessary computational expense.