import hashlib
import json
import logging
import os
from typing import Dict, List, Any, Tuple, Set

from config import config


class ExampleManager:
    
    def __init__(self, tools_data_dir: str):
        self.logger = logging.getLogger("example_manager")
        self.tools_data_dir = tools_data_dir
        self.tool_examples: Dict[str, Dict[str, Any]] = {}
    
    def load_tool_examples_for_tools(self, discovered_tools: Set[str]) -> Tuple[Dict[str, Dict[str, Any]], bool]:
        classifier_needs_retrain = False
        
        try:
            file_hashes = {}
            tools_needing_examples = []
            
            for tool_name in discovered_tools:
                tool_data_dir = os.path.join(self.tools_data_dir, tool_name)
                examples_file = os.path.join(tool_data_dir, "classifier_examples.json")
                autogen_examples_file = os.path.join(tool_data_dir, "embedding_trigger_examples.json")
                
                if os.path.exists(examples_file):
                    try:
                        file_hash = self._calculate_file_hash(examples_file)
                        file_hashes[examples_file] = file_hash
                        
                        with open(examples_file, 'r') as f:
                            examples = json.load(f)
                        
                        self.logger.warning(f"Using custom examples for {tool_name} - may be less comprehensive")
                        
                        self.tool_examples[tool_name] = {
                            "examples": examples,
                            "file_hash": file_hash,
                            "is_autogen": False
                        }
                    except Exception as e:
                        self.logger.error(f"Error loading examples from {examples_file}: {e}")
                
                elif os.path.exists(autogen_examples_file):
                    try:
                        file_hash = self._calculate_file_hash(autogen_examples_file)
                        file_hashes[autogen_examples_file] = file_hash
                        
                        with open(autogen_examples_file, 'r') as f:
                            examples = json.load(f)
                        
                        self.tool_examples[tool_name] = {
                            "examples": examples,
                            "file_hash": file_hash,
                            "is_autogen": True
                        }
                    except Exception as e:
                        self.logger.error(f"Error loading auto-generated examples from {autogen_examples_file}: {e}")
                
                else:
                    tools_needing_examples.append(tool_name)
            
            if tools_needing_examples:
                regenerated = self._handle_synthetic_example_generation(tools_needing_examples, file_hashes)
                if regenerated:
                    classifier_needs_retrain = True
            
            old_hashes = self._load_old_file_hashes()
            for file_path, new_hash in file_hashes.items():
                if file_path not in old_hashes or old_hashes[file_path] != new_hash:
                    if not classifier_needs_retrain:
                        classifier_needs_retrain = True
            
            for file_path in old_hashes:
                if file_path not in file_hashes:
                    classifier_needs_retrain = True
            
            self._save_file_hashes(file_hashes)
            
            self.logger.info(f"Loaded {len(self.tool_examples)} tools, retrain needed: {classifier_needs_retrain}")
            return self.tool_examples, classifier_needs_retrain
        
        except Exception as e:
            self.logger.error(f"Error loading tool examples: {e}")
            return {}, True
    
    def _handle_synthetic_example_generation(self, tools_needing_examples: List[str], file_hashes: Dict[str, str]) -> bool:
        old_hashes = self._load_old_file_hashes()
        
        needs_regen = False
        
        if not old_hashes:
            needs_regen = True
        else:
            for tool_name in tools_needing_examples:
                try:
                    tools_dir = 'tools'
                    if hasattr(config, 'paths') and hasattr(config.paths, 'tools_dir'):
                        tools_dir = config.paths.tools_dir
                    
                    tool_file_path = os.path.join(tools_dir, f"{tool_name}.py")
                    
                    if os.path.exists(tool_file_path):
                        current_hash = self._calculate_file_hash(tool_file_path)
                        old_hash = old_hashes.get(tool_file_path)
                        
                        file_hashes[tool_file_path] = current_hash
                        
                        if old_hash != current_hash:
                            needs_regen = True
                            break
                except Exception as e:
                    self.logger.error(f"Error checking tool source for {tool_name}: {e}")
        
        if needs_regen:
            self.logger.info(f"Generating examples for {len(tools_needing_examples)} tools")
            self._generate_synthetic_examples(tools_needing_examples)
            
            for tool_name in tools_needing_examples:
                try:
                    tool_data_dir = os.path.join(self.tools_data_dir, tool_name)
                    autogen_examples_file = os.path.join(tool_data_dir, "embedding_trigger_examples.json")
                    
                    if os.path.exists(autogen_examples_file):
                        file_hash = self._calculate_file_hash(autogen_examples_file)
                        file_hashes[autogen_examples_file] = file_hash
                        
                        with open(autogen_examples_file, 'r') as f:
                            examples = json.load(f)
                        
                        self.tool_examples[tool_name] = {
                            "examples": examples,
                            "file_hash": file_hash,
                            "is_autogen": True
                        }
                except Exception as e:
                    self.logger.error(f"Error loading newly generated examples for {tool_name}: {e}")
            
            return True
        
        return False
    
    def get_all_examples(self) -> List[Dict[str, Any]]:
        all_examples = []
        for tool_data in self.tool_examples.values():
            all_examples.extend(tool_data["examples"])
        return all_examples
    
    def _generate_synthetic_examples(self, tool_names: List[str]) -> None:
        try:
            from utils.synthetic_toolexample_generator import SyntheticToolExampleGenerator
            
            generator = SyntheticToolExampleGenerator()
            
            for tool_name in tool_names:
                try:
                    tools_dir = 'tools'
                    if hasattr(config, 'paths') and hasattr(config.paths, 'tools_dir'):
                        tools_dir = config.paths.tools_dir
                    
                    tool_file_path = os.path.join(tools_dir, f"{tool_name}.py")
                    
                    if not os.path.exists(tool_file_path):
                        self.logger.warning(f"Tool file not found for {tool_name}, skipping generation")
                        continue
                    
                    output_path = os.path.join(self.tools_data_dir, tool_name)
                    os.makedirs(output_path, exist_ok=True)
                    
                    capability_examples = generator.generate_all(
                        tool_path=tool_file_path,
                        examples_per_capability=15,
                        output_path=output_path
                    )
                    
                    examples = []
                    for capability_name, cap_examples in capability_examples.items():
                        examples.extend(cap_examples)
                    
                except Exception as tool_err:
                    self.logger.error(f"Error generating examples for {tool_name}: {tool_err}")
        
        except Exception as e:
            self.logger.error(f"Error in synthetic example generation: {e}")
    
    def _load_old_file_hashes(self) -> Dict[str, str]:
        hashes_file = os.path.join(self.tools_data_dir, "classifier_file_hashes.json")
        
        if os.path.exists(hashes_file):
            try:
                with open(hashes_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning(f"Error reading file hashes: {e}")
        
        return {}
    
    def _save_file_hashes(self, file_hashes: Dict[str, str]) -> None:
        hashes_file = os.path.join(self.tools_data_dir, "classifier_file_hashes.json")
        
        try:
            with open(hashes_file, 'w') as f:
                json.dump(file_hashes, f)
        except Exception as e:
            self.logger.error(f"Error saving file hashes: {e}")
    
    def _calculate_file_hash(self, file_path: str) -> str:
        try:
            hash_obj = hashlib.sha256()
            
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hash_obj.update(chunk)
            
            return hash_obj.hexdigest()
        except Exception as e:
            self.logger.error(f"Error calculating hash for {file_path}: {e}")
            raise