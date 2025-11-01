"""
Tool Relevance Service - Simplified CNS Integration Point

This module provides the primary interface for the CNS orchestrator to interact
with the consolidated tool relevance system. The service is now much simpler,
coordinating only 3 substantial components instead of 6 thin ones.

ALL EXISTING BUGS AND COMPLEXITY ARE PRESERVED from the original monolithic
implementation - they are just consolidated into fewer, more substantial components.
"""
import logging
import os
import time
from typing import List, Tuple, Dict, Any, Optional

from config import config
from tools.repo import ToolRepository
from cns.core.embedded_message import EmbeddedMessage

from .classification_engine import ClassificationEngine
from .example_manager import ExampleManager
from .tool_discovery import ToolDiscovery
from .relevance_state import RelevanceState


class ToolRelevanceService:
    """
    Simplified service for tool relevance management in CNS architecture.
    
    This service coordinates the consolidated tool relevance components and provides
    a clean interface for the CNS orchestrator. All original complexity and bugs are
    preserved in the consolidated internal components.
    
    CNS Integration Points:
    - get_relevant_tools(embedded_msg) -> List[Dict] of tool definitions
    - set_topic_changed(bool) from topic change events
    - suspend()/resume() for workflow management
    
    Consolidated Architecture:
    - ClassificationEngine: ML classification + matrix operations + embedding cache
    - ExampleManager: Tool discovery + example management + synthetic generation  
    - RelevanceState: Continuum context + tool persistence + state management
    """
    
    def __init__(self, tool_repo: ToolRepository, model):
        """
        Initialize the ToolRelevanceService.
        
        Args:
            tool_repo: Repository of available tools
            model: Pre-loaded ONNX embedding model to use
        """
        self.logger = logging.getLogger("tool_relevance_service")
        self.tool_repo = tool_repo
        self.suspended = False
        
        # Initialize the data directories
        self.tools_data_dir = os.path.join(config.paths.data_dir, "tools")
        
        # Initialize consolidated components
        self.tool_discovery = ToolDiscovery(self.tools_data_dir)
        self.example_manager = ExampleManager(self.tools_data_dir)
        self.classification_engine = ClassificationEngine(
            thread_limit=config.tool_relevance.thread_limit,
            cache_dir=os.path.join(config.paths.data_dir, "classifier"),
            model=model
        )
        self.relevance_state = RelevanceState(tool_repo)
        
        # Load examples and prepare the system
        self._initialize_system()
    
    def _initialize_system(self) -> None:
        """
        Initialize the tool relevance system following proper orchestration flow:
        1. Tool Discovery → "What tools exist in the system?"
        2. Example Management → "For these discovered tools, what examples do we have?"
        3. Classification → "Train on the examples we found"
        """
        self.logger.info("Initializing tool relevance system")
        
        try:
            self.logger.info("Step 1: Discovering available tools")
            discovered_tools = self.tool_discovery.discover_tools()
            existing_tools = self.tool_discovery.get_existing_tool_directories()
            all_tools = discovered_tools.union(existing_tools)
            
            if not all_tools:
                self.logger.warning("No tools discovered in the system")
                return
            
            self.logger.info(f"Discovered {len(all_tools)} tools: {', '.join(sorted(all_tools))}")
            
            self.tool_discovery.create_tool_data_directories(all_tools)
            
            self.logger.info("Step 2: Loading examples for discovered tools")
            tool_examples, needs_retrain = self.example_manager.load_tool_examples_for_tools(all_tools)
            
            if not tool_examples:
                self.logger.warning("No tool examples loaded for any discovered tools")
                return
            
            # Get all examples for training
            all_examples = self.example_manager.get_all_examples()
            
            if all_examples:
                self.logger.info(f"Step 3: Training classifier with {len(all_examples)} examples")
                self.classification_engine.train_classifier(all_examples, force_retrain=needs_retrain)
                
                # Precompute embeddings matrix
                self.classification_engine.precompute_tool_embeddings_matrix()
                
                self.logger.info("Tool relevance system initialization completed successfully")
            else:
                self.logger.warning("No examples available for classifier training")
        
        except Exception as e:
            self.logger.error(f"Error initializing tool relevance system: {e}")
            raise
    
    def get_relevant_tools(self, embedded_msg: EmbeddedMessage) -> List[Dict[str, Any]]:
        """
        Main CNS integration method: Get relevant tools using pre-computed embeddings.
        
        This method uses the embedded message to determine relevant tools without
        generating new embeddings, significantly improving performance.
        
        Args:
            embedded_msg: EmbeddedMessage with pre-computed embeddings
            
        Returns:
            List of tool definitions for relevant tools
        """
        start_time = time.time()
        self.logger.debug("Getting relevant tools using embedded message")
        
        # Check if service is suspended
        if self.suspended:
            self.logger.info("Tool relevance service is suspended, returning all tools")
            return self.tool_repo.get_all_tool_definitions() if self.tool_repo else []
        
        # Get current message ID for tracking
        current_message_id = self.relevance_state.increment_message_counter()
        
        try:
            # Analyze using embedded message
            tool_relevance = self._analyze_embedded_message(embedded_msg)
            
            # Get tool names from relevance scores
            newly_relevant_tools = [tool[0] for tool in tool_relevance] if tool_relevance else []
            
            # Get persistent tools
            persistent_tools = self.relevance_state.get_persistent_tools(current_message_id)
            
            # Combine newly relevant tools with persistent tools
            tools_to_enable = list(set(newly_relevant_tools + persistent_tools))
            
            # Update activation history
            if newly_relevant_tools:
                self.relevance_state.update_activation_history(newly_relevant_tools, current_message_id)
            
            if not tools_to_enable:
                self.logger.debug("No relevant tools found, returning all tools")
                return self.tool_repo.get_all_tool_definitions() if self.tool_repo else []
            
            # Get tool definitions for relevant tools
            tool_definitions = []
            for tool_name in tools_to_enable:
                tool_def = self.tool_repo.get_tool_definition(tool_name)
                if tool_def:
                    tool_definitions.append(tool_def)
            
            end_time = time.time()
            execution_time = (end_time - start_time) * 1000
            self.logger.debug(f"Tool relevance determined in {execution_time:.2f}ms")
            
            return tool_definitions if tool_definitions else self.tool_repo.get_all_tool_definitions()
        
        except Exception as e:
            self.logger.error(f"Error getting relevant tools: {e}")
            # Fallback to all tools on error
            return self.tool_repo.get_all_tool_definitions() if self.tool_repo else []
    
    def _analyze_embedded_message(self, embedded_msg: EmbeddedMessage) -> List[Tuple[str, float]]:
        """
        Analyze an embedded message to find the most relevant tools with confidence scores.
        
        Uses pre-computed embeddings for efficient classification.
        
        Args:
            embedded_msg: EmbeddedMessage with pre-computed embeddings
            
        Returns:
            List of (tool_name, confidence_score) tuples
        """
        try:
            # Use the classification engine with embedded message
            relevant_tools = self.classification_engine.classify_message_with_scores(embedded_msg)
            
            if not relevant_tools:
                self.logger.debug("No relevant tools identified for this message")
                return []
            
            # Log the relevant tools with scores
            tool_names = [f"{tool}({score:.2f})" for tool, score in relevant_tools[:3]]
            self.logger.debug(f"Relevant tools: {', '.join(tool_names)}")
            
            return relevant_tools
        
        except Exception as e:
            self.logger.error(f"Error analyzing embedded message: {e}")
            return []
    
    def set_topic_changed(self, topic_changed: bool) -> None:
        """
        Set the topic changed flag for tool relevance context management.
        
        This is called by CNS when topic change events are detected.
        Delegates to consolidated state manager.
        
        Args:
            topic_changed: Boolean indicating whether the topic has changed
        """
        self.relevance_state.set_topic_changed(topic_changed)
    
    def suspend(self) -> None:
        """
        Suspend automatic tool suggestion.
        
        When suspended, the service will not analyze messages or enable tools.
        This is used by CNS workflow management.
        """
        self.suspended = True
        self.logger.info("Tool relevance service suspended")
    
    def resume(self) -> None:
        """
        Resume automatic tool suggestion.
        
        This re-enables the service's analysis of messages and automatic tool enabling.
        """
        self.suspended = False
        self.logger.info("Tool relevance service resumed")
    
    def get_system_status(self) -> Dict[str, Any]:
        """
        Get current system status for debugging and monitoring.
        
        Returns:
            Dictionary containing system status information from consolidated components
        """
        # Get state summary from consolidated components
        state_summary = self.relevance_state.get_state_summary()
        cache_stats = self.classification_engine.cache_manager.get_cache_stats()
        
        return {
            "suspended": self.suspended,
            "classifiers_loaded": len(self.classification_engine.classifiers),
            "examples_loaded": len(self.example_manager.tool_examples),
            "matrix_available": self.classification_engine.tool_embeddings_matrix is not None,
            **state_summary,
            **cache_stats
        }