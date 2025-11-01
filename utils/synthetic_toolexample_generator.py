"""
Synthetic Tool Example Generator for MIRA

HOW IT WORKS:
This module generates high-quality training examples for AI tool classification using a sophisticated
capability-based approach designed for BGE-M3 embeddings with 8192 token context.

ARCHITECTURAL OVERVIEW:
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Tool Analysis  │ -> │ Capability Gen  │ -> │ Validation &    │ -> │ Output Files    │
│  (Sonnet-4)     │    │ (Sonnet-4 + BGE)│    │ Retry Logic     │    │ (JSON by cap)   │
└─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────┘

DETAILED WORKFLOW:

1. TOOL ANALYSIS PHASE:
   - Uses Claude Sonnet-4 to analyze Python tool files
   - Extracts distinct capabilities (e.g., "turn on", "turn off", "dim brightness")
   - Identifies target objects each capability acts on
   - Creates structured ToolAnalysis with capabilities broken down by action verbs

2. PARALLEL CAPABILITY PROCESSING:
   - Each capability processed independently with ThreadPoolExecutor
   - Uses Claude Sonnet-4 with extended thinking for creative generation
   - Generates 15 examples per capability by default
   - Focuses on domain-specific, actionable commands

3. MULTI-LAYER VALIDATION SYSTEM:
   a) Token Length Validation:
      - Uses BGE tokenizer to ensure ≤512 tokens per example
      - Restarts entire generation if violations found
   
   b) Semantic Diversity Analysis:
      - Uses BGE embeddings to calculate cosine similarity
      - Measures diversity score (1 - mean_similarity)
      - Flags high-similarity pairs (>0.7 similarity)
   
   c) Quality Validation via LLM:
      - Claude Sonnet-4 validates actionability and domain-specificity
      - Checks for generic vs specific language
      - Ensures examples clearly invoke the target capability

4. ADAPTIVE RETRY LOGIC:
   - Up to 3 attempts per capability with progressive feedback
   - Attempt 1: Initial generation
   - Attempt 2: Guided feedback from validation failures
   - Attempt 3: Enhanced feedback combining all previous attempts
   - Selects best attempt based on combined quality+diversity score

5. OUTPUT GENERATION:
   - Saves examples grouped by capability in separate JSON files
   - Creates summary file with statistics and file manifest
   - Provides detailed logging of validation metrics

KEY FEATURES:
- Dual LLM strategy: Sonnet for analysis, Opus for generation (Now using Sonnet. Vastly cheaper and same quality.)
- Token-aware generation preventing BGE embedding truncation
- Comprehensive validation ensuring training data quality
- Parallel processing for efficiency with sequential fallback
- Detailed logging and metrics for monitoring generation quality

OPTIMIZATION FOR BGE EMBEDDINGS:
- Biases toward concise examples (5-10 words ideal)
- Validates token counts using actual BGE tokenizer
- Ensures semantic diversity to prevent embedding collapse
- Domain-specific language to improve classification accuracy
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from clients.llm_provider import LLMProvider
from clients.vault_client import get_api_key
from clients.hybrid_embeddings_provider import get_hybrid_embeddings_provider

logger = logging.getLogger(__name__)


def cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray:
    """
    Compute pairwise cosine similarity between embeddings.

    Args:
        embeddings: Array of shape (n_samples, n_features)

    Returns:
        Similarity matrix of shape (n_samples, n_samples)
    """
    # Normalize embeddings to unit vectors
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / norms

    # Compute pairwise dot products (cosine similarity for normalized vectors)
    return np.dot(normalized, normalized.T)


@dataclass
class ToolCapability:
    """A specific capability/function of a tool."""
    name: str
    action_verb: str
    description: str
    example_targets: List[str]


@dataclass
class ToolAnalysis:
    """Complete analysis of a tool's functionality broken down by capabilities."""
    tool_name: str
    primary_purpose: str
    capabilities: List[ToolCapability]
    target_users: List[str]
    common_scenarios: List[str]


