# Drag-and-Drop Tools with Synthetic Example Generation

## Overview

MIRA implements a zero-configuration tool system where Python files can be dropped into the `tools/` folder and automatically integrate into the system on next startup. The synthetic example generator analyzes each tool's capabilities, generates training examples through multi-attempt refinement, and trains a classifier for intelligent tool selection.

## Architecture Flow

### 1. Tool Discovery on Startup

When MIRA starts, it scans the `tools/implementations/` directory for Python files:

```python
# tools/relevance_engine/tool_discovery.py:31-55

def discover_tools(self) -> Set[str]:
    discovered_tools = set()

    if os.path.exists(tools_dir):
        for file in os.listdir(tools_dir):
            if file.endswith('_tool.py') and not file.startswith('__'):
                tool_name = file[:-3]
                try:
                    sanitized_name = self._sanitize_tool_name(tool_name)
                    discovered_tools.add(sanitized_name)
                except ValueError as e:
                    self.logger.warning(f"Skipping tool with invalid name '{tool_name}': {e}")

    return discovered_tools
```

### 2. Change Detection with SHA-256 Hashing

The system tracks tool file changes to trigger regeneration only when needed:

```python
# tools/relevance_engine/example_manager.py:90-116

def _handle_synthetic_example_generation(self, tools_needing_examples: List[str], file_hashes: Dict[str, str]) -> bool:
    # Check if tool source file has changed
    tool_file_path = os.path.join(tools_dir, f"{tool_name}.py")
    current_hash = self._calculate_file_hash(tool_file_path)
    old_hash = old_hashes.get(tool_file_path)

    if old_hash != current_hash:
        needs_regen = True
        self._generate_synthetic_examples(tools_needing_examples)
```

### 3. Tool Analysis and Capability Extraction

Each new or modified tool is analyzed by Claude Sonnet to extract distinct capabilities:

```python
# utils/synthetic_toolexample_generator.py:220-289

def analyze_tool(self, tool_path: str) -> ToolAnalysis:
    """
    Analyze a tool file to extract capabilities and metadata.

    Returns ToolAnalysis with broken-down capabilities:
    - tool_name: exact name from code
    - capabilities: list of distinct user-facing functions
    - Each capability has: action_verb, targets, description
    """
    with open(tool_path, 'r') as f:
        source_code = f.read()

    # Use Sonnet to analyze and break down into capabilities
    return ToolAnalysis(
        tool_name=data["tool_name"],
        capabilities=[
            ToolCapability(
                action_verb=cap["action_verb"],
                example_targets=cap["example_targets"]
            ) for cap in data["capabilities"]
        ]
    )
```

### 4. Multi-Attempt Example Generation with Progressive Refinement

The generator creates examples with up to 3 attempts, each building on validation feedback:

```python
# utils/synthetic_toolexample_generator.py:654-750

def generate_with_validation(self, tool_name: str, capability: ToolCapability, count: int = 15) -> List[Dict[str, str]]:
    attempts = []

    # Attempt 1: Initial generation
    examples_v1 = self.generate_capability_examples(tool_name, capability, count)
    validation_v1 = self.validate_examples_comprehensive(examples_v1, capability)

    if validation_v1["passed"]:
        return examples_v1

    # Attempts 2-3: With progressively enhanced feedback
    for attempt_num in range(2, 4):
        # Build feedback from all previous attempts
        feedback = self._create_feedback_for_attempt(attempt_num, previous_validations, capability)

        examples = self.generate_capability_examples(tool_name, capability, count, feedback)
        validation = self.validate_examples_comprehensive(examples, capability)

        if validation["passed"]:
            return examples

    # Select best attempt based on quality + diversity scores
    return self._select_best_attempt(attempts, capability, count)
```

### 5. Comprehensive Validation Pipeline

Each generation attempt undergoes three-layer validation:

```python
# utils/synthetic_toolexample_generator.py:417-488

def validate_examples_comprehensive(self, examples: List[Dict[str, str]], capability: ToolCapability) -> Dict[str, Any]:
    # 1. Token length validation (≤512 tokens for BGE)
    token_result = self._validate_token_length(examples)

    # 2. Semantic diversity analysis using BGE embeddings
    embeddings = self.embeddings_provider.encode_realtime(queries)
    similarities = cosine_similarity(embeddings)
    diversity_score = 1 - mean_similarity

    # 3. Quality validation via LLM
    quality_result = self._validate_quality(examples, capability)

    overall_passed = (
        token_result["passed"] and
        quality_result["passed"] and
        diversity_score > 0.3
    )

    return {
        "passed": overall_passed,
        "feedback": combined_feedback,
        "diversity": diversity_result,
        "tokens": token_result,
        "quality": quality_result
    }
```

### 6. Automatic Tool Registration and Enablement

Tools are registered and enabled based on configuration:

```python
# tools/repo.py:457-551

def discover_tools(self, package_path: str = "tools.implementations") -> None:
    # Scan for Tool subclasses in modules
    for module_info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(module_info.name)

        for attr in dir(module):
            if issubclass(attr, Tool) and attr is not Tool:
                # Register tool class for lazy instantiation
                self.register_tool_class(attr, attr.name)

def enable_tools_from_config(self) -> None:
    if config.tools.auto_discovery:
        self.logger.info("Auto-discovery ON: Enabling all discovered tools")
        self.enable_all_tools()
```

### 7. Classifier Training with Generated Examples

The classification engine trains on all generated examples:

```python
# tools/relevance_engine/tool_relevance_service.py:73-119

def _initialize_system(self) -> None:
    # Step 1: Discover available tools
    discovered_tools = self.tool_discovery.discover_tools()

    # Step 2: Load/generate examples for discovered tools
    tool_examples, needs_retrain = self.example_manager.load_tool_examples_for_tools(discovered_tools)

    # Step 3: Train classifier with examples
    if all_examples:
        self.classification_engine.train_classifier(all_examples, force_retrain=needs_retrain)
        self.classification_engine.precompute_tool_embeddings_matrix()
```

## Key Properties

### Zero Configuration
Drop a `*_tool.py` file into the `tools/` folder. On next startup, MIRA automatically:
- Discovers the tool
- Analyzes its capabilities
- Generates training examples
- Trains the classifier
- Enables the tool

### Self-Improving Generation
The multi-attempt generation with progressive feedback creates increasingly better examples:
- Attempt 1: Initial generation
- Attempt 2: Guided by validation failures
- Attempt 3: Enhanced with all previous feedback
- Final: Best attempt selected by quality + diversity score

### Intelligent Regeneration
SHA-256 hashing ensures examples are only regenerated when:
- Tool source code changes
- Tool is newly added
- Example files are missing

### Semantic Diversity
BGE embeddings measure semantic similarity between examples, ensuring diverse training data that covers the full capability space rather than clustering around similar phrasings.

## Design Rationale

Traditional tool systems require:
- Manual registration in configuration files
- Hand-written training examples
- Explicit integration code
- Redeployment for new tools

MIRA's approach instead:
1. Treats tools as self-describing plugins
2. Generates training data automatically
3. Adapts to tool changes without manual intervention
4. Maintains quality through multi-layer validation
5. Ensures diversity through embedding-based analysis

The result is a system where adding new capabilities is as simple as dropping a Python file into a folder, making the development loop incredibly tight and the system genuinely extensible by non-developers.