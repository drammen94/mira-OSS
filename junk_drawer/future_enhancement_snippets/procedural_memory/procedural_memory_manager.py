import hashlib
import json
import logging
import os
import re
from glob import glob
from typing import Dict, List, Any, Optional, Tuple, Set, cast

import numpy as np
from cns.core.embedded_message import EmbeddedMessage

from tools.repo import ToolRepository
from config.config_manager import config
from utils.serialization import to_json, from_json


class ProceduralMemoryManager:
    
    def __init__(
        self,
        tool_repo: ToolRepository,
        model,
        procedural_memories_dir: Optional[str] = None,
        llm_provider = None,
        working_memory = None,
        tag_parser = None
    ):
        self.logger = logging.getLogger("procedural_memory_manager")
        self.tool_repo = tool_repo

        self.working_memory = working_memory
        self.tag_parser = tag_parser

        self._detected_procedural_memory_id = None


        
        self.procedural_memories_dir = procedural_memories_dir
        if self.procedural_memories_dir is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.procedural_memories_dir = current_dir
        
        self.procedural_memories: Dict[str, Dict[str, Any]] = {}
        
        # Use the provided model for embeddings (BGE-M3)
        self.match_threshold = 0.65
        
        self.active_procedural_memory_id: Optional[str] = None
        
        self.completed_steps: Set[str] = set()
        self.available_steps: Set[str] = set()
        self.procedural_memory_data: Dict[str, Any] = {}
        
        self.procedural_memory_embeddings: Dict[str, Dict[str, Any]] = {}
        
        self.model = model
        self.logger.info("Using provided ONNX embedding model")
        
        self.llm_provider = llm_provider
        
        self.load_procedural_memories()
        
        self._compute_procedural_memory_embeddings()
    
    def load_procedural_memories(self) -> None:
        self.logger.info(f"Loading procedural memory definitions from {self.procedural_memories_dir}")
        
        procedural_memory_files = glob(os.path.join(self.procedural_memories_dir, "*.json"))
        
        for file_path in procedural_memory_files:
            try:
                with open(file_path, 'r') as f:
                    procedural_memory = json.load(f)
                
                if self._validate_procedural_memory(procedural_memory):
                    self.procedural_memories[procedural_memory["id"]] = procedural_memory
                    self.logger.info(f"Loaded procedural memory: {procedural_memory['id']} ({procedural_memory['name']})")
            except Exception as e:
                self.logger.error(f"Error loading procedural memory definition from {file_path}: {e}")
        
        self.logger.info(f"Loaded {len(self.procedural_memories)} procedural memory definitions: {', '.join(self.procedural_memories.keys())}")
    
    
    def _validate_procedural_memory(self, procedural_memory: Dict[str, Any]) -> bool:
        required_fields = ["id", "name", "description", "trigger_examples", "steps", "completion_requirements"]
        for field in required_fields:
            if field not in procedural_memory:
                self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}: missing required field '{field}'")
                return False
        
        if not isinstance(procedural_memory["steps"], dict) or not procedural_memory["steps"]:
            self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}: steps must be a non-empty dictionary")
            return False
        
        if not isinstance(procedural_memory["trigger_examples"], list) or not procedural_memory["trigger_examples"]:
            self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}: trigger_examples must be a non-empty list")
            return False
        
        completion_requirements = procedural_memory.get("completion_requirements", {})
        if not isinstance(completion_requirements, dict):
            self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}: completion_requirements must be a dictionary")
            return False
        
        if "required_steps" not in completion_requirements and "required_data" not in completion_requirements:
            self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}: completion_requirements must include required_steps or required_data")
            return False
        
        for step_id, step in procedural_memory["steps"].items():
            if not isinstance(step, dict):
                self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}, step {step_id}: step must be a dictionary")
                return False
            
            for field in ["id", "description", "tools", "guidance", "prerequisites"]:
                if field not in step:
                    self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}, step {step_id}: missing required field '{field}'")
                    return False
            
            if not isinstance(step["tools"], list):
                self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}, step {step_id}: tools must be a list")
                return False
            
            if not isinstance(step["prerequisites"], list):
                self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}, step {step_id}: prerequisites must be a list")
                return False
            
            if "optional" in step and not isinstance(step["optional"], bool):
                self.logger.error(f"Procedural Memory validation failed for {procedural_memory.get('id', 'unknown')}, step {step_id}: optional flag must be a boolean")
                return False
        
        return True
    
    def _compute_procedural_memory_embeddings(self) -> None:
        if not self.model:
            self.logger.error("Cannot compute embeddings: model not loaded")
            return
        
        self.logger.info("Computing embeddings for procedural_memory trigger examples")
        
        for procedural_memory_id, procedural_memory in self.procedural_memories.items():
            examples = procedural_memory["trigger_examples"]
            
            try:
                # Use encode_realtime for procedural_memory detection (fast path)
                embeddings = self.model.encode_realtime(examples)
                
                self.procedural_memory_embeddings[procedural_memory_id] = {
                    "examples": examples,
                    "embeddings": embeddings
                }
                
                self.logger.debug(f"Computed embeddings for procedural_memory {procedural_memory_id}: {len(examples)} examples")
            except Exception as e:
                self.logger.error(f"Error computing embeddings for procedural_memory {procedural_memory_id}: {e}")
        
        self.logger.info(f"Computed embeddings for {len(self.procedural_memory_embeddings)} procedural_memorys")
    
    def update_procedural_memory_hint(self, detected_procedural_memory_id: Optional[str] = None) -> None:
        if detected_procedural_memory_id:
            self._detected_procedural_memory_id = detected_procedural_memory_id
        else:
            detected_procedural_memory_id = self._detected_procedural_memory_id

        if not detected_procedural_memory_id or not self.working_memory:
            return

        if self.get_active_procedural_memory():
            return

        procedural_memory = self.procedural_memories.get(detected_procedural_memory_id)
        if not procedural_memory:
            return

        procedural_memory_hint = f"# Detected Procedural Memory\n"
        procedural_memory_hint += f"I've detected that the user might want help with: {procedural_memory['name']}.\n"
        procedural_memory_hint += "If this seems correct, you can confirm and start this procedural_memory process by including this exact text in your response:\n"
        procedural_memory_hint += f"<procedural_memory_start id=\"{detected_procedural_memory_id}\" />"

        # Send procedural_memory update to ProceduralMemoryTrinket
        context = {
            'procedural_memory_id': detected_procedural_memory_id,
            'procedural_memory_hint': procedural_memory_hint,
            'procedural_memory_content': None  # No active procedural_memory yet
        }
        
        self.working_memory.publish_trinket_update(
            target_trinket="ProceduralMemoryTrinket",
            context=context
        )

        self.logger.debug(f"Sent procedural_memory hint for '{procedural_memory['name']}' to ProceduralMemoryTrinket")

    def detect_procedural_memory(self, message: EmbeddedMessage) -> Tuple[Optional[str], float]:
        if not self.procedural_memories or not self.procedural_memory_embeddings:
            return None, 0.0

        try:
            # Get pre-computed embedding from embedded message
            message_embedding = message.embedding_384
            
            best_match_id = None
            best_match_score = 0.0
            
            for procedural_memory_id, embedding_data in self.procedural_memory_embeddings.items():
                example_embeddings = embedding_data["embeddings"]
                
                scores = []
                for example_embedding in example_embeddings:
                    # Direct dot product since both are normalized
                    similarity = np.dot(message_embedding, example_embedding)
                    scores.append(float(similarity))
                
                if scores:
                    best_score = max(scores)
                    
                    if best_score > best_match_score:
                        best_match_score = best_score
                        best_match_id = procedural_memory_id
            
            if best_match_score >= self.match_threshold:
                self.logger.info(f"Detected procedural_memory: {best_match_id} (confidence: {best_match_score:.4f})")
                return best_match_id, best_match_score
            else:
                self.logger.debug(f"No procedural_memory detected (best confidence: {best_match_score:.4f})")
                return None, 0.0
        
        except Exception as e:
            self.logger.error(f"Procedural Memory detection failed: {e.__class__.__name__}: {e}", exc_info=True)
            return None, 0.0
    
    def update_working_memory(self) -> None:
        if not self.get_active_procedural_memory() and hasattr(self, '_detected_procedural_memory_id') and getattr(self, '_detected_procedural_memory_id', None):
            self.update_procedural_memory_hint(self._detected_procedural_memory_id)

        self._update_procedural_memory_content()

    def _update_procedural_memory_content(self) -> None:
        if not self.working_memory or not self.active_procedural_memory_id:
            return

        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]

        content = self._generate_procedural_memory_content(procedural_memory)
        
        # Send complete procedural_memory content to ProceduralMemoryTrinket
        context = {
            'procedural_memory_id': self.active_procedural_memory_id,
            'procedural_memory_hint': None,  # No hint when procedural_memory is active
            'procedural_memory_content': content
        }
        
        self.working_memory.publish_trinket_update(
            target_trinket="ProceduralMemoryTrinket",
            context=context
        )

        self.logger.debug(f"Sent procedural_memory content for '{self.active_procedural_memory_id}' to ProceduralMemoryTrinket")


    def _generate_procedural_memory_content(self, procedural_memory: Dict[str, Any]) -> Dict[str, Any]:
        content = {
            "header": "",
            "steps": {},
            "data": {},
            "checklist": "",
            "navigation": ""
        }

        content["header"] = f"""

# ACTIVE PROCEDURAL MEMORY GUIDANCE
You are currently helping the user with: **{procedural_memory['name']}**
Description: {procedural_memory['description']}
"""

        for step_id in self.available_steps:
            step = procedural_memory["steps"].get(step_id)
            if step:
                optional_text = " (Optional)" if step.get("optional", False) else ""
                step_content = f"""

## Available Step: {step['description']}{optional_text} (ID: {step_id})
{step['guidance']}
"""
                
                if "inputs" in step and step["inputs"]:
                    step_content += "\n\n### Required inputs for this step:\n"
                    for input_item in step["inputs"]:
                        req_marker = "(Required)" if input_item.get("required", False) else "(Optional)"
                        input_desc = input_item.get("description", input_item["name"])
                        
                        format_info = ""
                        if input_item.get("type") == "select" and "options" in input_item:
                            options = input_item["options"]
                            if isinstance(options[0], dict):
                                option_values = ", ".join([f"{o.get('label', o['value'])}" for o in options])
                            else:
                                option_values = ", ".join([str(o) for o in options])
                            format_info = f" - Options: {option_values}"
                        elif input_item.get("example"):
                            format_info = f" - Example: {input_item['example']}"
                        
                        step_content += f"- **{input_item['name']}** {req_marker}: {input_desc}{format_info}\n"
                
                content["steps"][step_id] = step_content

        for field, value in self.procedural_memory_data.items():
            if isinstance(value, (dict, list)):
                display_value = json.dumps(value, indent=2)
            else:
                display_value = str(value)

            field_description = ""
            if "data_schema" in procedural_memory and field in procedural_memory["data_schema"]:
                field_description = f" - {procedural_memory['data_schema'][field].get('description', '')}"

            content["data"][field] = f"\n## Data: {field}{field_description}\n{display_value}"

        checklist_items = []
        for step_id, step in procedural_memory["steps"].items():
            if step_id in self.completed_steps:
                status_marker = "[✅]"
            elif step_id in self.available_steps:
                status_marker = "[🔄]"
            else:
                status_marker = "[ ]"
            
            optional_text = " (Optional)" if step.get("optional", False) else ""
            checklist_items.append(f"{status_marker} {step['description']}{optional_text} `(ID: {step_id})`")

        content["checklist"] = f"""

## Procedural Memory Checklist
{chr(10).join(checklist_items)}
"""

        content["navigation"] = """

## Navigation Commands
You can navigate the procedural_memory using these commands:
- <procedural_memory_complete_step id="step_id" /> - Mark a step as complete (replace step_id with the actual ID)
- <procedural_memory_skip_step id="step_id" /> - Skip an optional step (replace step_id with the actual ID)
- <procedural_memory_revisit_step id="step_id" /> - Go back to a previously completed step (replace step_id with the actual ID)
- <procedural_memory_complete /> - Complete the entire procedural_memory
- <procedural_memory_cancel /> - Cancel the procedural_memory

## IMPORTANT: Using Step IDs Correctly
- When using procedural_memory commands, you MUST use the exact step ID as shown in parentheses
- Only mark a step as complete when you have collected ALL required inputs for that step
- After completing a step, available steps will automatically update based on the procedural_memory structure
- Complete each step fully before moving to the next step
"""

        return content


    def start_procedural_memory(self, procedural_memory_id: str, triggering_message: str = None, llm_provider = None) -> Dict[str, Any]:
        if procedural_memory_id not in self.procedural_memories:
            raise ValueError(f"Procedural Memory with ID '{procedural_memory_id}' doesn't exist")

        procedural_memory = self.procedural_memories[procedural_memory_id]

        self.active_procedural_memory_id = procedural_memory_id

        self.completed_steps = set()
        self.procedural_memory_data = {}

        if triggering_message and llm_provider:
            extracted_data = self._extract_initial_data(procedural_memory_id, triggering_message, llm_provider)
            if extracted_data:
                self.procedural_memory_data.update(extracted_data)
                self.logger.info(f"Extracted initial data from triggering message: {extracted_data}")

        self.available_steps = set()

        entry_points = []
        if "entry_points" in procedural_memory and procedural_memory["entry_points"]:
            entry_points = [
                step_id for step_id in procedural_memory["entry_points"]
                if step_id in procedural_memory["steps"]
            ]
        else:
            entry_points = [
                step_id for step_id, step in procedural_memory["steps"].items()
                if not step["prerequisites"]
            ]

        for step_id in entry_points:
            step = procedural_memory["steps"].get(step_id)
            if not step:
                continue

            if "provides_data" in step and step["provides_data"]:
                if all(field in self.procedural_memory_data for field in step["provides_data"]):
                    self.completed_steps.add(step_id)
                    self.logger.info(f"Auto-completed step {step_id} based on extracted data")
                else:
                    self._check_and_add_available_step(step_id)
            else:
                self._check_and_add_available_step(step_id)

        for step_id in procedural_memory["steps"]:
            if step_id not in self.completed_steps and step_id not in self.available_steps:
                self._check_and_add_available_step(step_id)

        self._update_tool_access()

        if self.working_memory:
            self._update_procedural_memory_content()

        self.logger.info(f"Started procedural_memory: {procedural_memory_id}")

        return {
            "procedural_memory_id": procedural_memory_id,
            "name": procedural_memory["name"],
            "description": procedural_memory["description"],
            "available_steps": list(self.available_steps),
            "completed_steps": list(self.completed_steps),
            "procedural_memory_data": self.procedural_memory_data
        }
        
    def _extract_initial_data(self, procedural_memory_id: str, message: str, llm_provider) -> Dict[str, Any]:
        procedural_memory = self.procedural_memories.get(procedural_memory_id)
        if not procedural_memory or not message or not llm_provider:
            return {}
            
        data_schema = procedural_memory.get("data_schema", {})
        if not data_schema:
            return {}
            
        try:
            system_prompt = f"""
            You are a data extraction assistant that extracts structured information from natural language requests.
            
            For the procedural_memory: "{procedural_memory['name']}" ({procedural_memory['description']}), extract any relevant data from the user's message.
            
            Only extract data that is explicitly mentioned or can be clearly inferred. DO NOT make up or assume information not in the message.
            
            The data schema for this procedural_memory contains these possible fields:
            """
            
            for field_name, field_info in data_schema.items():
                field_type = field_info.get("type", "string")
                field_description = field_info.get("description", "")
                system_prompt += f"\n- {field_name} ({field_type}): {field_description}"
            
            system_prompt += """
            
            IMPORTANT OUTPUT FORMATTING INSTRUCTIONS:
            1. Return a JSON object with field names as keys and extracted values as values
            2. ONLY include fields that are explicitly mentioned or clearly implied in the message
            3. DO NOT include fields where no information is provided
            4. DO NOT add explanations, comments, or markdown formatting
            5. Return ONLY valid, parseable JSON
            """
            
            user_message = f"Extract data from this message: {message}"
            
            response = llm_provider.generate_response(
                messages=[{"role": "user", "content": user_message}],
                system_prompt=system_prompt,
                temperature=0.0,
                stream=False
            )
            
            response_text = llm_provider.extract_text_content(response)
            
            import json
            
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
                
            try:
                extracted_data = json.loads(response_text)
                
                validated_data = {}
                for field, value in extracted_data.items():
                    if field in data_schema:
                        field_type = data_schema[field]["type"]
                        
                        if (field_type == "string" and isinstance(value, str)) or \
                           (field_type == "number" and isinstance(value, (int, float))) or \
                           (field_type == "integer" and isinstance(value, int)) or \
                           (field_type == "boolean" and isinstance(value, bool)) or \
                           (field_type == "array" and isinstance(value, list)) or \
                           (field_type == "object" and isinstance(value, dict)):
                            validated_data[field] = value
                        else:
                            self.logger.warning(f"Field {field} has incorrect type: expected {field_type}")
                
                return validated_data
                
            except json.JSONDecodeError as e:
                self.logger.error(f"Failed to parse JSON from LLM response: {e}")
                self.logger.debug(f"Response text: {response_text}")
                return {}
            
        except Exception as e:
            self.logger.error(f"Error extracting initial data: {e}")
            return {}
    
    def _check_and_add_available_step(self, step_id: str) -> None:
        if not self.active_procedural_memory_id:
            return
        
        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]
        step = procedural_memory["steps"].get(step_id)
        
        if not step:
            return
        
        if step_id in self.completed_steps or step_id in self.available_steps:
            return
        
        for prereq in step["prerequisites"]:
            if prereq not in self.completed_steps:
                return
        
        if "condition" in step:
            condition = step["condition"]
            if condition.startswith("!procedural_memory_data."):
                data_field = condition.split(".", 1)[1]
                if data_field in self.procedural_memory_data and self.procedural_memory_data[data_field]:
                    return
            elif condition.startswith("procedural_memory_data."):
                data_field = condition.split(".", 1)[1]
                if data_field not in self.procedural_memory_data or not self.procedural_memory_data[data_field]:
                    return
        
        if "requires_data" in step:
            for data_field in step["requires_data"]:
                if data_field not in self.procedural_memory_data:
                    return
        
        self.available_steps.add(step_id)
    
    def complete_step(self, step_id: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
        if not self.active_procedural_memory_id:
            raise ValueError("No active procedural_memory")

        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]

        if step_id not in procedural_memory["steps"]:
            raise ValueError(f"Step '{step_id}' doesn't exist in procedural_memory '{self.active_procedural_memory_id}'")

        if step_id not in self.available_steps:
            raise ValueError(f"Step '{step_id}' is not currently available")

        self.completed_steps.add(step_id)
        self.available_steps.remove(step_id)

        if data:
            self.procedural_memory_data.update(data)

        step = procedural_memory["steps"][step_id]
        if "provides_data" in step and not data:
            self.logger.warning(f"Step {step_id} is marked as providing data but no data was provided")

        for potential_step_id in procedural_memory["steps"]:
            self._check_and_add_available_step(potential_step_id)

        self._update_tool_access()

        if self.working_memory:
            self._update_procedural_memory_content()

        self.logger.info(f"Completed procedural_memory step: {step_id}")

        is_complete = self._check_procedural_memory_completion()

        if is_complete:
            return self.complete_procedural_memory()

        return {
            "procedural_memory_id": self.active_procedural_memory_id,
            "name": procedural_memory["name"],
            "description": procedural_memory["description"],
            "available_steps": list(self.available_steps),
            "completed_steps": list(self.completed_steps),
            "procedural_memory_data": self.procedural_memory_data
        }
    
    def skip_step(self, step_id: str) -> Dict[str, Any]:
        if not self.active_procedural_memory_id:
            raise ValueError("No active procedural_memory")

        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]

        if step_id not in procedural_memory["steps"]:
            raise ValueError(f"Step '{step_id}' doesn't exist in procedural_memory '{self.active_procedural_memory_id}'")

        if step_id not in self.available_steps:
            raise ValueError(f"Step '{step_id}' is not currently available")

        step = procedural_memory["steps"][step_id]
        if not step.get("optional", False):
            raise ValueError(f"Step '{step_id}' is not optional and cannot be skipped")

        self.completed_steps.add(step_id)
        self.available_steps.remove(step_id)

        for potential_step_id in procedural_memory["steps"]:
            self._check_and_add_available_step(potential_step_id)

        self._update_tool_access()

        if self.working_memory:
            self._update_procedural_memory_content()

        self.logger.info(f"Skipped procedural_memory step: {step_id}")

        is_complete = self._check_procedural_memory_completion()

        if is_complete:
            return self.complete_procedural_memory()

        return {
            "procedural_memory_id": self.active_procedural_memory_id,
            "name": procedural_memory["name"],
            "description": procedural_memory["description"],
            "available_steps": list(self.available_steps),
            "completed_steps": list(self.completed_steps),
            "procedural_memory_data": self.procedural_memory_data
        }
    
    def revisit_step(self, step_id: str) -> Dict[str, Any]:
        if not self.active_procedural_memory_id:
            raise ValueError("No active procedural_memory")

        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]

        if step_id not in procedural_memory["steps"]:
            raise ValueError(f"Step '{step_id}' doesn't exist in procedural_memory '{self.active_procedural_memory_id}'")

        if step_id not in self.completed_steps:
            raise ValueError(f"Step '{step_id}' was not previously completed")

        self.completed_steps.remove(step_id)
        self.available_steps.add(step_id)

        self._update_tool_access()

        if self.working_memory:
            self._update_procedural_memory_content()

        self.logger.info(f"Revisiting procedural_memory step: {step_id}")

        return {
            "procedural_memory_id": self.active_procedural_memory_id,
            "name": procedural_memory["name"],
            "description": procedural_memory["description"],
            "available_steps": list(self.available_steps),
            "completed_steps": list(self.completed_steps),
            "procedural_memory_data": self.procedural_memory_data
        }
    
    def _check_procedural_memory_completion(self) -> bool:
        if not self.active_procedural_memory_id:
            return False
        
        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]
        completion_requirements = procedural_memory.get("completion_requirements", {})
        
        required_steps = completion_requirements.get("required_steps", [])
        for step_id in required_steps:
            if step_id not in self.completed_steps:
                return False
        
        required_data = completion_requirements.get("required_data", [])
        for data_field in required_data:
            if data_field not in self.procedural_memory_data:
                return False
        
        return True
    
    def complete_procedural_memory(self) -> Dict[str, Any]:
        if not self.active_procedural_memory_id:
            raise ValueError("No active procedural_memory")

        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]

        completed_procedural_memory = {
            "procedural_memory_id": self.active_procedural_memory_id,
            "name": procedural_memory["name"],
            "description": procedural_memory["description"],
            "status": "completed",
            "completed_steps": list(self.completed_steps),
            "procedural_memory_data": self.procedural_memory_data
        }


        # Clear procedural_memory content by sending empty update
        if self.working_memory:
            context = {
                'procedural_memory_id': None,
                'procedural_memory_hint': None,
                'procedural_memory_content': None
            }
            self.working_memory.publish_trinket_update(
                target_trinket="ProceduralMemoryTrinket",
                context=context
            )

        self.active_procedural_memory_id = None
        self.completed_steps = set()
        self.available_steps = set()
        self.procedural_memory_data = {}

        self.logger.info(f"Completed procedural_memory: {completed_procedural_memory['procedural_memory_id']}")

        return completed_procedural_memory

    def cancel_procedural_memory(self) -> Dict[str, Any]:
        if not self.active_procedural_memory_id:
            raise ValueError("No active procedural_memory")

        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]

        cancelled_procedural_memory = {
            "procedural_memory_id": self.active_procedural_memory_id,
            "name": procedural_memory["name"],
            "description": procedural_memory["description"],
            "status": "cancelled",
            "completed_steps": list(self.completed_steps),
            "procedural_memory_data": self.procedural_memory_data
        }


        # Clear procedural_memory content by sending empty update
        if self.working_memory:
            context = {
                'procedural_memory_id': None,
                'procedural_memory_hint': None,
                'procedural_memory_content': None
            }
            self.working_memory.publish_trinket_update(
                target_trinket="ProceduralMemoryTrinket",
                context=context
            )

        self.active_procedural_memory_id = None
        self.completed_steps = set()
        self.available_steps = set()
        self.procedural_memory_data = {}

        self.logger.info(f"Cancelled procedural_memory: {cancelled_procedural_memory['procedural_memory_id']}")

        return cancelled_procedural_memory
    
    def get_active_procedural_memory(self) -> Optional[Dict[str, Any]]:
        if not self.active_procedural_memory_id:
            return None
        
        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]
        
        return {
            "procedural_memory_id": self.active_procedural_memory_id,
            "name": procedural_memory["name"],
            "description": procedural_memory["description"],
            "available_steps": list(self.available_steps),
            "completed_steps": list(self.completed_steps),
            "procedural_memory_data": self.procedural_memory_data,
            "total_steps": len(procedural_memory["steps"])
        }
    
    def get_detected_procedural_memory(self) -> Optional[str]:
        """Get the currently detected procedural_memory ID."""
        return self._detected_procedural_memory_id
    
    def has_procedural_memory_hint(self) -> bool:
        """Check if there's currently a procedural_memory hint active."""
        return self._detected_procedural_memory_id is not None and not self.active_procedural_memory_id
    
    def _update_tool_access(self) -> None:
        if not self.active_procedural_memory_id:
            return
        
        procedural_memory = self.procedural_memories[self.active_procedural_memory_id]
        
        needed_tools = set()
        for step_id in self.available_steps:
            step = procedural_memory["steps"].get(step_id)
            if step:
                if "tools" in step and step["tools"]:
                    for tool_name in step.get("tools", []):
                        if tool_name:
                            needed_tools.add(tool_name)
                else:
                    pass
        
        for tool_name in needed_tools:
            if not tool_name:
                self.logger.warning(f"Empty tool name found in procedural_memory {self.active_procedural_memory_id} step - skipping")
                continue
                
            try:
                if not self.tool_repo.is_tool_enabled(tool_name):
                    self.tool_repo.enable_tool(tool_name)
                    self.logger.info(f"Enabled tool for procedural_memory state: {tool_name}")
            except Exception as e:
                self.logger.error(f"Error enabling tool {tool_name}: {e}")
    
    def get_system_prompt_extension(self) -> str:
        if not self.active_procedural_memory_id or not self.working_memory:
            return ""
        
        return self.working_memory.get_prompt_content()
    
    def check_for_procedural_memory_commands(self, message: str) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        if not self.tag_parser:
            return False, None, None, None
            
        procedural_memory_commands = self.tag_parser.extract_procedural_memory_commands(message)
        if not procedural_memory_commands:
            return False, None, None, None
            
        # Get the first procedural_memory command
        procedural_memory_command = procedural_memory_commands[0]
        
        if procedural_memory_command and procedural_memory_command["command"]:
            command = procedural_memory_command["command"]
            procedural_memory_id = procedural_memory_command["id"]
            
            # Map CNS command format to action
            if command == "procedural_memory_start":
                action = "start"
            elif command == "procedural_memory_complete_step":
                action = "complete_step"
            elif command == "procedural_memory_skip_step":
                action = "skip_step"
            elif command == "procedural_memory_revisit_step":
                action = "revisit_step"
            elif command == "procedural_memory_complete":
                action = "complete"
            elif command == "procedural_memory_cancel":
                action = "cancel"
            else:
                return False, None, None, None
            
            if action == "start":
                if procedural_memory_id:
                    return True, "start", procedural_memory_id, None
            elif action == "complete_step":
                step_id = procedural_memory_id  # The id is the step_id for complete_step
                data = {}  # CNS TagParser doesn't extract data, would need additional parsing
                if step_id:
                    return True, "complete_step", step_id, data
            elif action == "skip_step":
                step_id = procedural_memory_id  # The id is the step_id for skip_step
                if step_id:
                    return True, "skip_step", step_id, None
            elif action == "revisit_step":
                step_id = procedural_memory_id  # The id is the step_id for revisit_step
                if step_id:
                    return True, "revisit_step", step_id, None
            elif action == "complete":
                return True, "complete", None, None
            elif action == "cancel":
                return True, "cancel", None, None
        
        return False, None, None, None
    
    def process_response_commands(self, assistant_response: str, messages: List, llm_provider, tool_relevance_engine) -> None:
        command_found, command_type, command_params, command_data = self.check_for_procedural_memory_commands(assistant_response)
        
        if not command_found:
            return
            
        if command_type == "start" and not self.get_active_procedural_memory():
            procedural_memory_id = command_params
            try:
                triggering_message = None
                for msg in reversed(messages):
                    if msg.role == "user" and isinstance(msg.content, str):
                        triggering_message = msg.content
                        break
                
                self.start_procedural_memory(
                    procedural_memory_id,
                    triggering_message=triggering_message, 
                    llm_provider=llm_provider
                )
                self.logger.info(f"Started procedural_memory: {procedural_memory_id}")
                # Don't suspend tool relevance - we want it active during procedural_memorys
                # to help suggest relevant tools alongside procedural_memory-specified tools
            except Exception as e:
                self.logger.error(f"Error starting procedural_memory {procedural_memory_id}: {e}")
        
        elif command_type == "complete_step" and self.get_active_procedural_memory():
            step_id = command_params
            try:
                self.complete_step(step_id, command_data)
                self.logger.info(f"Completed procedural_memory step: {step_id}")
            except Exception as e:
                self.logger.error(f"Error completing procedural_memory step {step_id}: {e}")
        
        elif command_type == "skip_step" and self.get_active_procedural_memory():
            step_id = command_params
            try:
                self.skip_step(step_id)
                self.logger.info(f"Skipped procedural_memory step: {step_id}")
            except Exception as e:
                self.logger.error(f"Error skipping procedural_memory step {step_id}: {e}")
        
        elif command_type == "revisit_step" and self.get_active_procedural_memory():
            step_id = command_params
            try:
                self.revisit_step(step_id)
                self.logger.info(f"Revisiting procedural_memory step: {step_id}")
            except Exception as e:
                self.logger.error(f"Error revisiting procedural_memory step {step_id}: {e}")
        
        elif command_type == "complete" and self.get_active_procedural_memory():
            try:
                self.complete_procedural_memory()
                self.logger.info("Completed procedural_memory")
                # Tool relevance is no longer suspended during procedural_memorys
            except Exception as e:
                self.logger.error(f"Error completing procedural_memory: {e}")
        
        elif command_type == "cancel" and self.get_active_procedural_memory():
            try:
                self.cancel_procedural_memory()
                self.logger.info("Cancelled procedural_memory")
                # Tool relevance is no longer suspended during procedural_memorys
            except Exception as e:
                self.logger.error(f"Error cancelling procedural_memory: {e}")
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_procedural_memory_id": self.active_procedural_memory_id,
            "completed_steps": list(self.completed_steps),
            "available_steps": list(self.available_steps),
            "procedural_memory_data": self.procedural_memory_data
        }
    
    def from_dict(self, data: Dict[str, Any]) -> None:
        self.active_procedural_memory_id = data.get("active_procedural_memory_id")
        self.completed_steps = set(data.get("completed_steps", []))
        self.available_steps = set(data.get("available_steps", []))
        self.procedural_memory_data = data.get("procedural_memory_data", {})
        
        self.logger.info(f"Loaded procedural_memory manager state: active procedural_memory: {self.active_procedural_memory_id}")
        
        if self.active_procedural_memory_id:
            self._update_tool_access()
    
    def to_json(self) -> str:
        return to_json(self.to_dict())
    
    def from_json(self, json_str: str) -> None:
        data = from_json(json_str)
        self.from_dict(data)