class SyntheticToolExampleGenerator:
    """
    Generates focused conversational examples for tool classification.
    
    Each capability is processed independently to create high-quality,
    token-efficient training data optimized for BGE embeddings.
    """
    
    def __init__(self):
        """Initialize with separate LLMs for analysis and generation."""
        # Get API key from vault
        api_key = get_api_key('anthropic_key')
        
        # Sonnet for fast, accurate code analysis
        self.analyzer = LLMProvider(
            model="claude-sonnet-4-20250514",
            api_key=api_key,
            timeout=300
        )

        # Opus for superior creative generation with extended thinking
        self.generator = LLMProvider(
            model="claude-sonnet-4-20250514", # claude-opus-4-20250514 used to be Opus but it was like $5 per tool gen lol
            temperature=1.0,
            max_tokens=8000,
            api_key=api_key,
            timeout=300
        )
        
        # Use unified BGE-M3 embeddings provider
        self.embeddings_provider = get_hybrid_embeddings_provider()
        # BGE-M3 supports 8192 tokens, no need for strict 512 token limit anymore
        
        logger.info("Initialized with Sonnet analyzer, Opus generator, and unified BGE-M3 embeddings")
    
    def _clean_json_response(self, response_text: str) -> str:
        """Clean LLM response by removing markdown code blocks.
        
        Args:
            response_text: Raw response text from LLM
            
        Returns:
            Cleaned text ready for JSON parsing
        """
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]  # Remove ```json
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text[3:]  # Remove ```
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]  # Remove trailing ```
        return cleaned_text.strip()
    
    def _parse_llm_json_response(self, response_text: str, context: str = "LLM response") -> Dict[str, Any]:
        """Parse and validate JSON from LLM response with comprehensive error handling.
        
        Args:
            response_text: Raw response text from LLM
            context: Context description for error messages
            
        Returns:
            Parsed JSON data
            
        Raises:
            ValueError: If response is empty or JSON parsing fails
        """
        if not response_text.strip():
            logger.error(f"{context}: LLM returned empty response")
            raise ValueError(f"{context}: LLM returned empty response")
        
        try:
            cleaned_text = self._clean_json_response(response_text)
            return json.loads(cleaned_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse {context}: {e}")
            logger.debug(f"Raw response: {response_text[:500]}...")
            logger.error(f"{context} parsing failed: {e}")
            raise ValueError(f"{context} parsing failed: {e}") from e
    
    def _generate_and_parse_response(self, messages: List[Dict], llm_provider, context: str, extra_body: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate LLM response and parse JSON with unified error handling.
        
        Args:
            messages: Messages for LLM
            llm_provider: LLM provider instance (analyzer or generator)
            context: Context description for errors
            extra_body: Optional extra parameters for generation
            
        Returns:
            Parsed JSON response
            
        Raises:
            ValueError: If generation or parsing fails
        """
        try:
            if extra_body:
                response = llm_provider.generate_response(messages, extra_body=extra_body)
            else:
                response = llm_provider.generate_response(messages)
            
            response_text = llm_provider.extract_text_content(response)
            return self._parse_llm_json_response(response_text, context)
            
        except ValueError:
            raise  # Re-raise ValueErrors as-is
        except Exception as e:
            logger.error(f"{context} generation failed: {e}")
            logger.error(f"{context} failed: {e}")
            raise ValueError(f"{context} failed: {e}") from e
    
    def analyze_tool(self, tool_path: str) -> ToolAnalysis:
        """
        Analyze a tool file to extract capabilities and metadata.
        
        Args:
            tool_path: Path to the Python tool file
            
        Returns:
            ToolAnalysis with broken-down capabilities
            
        Raises:
            ValueError: If analysis fails
        """
        logger.info(f"Analyzing tool: {tool_path}")
        
        with open(tool_path, 'r', encoding='utf-8') as f:
            source_code = f.read()
        
        prompt = """
        Analyze this Python tool and break it down into specific capabilities/functions that users would invoke.
        
        CRITICAL: Respond with ONLY valid JSON. No markdown, no explanations.
        
        Required JSON structure:
        {
            "tool_name": "exact name of the tool from the code",
            "primary_purpose": "concise description of what this tool does",
            "capabilities": [
                {
                    "name": "descriptive name of this capability",
                    "action_verb": "primary action verb (turn on, send, check, etc.)",
                    "description": "what this specific capability does",
                    "example_targets": ["specific", "things", "this", "acts", "on"]
                }
            ],
            "target_users": ["types", "of", "people", "who", "would", "use", "this"],
            "common_scenarios": ["situations", "when", "people", "would", "need", "this", "tool"]
        }
        
        Focus on:
        1. Break tool into distinct user-facing capabilities
        2. Each capability should have a clear action verb and targets
        3. Think about what users actually DO with this tool
        
        Tool source code:
        """
        
        messages = [{"role": "user", "content": f"{prompt}\n\n```python\n{source_code}\n```"}]
        
        try:
                data = self._generate_and_parse_response(messages, self.analyzer, "tool analysis")
                
                # Convert to dataclass objects
                capabilities = [
                    ToolCapability(
                        name=cap["name"],
                        action_verb=cap["action_verb"],
                        description=cap["description"],
                        example_targets=cap["example_targets"]
                    )
                    for cap in data.get("capabilities", [])
                ]
                
                return ToolAnalysis(
                    tool_name=data["tool_name"],
                    primary_purpose=data["primary_purpose"],
                    capabilities=capabilities,
                    target_users=data.get("target_users", []),
                    common_scenarios=data.get("common_scenarios", [])
                )
                
        except (KeyError) as e:
            logger.error(f"Missing required fields in analysis: {e}")
            logger.error(f"Tool analysis failed: {e}")
            raise ValueError(f"Tool analysis failed: {e}") from e
    
    def generate_capability_examples(
        self, 
        tool_name: str, 
        capability: ToolCapability, 
        count: int = 15,
        previous_feedback: str = ""
    ) -> List[Dict[str, str]]:
        """
        Generate examples for a single capability with optional feedback.
        
        Args:
            tool_name: Name of the tool
            capability: The capability to generate examples for
            count: Number of examples to generate
            previous_feedback: Optional feedback from failed validation
            
        Returns:
            List of example dictionaries with tool_name and query
        """
        logger.info(f"Generating {count} examples for: {capability.name}")
        
        feedback_section = ""
        if previous_feedback:
            feedback_section = f"""
        
        GUIDANCE FOR IMPROVEMENT:
        {previous_feedback}
        
        Apply this guidance while maintaining natural language variety.
        """
        
        prompt = f"""
        Generate {count} examples of how different users would request this capability.
        
        CAPABILITY: {capability.name} ({capability.action_verb} {', '.join(capability.example_targets)})
        Tool: {tool_name}{feedback_section}
        
        Create {count} NATURAL requests that people would actually type or say.
        To avoid clustering in embedding space, draw from different real-world contexts:
        
        NATURAL VARIATION SOURCES:
        
        1. URGENCY & MOOD:
           - Rushed: minimal words, no politeness
           - Frustrated: expressing annoyance with current state
           - Polite: please/thank you, indirect phrasing
           - Tired: lazy shortcuts, minimal effort
           - Excited: enthusiasm, exclamation points
        
        2. TECHNICAL COMFORT:
           - Tech-savvy: specific parameters, technical terms
           - Learning: asking how/what/where questions
           - Confused: vague references, uncertainty
           - Confident: terse, efficient commands
        
        3. CONTEXT TIMING:
           - Activity-triggered: doing something that needs this
           - Time-triggered: regular routines, schedules
           - Problem-triggered: something's wrong, needs fixing
           - Socially-triggered: other people involved
        
        4. NATURAL SPEECH PATTERNS:
           - Shortcuts: minimal typing
           - Questions: seeking capability or state
           - Descriptions: explaining desired outcome
           - Commands: direct imperatives
           - Observations: stating problem implies action
        
        AUTHENTICITY RULES:
        - Write requests as if YOU needed this capability in different situations
        - Include realistic typos and shortcuts
        - Mix specific references with vague ones ("that thing")
        - Sometimes include WHY they need it, sometimes don't
        - Use natural grammar, not formal language
        
        DIVERSITY THROUGH REALISM:
        - A tired parent types differently than an excited teenager
        - Morning requests differ from evening ones
        - First-time users ask differently than regulars
        - Frustrated users phrase things differently than calm ones
        
        AVOID:
        - Academic or formal language
        - Forced diversity that sounds robotic
        - Always using complete sentences
        - Perfect grammar when casual is more natural
        - Domain-specific terms that would only apply to one tool type
        
        Return ONLY a JSON array:
        [
            {{"tool_name": "{tool_name}", "query": "typed request here"}},
            ...
        ]
        """
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
                examples = self._generate_and_parse_response(
                    messages, 
                    self.generator, 
                    "example generation",
                    extra_body={"thinking": {"type": "enabled", "budget_tokens": 1500}}
                )
                
                if not isinstance(examples, list):
                    logger.error("Generator returned non-list response")
                    raise ValueError("Generator returned non-list response")
                
                # Validate structure
                valid_examples = [
                    ex for ex in examples 
                    if isinstance(ex, dict) and "tool_name" in ex and "query" in ex
                ]
                
                logger.info(f"Generated {len(valid_examples)} valid examples")
                return valid_examples
                
        except ValueError:
            raise  # Re-raise ValueErrors from _generate_and_parse_response
    
    def validate_examples_comprehensive(
        self, 
        examples: List[Dict[str, str]], 
        capability: ToolCapability
    ) -> Dict[str, Any]:
        """
        Comprehensive validation including quality, diversity, and token length.
        
        Args:
            examples: List of generated examples
            capability: The capability being validated
            
        Returns:
            Dict with validation results including all metrics
        """
        if not examples:
            return {
                "passed": True, 
                "feedback": "", 
                "examples": examples,
                "diversity": {"assessment": "no_data"},
                "tokens": {"passed": True}
            }
        
        logger.info(f"Validating {len(examples)} examples for: {capability.name}")
        
        # 1. Token length validation
        token_result = self._validate_token_length(examples)
        
        # 2. Diversity analysis
        diversity_result = self._analyze_diversity(examples)
        logger.info(f"Diversity assessment: {diversity_result['assessment']} (score: {diversity_result['diversity_score']:.3f})")
        
        # 3. Quality validation via LLM
        quality_result = self._validate_quality(examples, capability)
        
        # 4. Combine results
        diversity_fails = (
            diversity_result['assessment'] in ['low', 'error'] or 
            diversity_result['high_similarity_pairs'] > len(examples) * 0.2
        )
        
        overall_passed = (
            token_result["passed"] and 
            quality_result["passed"] and 
            not diversity_fails
        )
        
        # Combine feedback
        feedback_parts = []
        if not quality_result["passed"]:
            feedback_parts.append(quality_result["feedback"])
        if diversity_fails:
            feedback_parts.append(self._create_diversity_feedback(diversity_result, capability))
        if not token_result["passed"]:
            feedback_parts.append(f"Token length violations: {token_result['violation_count']} examples exceed 512 tokens")
        
        result = {
            "passed": overall_passed,
            "feedback": "\n\n".join(feedback_parts),
            "examples": examples,
            "diversity": diversity_result,
            "tokens": token_result,
            "quality": quality_result
        }
        
        if result["passed"]:
            logger.info(f"✓ Comprehensive validation passed: {quality_result.get('good_count', 0)}/{len(examples)} good, diversity: {diversity_result['assessment']}")
        else:
            logger.warning(f"✗ Comprehensive validation failed: {result['feedback'][:100]}...")
        
        return result
    
    def _analyze_diversity(self, examples: List[Dict[str, str]]) -> Dict[str, Any]:
        """Analyze semantic diversity using BGE embeddings."""
        if len(examples) < 2:
            return {
                "diversity_score": 1.0,
                "mean_similarity": 0.0,
                "assessment": "insufficient_data",
                "high_similarity_pairs": 0,
                "details": "Less than 2 examples - diversity analysis skipped"
            }
        
        queries = [ex['query'] for ex in examples]
        
        try:
            embeddings = self.embeddings_provider.encode_realtime(queries)
            similarities = cosine_similarity_matrix(embeddings)
            
            # Get upper triangle similarities
            n = len(queries)
            similarities_list = [
                similarities[i][j] for i in range(n) for j in range(i+1, n)
            ]
            
            mean_similarity = sum(similarities_list) / len(similarities_list)
            diversity_score = 1 - mean_similarity
            high_sim_pairs = sum(1 for s in similarities_list if s > 0.7)
            
            # Assessment thresholds
            if diversity_score > 0.4:
                assessment = "excellent"
            elif diversity_score > 0.35:
                assessment = "good"
            elif diversity_score > 0.3:
                assessment = "moderate"
            else:
                assessment = "low"
            
            return {
                "diversity_score": diversity_score,
                "mean_similarity": mean_similarity,
                "assessment": assessment,
                "high_similarity_pairs": high_sim_pairs,
                "min_similarity": min(similarities_list),
                "max_similarity": max(similarities_list),
                "total_pairs": len(similarities_list),
                "details": f"Diversity: {diversity_score:.3f}, High similarity pairs (>0.3): {high_sim_pairs}"
            }
            
        except Exception as e:
            logger.error(f"Diversity analysis failed: {e}")
            raise RuntimeError(f"Embeddings provider failure during diversity analysis: {e}") from e
    
    def _validate_token_length(self, examples: List[Dict[str, str]]) -> Dict[str, Any]:
        """Validate token lengths for BGE embeddings."""
        token_violations = []
        max_tokens = 0
        total_tokens = 0
        
        for i, example in enumerate(examples):
            query = example.get('query', '')
            # BGE-M3 supports 8192 tokens, so we're well within limits
            # Just check approximate length (4 chars ≈ 1 token)
            token_count = len(query) // 4
            total_tokens += token_count
            max_tokens = max(max_tokens, token_count)
            
            if token_count > 512:
                token_violations.append({
                    "index": i,
                    "query": query,
                    "token_count": token_count,
                    "excess": token_count - 512
                })
        
        avg_tokens = total_tokens / len(examples) if examples else 0
        passed = len(token_violations) == 0
        
        if passed:
            logger.info(f"✓ Token validation passed: max {max_tokens} tokens, avg {avg_tokens:.1f}")
        else:
            logger.warning(f"✗ Token validation failed: {len(token_violations)} examples exceed 512 tokens")
        
        return {
            "passed": passed,
            "violations": token_violations,
            "violation_count": len(token_violations),
            "max_tokens": max_tokens,
            "avg_tokens": avg_tokens,
            "total_examples": len(examples)
        }
    
    def _validate_quality(self, examples: List[Dict[str, str]], capability: ToolCapability) -> Dict[str, Any]:
        """Validate example quality via LLM."""
        prompt = f"""
        Validate these examples for a specific capability with contextual understanding.
        
        CAPABILITY CONTEXT:
        - Name: {capability.name}
        - Core Action: {capability.action_verb}
        - Description: {capability.description}
        - Valid Targets: {', '.join(capability.example_targets)}
        
        EXAMPLES TO VALIDATE:
        {json.dumps([ex['query'] for ex in examples], indent=2)}
        
        VALIDATION APPROACH:
        Judge if each example would reasonably accomplish this capability's purpose.
        Be generous with natural language - humans express actions in many ways.
        
        ACCEPT examples that:
        1. Express the capability's intent (even with different verbs)
        2. Reference relevant targets (flexible: "lights"="smart bulbs", "device"=any target)
        3. Are actionable commands/requests (not just observations)
        4. Use natural human language
        5. Are DOMAIN-SPECIFIC (not generic device management)
        
        EXAMPLES OF FLEXIBLE ACCEPTANCE:
        - For "turn" capability: "switch off", "power down", "shut off"
        - For "set" capability: "change", "make", "adjust", "put"
        - For "check" capability: "see", "monitor", "get", "show"
        - For brightness: "dim", "brighten", "set brightness"
        - For discovery: "find", "locate", "scan"
        
        REJECT examples that are too generic:
        - "find all devices" (could be any device tool)
        - "get device status" (too generic)
        - "control outlet" (vague action verb)
        
        Focus on INTENT, ACTIONABILITY, and DOMAIN SPECIFICITY.
        
        Return ONLY JSON:
        {{
            "passed": true/false,
            "issues_found": ["only fundamental problems"],
            "feedback": "guidance on missing elements, not word substitutions",
            "good_count": number_of_acceptable_examples,
            "total_count": {len(examples)}
        }}
        
        Set passed=false ONLY if >10% have fundamental issues (no clear action, no target, not actionable).
        """
        
        try:
            return self._generate_and_parse_response(
                [{"role": "user", "content": prompt}], 
                self.analyzer, 
                "quality validation"
            )
            
        except Exception as e:
            logger.error(f"Quality validation error: {e}")
            raise RuntimeError(f"LLM quality validation failure: {e}") from e
    
    def generate_with_validation(
        self,
        tool_name: str,
        capability: ToolCapability,
        count: int = 15,
        max_retries: int = 2
    ) -> List[Dict[str, str]]:
        """
        Generate examples with validation and up to 3 attempts for quality/diversity.
        Restarts entire process if token length validation fails.
        
        Args:
            tool_name: Name of the tool
            capability: The capability to generate for
            count: Number of examples to generate
            max_retries: Maximum number of complete restarts for token violations
            
        Returns:
            List of validated examples (best available from up to 3 attempts)
        """
        for retry in range(max_retries + 1):
            if retry > 0:
                logger.info(f"🔄 Retry {retry}/{max_retries} for {capability.name} due to token violations")
            
            attempts = []
            
            # Attempt 1: Initial generation
            logger.info(f"Attempt 1/3 for {capability.name}")
            examples_v1 = self.generate_capability_examples(tool_name, capability, count)
            
            # Comprehensive validation
            validation_v1 = self.validate_examples_comprehensive(examples_v1, capability)
            
            # Check token length first - restart if violations found
            token_info = validation_v1.get('tokens', {})
            if not token_info.get('passed', True):
                logger.warning(f"Token violations detected, restarting generation (retry {retry + 1}/{max_retries + 1})")
                if retry == max_retries:
                    logger.error(f"Max retries reached for {capability.name}, proceeding with token violations")
                else:
                    continue  # Restart the entire process
            attempts.append((examples_v1, validation_v1, "initial"))
            
            if validation_v1["passed"]:
                logger.info(f"✓ Attempt 1 passed validation")
                return examples_v1
            
            # Attempts 2-3: With progressively enhanced feedback
            for attempt_num in range(2, 4):
                logger.info(f"Attempt {attempt_num}/3 for {capability.name} with enhanced guidance")
                
                # Get all previous validations for feedback creation
                previous_validations = [validation for _, validation, _ in attempts]
                feedback = self._create_feedback_for_attempt(attempt_num, previous_validations, capability)
                
                examples = self.generate_capability_examples(tool_name, capability, count, feedback)
                
                # Comprehensive validation
                validation = self.validate_examples_comprehensive(examples, capability)
                
                # Check token length - restart if violations found
                token_info = validation.get('tokens', {})
                if not token_info.get('passed', True):
                    logger.warning(f"Token violations in attempt {attempt_num}, restarting generation")
                    if retry == max_retries:
                        logger.error(f"Max retries reached for {capability.name}, proceeding with token violations")
                        attempts.append((examples, validation, f"attempt_{attempt_num}"))
                        break
                    else:
                        break  # Break inner loop to restart outer loop
                attempts.append((examples, validation, f"attempt_{attempt_num}"))
                
                if validation["passed"]:
                    logger.info(f"✓ Attempt {attempt_num} passed validation")
                    return examples
            
            # If we got here without token violations, we can proceed to final selection
            token_info = validation.get('tokens', {})
            if token_info.get('passed', True):
                break
        
        # All attempts completed (or max retries reached) - pick the best one
        best_attempt = self._select_best_attempt(attempts, capability, count)
        examples, validation, attempt_type = best_attempt
        
        logger.warning(f"⚠ Using best attempt ({attempt_type}) for {capability.name}")
        quality_info = validation.get('quality', validation)  # Fallback for older format
        logger.warning(f"  Quality: {quality_info.get('good_count', 0)}/{count} good")
        if 'diversity' in validation:
            logger.warning(f"  Diversity: {validation['diversity']['assessment']} (score: {validation['diversity']['diversity_score']:.3f})")
        
        # Final token check for logging
        token_info = validation.get('tokens', {})
        if not token_info.get('passed', True):
            logger.warning(f"  Token violations: {token_info.get('violation_count', 0)} examples exceed 512 tokens")
        
        return examples
    
    def _get_core_requirements(self, capability: ToolCapability) -> str:
        """Get core requirements shared across all feedback types."""
        return f"""
        CORE REQUIREMENTS for "{capability.action_verb}" on {', '.join(capability.example_targets)}:
        • ACTIONABILITY: Every example must be a clear, executable command
        • DOMAIN SPECIFICITY: Clearly indicate this tool's domain, not generic actions
        • TARGET RELEVANCE: Reference appropriate targets from the capability list
        • NATURAL VARIETY: Use different sentence patterns, not just word swaps
        • SPECIFIC VERBS: Avoid "control", "manage", "handle" - use specific actions
        """.strip()
    
    def _get_diversity_strategies(self) -> str:
        """Get strategies for improving semantic diversity."""
        return """
        DIVERSITY STRATEGIES:
        • VARY FORMALITY LEVELS: Mix casual, formal, urgent tones
        • CHANGE SENTENCE STRUCTURES: Commands, questions, requests, statements
        • USE DIFFERENT CONTEXTS: Different situations, environments, users
        • VARY TARGET SPECIFICITY: Generic vs specific vs detailed references
        • ADD CONTEXT CLUES: Time, location, reason, condition
        • MIX INTERACTION STYLES: Voice commands, typed requests, incomplete phrases
        • INCLUDE EDGE CASES: Multiple targets, conditional requests, partial specifications
        """.strip()
    
    def _create_diversity_feedback(self, diversity_result: Dict[str, Any], capability: ToolCapability) -> str:
        """Create actionable feedback for improving diversity."""
        return f"""
        DIVERSITY ISSUE DETECTED:
        - Current diversity score: {diversity_result['diversity_score']:.3f} ({diversity_result['assessment']})
        - High similarity pairs: {diversity_result['high_similarity_pairs']} (threshold: >20% of examples)
        
        {self._get_core_requirements(capability)}
        
        {self._get_diversity_strategies()}
        
        Generate examples that would NOT be semantically similar to each other in BGE embeddings.
        Focus on creating distinct semantic patterns rather than just word variations.
        """.strip()
    
    def _create_guided_feedback(self, raw_feedback: str, capability: ToolCapability) -> str:
        """Convert prescriptive feedback to principle-based guidance."""
        return f"""
        Based on validation feedback, improve examples while maintaining natural language variety:
        
        PRESERVE:
        - Natural synonyms and varied phrasing
        - Different length categories (concise, short, conversational)
        - Casual/formal tone variety
        
        {self._get_core_requirements(capability)}
        
        Original feedback context: {raw_feedback}
        
        Focus on fundamental completeness rather than specific word choices.
        """.strip()
    
    def _create_enhanced_final_feedback(
        self, 
        previous_validations: List[Dict[str, Any]], 
        capability: ToolCapability
    ) -> str:
        """Create enhanced feedback for final attempt combining all previous results."""
        all_issues = []
        diversity_scores = []
        
        for i, validation in enumerate(previous_validations, 1):
            # Extract quality issues
            quality_info = validation.get("quality", validation)  # Fallback for older format
            issues = quality_info.get("issues_found", [])
            if issues:
                all_issues.extend([f"Attempt {i}: {issue}" for issue in issues])
            
            # Extract diversity scores
            diversity_info = validation.get("diversity", {})
            if diversity_info:
                diversity_scores.append(f"Attempt {i}: {diversity_info.get('assessment', 'unknown')} (score: {diversity_info.get('diversity_score', 0):.3f})")
        
        return f"""
        FINAL ATTEMPT GUIDANCE:
        After {len(previous_validations)} attempts, focus on these specific improvements:
        
        QUALITY ISSUES IDENTIFIED:
        {chr(10).join(all_issues) if all_issues else "- None specifically identified"}
        
        DIVERSITY ANALYSIS:
        {chr(10).join(diversity_scores) if diversity_scores else "- No diversity data available"}
        
        {self._get_core_requirements(capability)}
        
        {self._get_diversity_strategies()}
        
        This is the final attempt - prioritize creating semantically distinct examples.
        """.strip()
    
    def _create_feedback_for_attempt(
        self, 
        attempt_num: int,
        previous_validations: List[Dict[str, Any]], 
        capability: ToolCapability
    ) -> str:
        """Create feedback for the current attempt based on previous validation results."""
        if attempt_num == 2:
            # Second attempt: use guided feedback from first attempt
            first_feedback = previous_validations[0].get("feedback", "")
            return self._create_guided_feedback(first_feedback, capability)
        elif attempt_num == 3:
            # Third attempt: enhanced feedback combining all previous attempts
            return self._create_enhanced_final_feedback(previous_validations, capability)
        return ""
    
    def _select_best_attempt(
        self, 
        attempts: List[tuple], 
        capability: ToolCapability, 
        count: int
    ) -> tuple:
        """
        Select the best attempt based on combined quality and diversity scores.
        
        Args:
            attempts: List of (examples, validation, attempt_type) tuples
            capability: The capability being evaluated
            count: Target number of examples
            
        Returns:
            Best (examples, validation, attempt_type) tuple
        """
        best_score = -1
        best_attempt = attempts[0]  # Fallback to first attempt
        
        for examples, validation, attempt_type in attempts:
            # Calculate combined score (quality + diversity)
            quality_info = validation.get("quality", {})
            quality_score = quality_info.get("good_count", 0) / count
            
            diversity_info = validation.get("diversity", {})
            diversity_score = diversity_info.get("diversity_score", 0.5)  # Neutral if missing
            
            # Weight quality slightly higher than diversity (60/40 split)
            combined_score = (quality_score * 0.6) + (diversity_score * 0.4)
            
            logger.info(f"  {attempt_type}: quality={quality_score:.3f}, diversity={diversity_score:.3f}, combined={combined_score:.3f}")
            
            if combined_score > best_score:
                best_score = combined_score
                best_attempt = (examples, validation, attempt_type)
        
        return best_attempt
    
    def process_capability_parallel(
        self,
        tool_name: str,
        capability: ToolCapability,
        examples_per_capability: int
    ) -> List[Dict[str, str]]:
        """
        Process a single capability with generation, validation, and regeneration.
        
        Args:
            tool_name: Name of the tool
            capability: The capability to process
            examples_per_capability: Number of examples to generate
            
        Returns:
            List of validated examples for this capability
        """
        logger.info(f"Processing: {capability.name}")
        
        examples = self.generate_with_validation(
            tool_name,
            capability,
            examples_per_capability
        )
        logger.info(f"✓ Completed {capability.name}: {len(examples)} examples")
        return examples
    
    def _generate_sequential_grouped(
        self,
        analysis: ToolAnalysis,
        examples_per_capability: int
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        Sequential fallback processing for capabilities with grouped output.
        
        Args:
            analysis: Tool analysis with capabilities
            examples_per_capability: Number of examples per capability
            
        Returns:
            Dict mapping capability names to their examples
        """
        capability_examples = {}

        for capability in analysis.capabilities:
            examples = self.process_capability_parallel(
                analysis.tool_name,
                capability,
                examples_per_capability
            )
            capability_examples[capability.name] = examples

        return capability_examples
    
    def _save_separated_outputs(
        self, 
        output_path: str, 
        capability_examples: Dict[str, List[Dict[str, str]]],
        tool_name: str
    ) -> None:
        """
        Save examples as embedding trigger examples for tool-level classification.
        
        Args:
            output_path: File or directory path to save trigger examples
            capability_examples: Dict mapping capability names to examples
            tool_name: Name of the tool for file naming
        """
        # Handle both file and directory paths
        output_path_obj = Path(output_path)
        
        # If it's a file path ending with .json, use its parent directory
        if output_path_obj.suffix == '.json':
            output_dir = output_path_obj.parent
        else:
            output_dir = output_path_obj
        
        # Create the output directory
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create embedding trigger examples file for tool-level classification
        trigger_examples = []
        for capability_name, cap_examples in capability_examples.items():
            for example in cap_examples:
                trigger_examples.append({
                    "tool_name": tool_name,
                    "query": example["query"]
                })
        
        # Save the embedding trigger examples
        trigger_file = output_dir / "embedding_trigger_examples.json"
        with open(trigger_file, 'w', encoding='utf-8') as f:
            json.dump(trigger_examples, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Created embedding trigger examples file: {trigger_file}")
        logger.info(f"Total trigger examples: {len(trigger_examples)}")
    
    def generate_all_with_analysis(
        self,
        analysis: ToolAnalysis,
        examples_per_capability: int = 15,
        output_path: Optional[str] = None,
        max_workers: Optional[int] = None
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        Main orchestration method with parallel processing using pre-analyzed tool data.
        
        Args:
            analysis: Pre-analyzed tool data
            examples_per_capability: Examples to generate per capability
            output_path: Optional output file path
            max_workers: Maximum number of parallel workers (defaults to number of capabilities)
            
        Returns:
            Dict with capability names as keys and their examples as values
        """
        logger.info(f"Tool: {analysis.tool_name}")
        logger.info(f"Purpose: {analysis.primary_purpose}")
        logger.info(f"Capabilities: {len(analysis.capabilities)}")
        
        # Calculate optimal worker count
        num_capabilities = len(analysis.capabilities)
        if max_workers is None:
            # Default to number of capabilities, with reasonable bounds
            optimal_workers = min(num_capabilities, 12)  # Cap at 12 to avoid overwhelming APIs
        else:
            # Use provided max_workers but don't exceed capability count
            optimal_workers = min(max_workers, num_capabilities)
        
        # Try parallel processing first
        capability_examples = {}
        try:
            logger.info(f"Attempting parallel processing with {optimal_workers} workers for {num_capabilities} capabilities")
            
            with ThreadPoolExecutor(max_workers=optimal_workers) as executor:
                # Submit all capability processing tasks
                future_to_capability = {
                    executor.submit(
                        self.process_capability_parallel,
                        analysis.tool_name,
                        capability,
                        examples_per_capability
                    ): capability
                    for capability in analysis.capabilities
                }
                
                # Collect results as they complete
                for future in as_completed(future_to_capability):
                    capability = future_to_capability[future]
                    examples = future.result()
                    capability_examples[capability.name] = examples
            
            logger.info("✓ Parallel processing completed successfully")
            
        except Exception as e:
            logger.warning(f"Parallel processing failed ({e}), falling back to sequential")
            capability_examples = self._generate_sequential_grouped(analysis, examples_per_capability)
        
        total_examples = sum(len(examples) for examples in capability_examples.values())
        logger.info(f"\nTotal: {total_examples} examples generated across {len(capability_examples)} capabilities")
        
        # Save if requested
        if output_path:
            self._save_separated_outputs(output_path, capability_examples, analysis.tool_name)
        
        return capability_examples

    def generate_all(
        self,
        tool_path: str,
        examples_per_capability: int = 15,
        output_path: Optional[str] = None,
        max_workers: Optional[int] = None
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        Main orchestration method with parallel processing and sequential fallback.
        
        Args:
            tool_path: Path to tool file
            examples_per_capability: Examples to generate per capability
            output_path: Optional output file path
            max_workers: Maximum number of parallel workers (defaults to number of capabilities)
            
        Returns:
            Dict with capability names as keys and their examples as values
        """
        # Analyze tool
        analysis = self.analyze_tool(tool_path)
        
        # Use the analysis-based method
        return self.generate_all_with_analysis(
            analysis=analysis,
            examples_per_capability=examples_per_capability,
            output_path=output_path,
            max_workers=max_workers
        )