"""
Unified Relevance State Manager for continuum context and tool persistence.

This module consolidates ConversationContextManager + ToolPersistenceManager
into a single state management component. ALL EXISTING BUGS AND COMPLEXITY
ARE PRESERVED during this faithful merge.

CRITICAL BUGS PRESERVED:
- Race conditions in tool_activation_history operations
- Race conditions in message history operations  
- Non-atomic message counter increment
- Thread safety issues in concurrent state modifications
- Unsafe tool repository operations
"""
import logging
from collections import deque
from typing import Dict, List, Optional, Any

from config import config
from tools.repo import ToolRepository


class RelevanceState:
    """
    Unified state manager for continuum context and tool persistence.
    
    This consolidates:
    - ConversationContextManager: message history, topic changes, message counter
    - ToolPersistenceManager: tool activation tracking, persistence rules, enable/disable
    
    ALL ORIGINAL BUGS AND RACE CONDITIONS ARE PRESERVED.
    """
    
    def __init__(self, tool_repo: ToolRepository):
        """
        Initialize the RelevanceState.
        
        Args:
            tool_repo: Repository of available tools
        """
        self.logger = logging.getLogger("relevance_state")
        self.tool_repo = tool_repo
        
        # Continuum context management (from ConversationContextManager)
        self.context_window_size = config.tool_relevance.context_window_size
        self.message_history: deque[str] = deque(maxlen=self.context_window_size)
        self.topic_coherence_threshold = config.tool_relevance.topic_coherence_threshold
        self.message_counter = 0
        self.topic_changed = False
        
        # Tool persistence management (from ToolPersistenceManager)
        self.tool_persistence_messages = config.tool_relevance.tool_persistence_messages
        self.tool_activation_history: Dict[str, int] = {}  # Maps tool_name to last_activation_message_id
    
    def build_contextual_message(self, message: str) -> str:
        """
        Build a contextual message by considering recent message history.
        
        This method uses the topic_changed flag set by the continuum manager
        to determine whether to maintain continuum context or start fresh.
        
        PRESERVES: All original context building logic and potential race conditions
        
        Args:
            message: Current user message
            
        Returns:
            Enhanced message with relevant context (if applicable)
        """
        if not self.message_history:
            # No history yet, just store the message and return as is
            self.message_history.append(message)
            return message
        
        # Check if topic has changed based on the flag
        # If topic has not changed, add to history; otherwise clear history
        if not self.topic_changed:
            self.logger.info("Topic continuing - maintaining continuum context")
            self.message_history.append(message)
            return message
        else:
            # Topic has changed - treat as topic change, clear history and start fresh
            self.logger.info("Topic changed - starting new continuum context")
            self.message_history.clear()
            self.message_history.append(message)
            # Reset the flag after handling the topic change
            self.topic_changed = False
            return message
    
    def set_topic_changed(self, topic_changed: bool) -> None:
        """
        Set the topic changed flag for tool relevance context management.
        
        This method is called by the continuum manager after analyzing
        the LLM's response for topic change tags.
        
        Args:
            topic_changed: Boolean indicating whether the topic has changed
        """
        self.topic_changed = topic_changed
        self.logger.info(f"Tool relevance topic change flag set to: {topic_changed}")
    
    def increment_message_counter(self) -> int:
        """
        Increment and return the message counter.
        
        BUG PRESERVED: Non-atomic increment creates race conditions
        
        Returns:
            Current message counter value
        """
        # BUG: This should be atomic but isn't (preserving original bug)
        self.message_counter += 1
        return self.message_counter
    
    def get_message_counter(self) -> int:
        """
        Get current message counter value.
        
        Returns:
            Current message counter
        """
        return self.message_counter
    
    def clear_history(self) -> None:
        """
        Clear message history.
        
        This is typically called when topic changes are detected.
        """
        self.message_history.clear()
        self.logger.info("Message history cleared")
    
    def get_history_length(self) -> int:
        """
        Get current message history length.
        
        Returns:
            Number of messages in history
        """
        return len(self.message_history)
    
    def get_persistent_tools(self, current_message_id: int) -> List[str]:
        """
        Get tools that should persist due to recent activation.
        
        This method identifies tools that were activated recently enough
        that they should remain enabled according to the persistence rule.
        
        Args:
            current_message_id: ID of the current message being processed
            
        Returns:
            List of tool names that should persist
        """
        persistent_tools = []
        
        for tool_name, activation_message_id in self.tool_activation_history.items():
            # Keep tools enabled if they were activated within the persistence window
            messages_since_activation = current_message_id - activation_message_id
            
            if messages_since_activation < self.tool_persistence_messages:
                self.logger.info(f"Tool {tool_name} persisting due to recent activation ({messages_since_activation}/{self.tool_persistence_messages} messages ago)")
                persistent_tools.append(tool_name)
        
        return persistent_tools
    
    def update_activation_history(self, tool_names: List[str], message_id: int) -> None:
        """
        Update activation history for newly relevant tools.
        
        BUG PRESERVED: Race conditions in concurrent access to activation history
        
        Args:
            tool_names: List of tool names that were activated
            message_id: Current message ID
        """
        # BUG: No locking around dictionary modification (preserving original bug)
        for tool_name in tool_names:
            self.tool_activation_history[tool_name] = message_id
    
    def enable_tools(self, tools_to_enable: List[str]) -> List[str]:
        """
        Enable the specified tools in the tool repository.
        
        Args:
            tools_to_enable: List of tool names to enable
            
        Returns:
            List of successfully enabled tool names
        """
        enabled_tools = []
        
        for tool_name in tools_to_enable:
            try:
                if not self.tool_repo.is_tool_enabled(tool_name):
                    self.tool_repo.enable_tool(tool_name)
                    self.logger.info(f"Enabled tool: {tool_name}")
                
                enabled_tools.append(tool_name)
            
            except Exception as e:
                self.logger.error(f"Error enabling tool {tool_name}: {e}")
        
        return enabled_tools
    
    def disable_irrelevant_tools(self, current_relevant_tools: List[str]) -> None:
        """
        Disable tools that are no longer relevant to the continuum.
        
        This method compares the current set of relevant tools (including
        persistent tools) with previously enabled tools and disables those
        that are no longer needed.
        
        Args:
            current_relevant_tools: List of currently relevant tool names (including persistent tools)
        """
        # Get currently enabled tools from repo
        enabled_tools = self.tool_repo.get_enabled_tools()
        
        # Identify tools to disable (enabled but no longer relevant or persistent)
        to_disable = [tool for tool in enabled_tools if tool not in current_relevant_tools]
        
        if to_disable:
            self.logger.info(f"Disabling {len(to_disable)} tools that are no longer relevant: {', '.join(to_disable)}")
            
            for tool_name in to_disable:
                try:
                    self.tool_repo.disable_tool(tool_name)
                    # Also remove from activation history if we're disabling
                    # BUG PRESERVED: Race condition in dictionary access during iteration
                    if tool_name in self.tool_activation_history:
                        del self.tool_activation_history[tool_name]
                except Exception as e:
                    self.logger.error(f"Error disabling tool {tool_name}: {e}")
    
    def clear_activation_history(self) -> None:
        """
        Clear all tool activation history.
        
        This is typically used when resetting the system state.
        """
        # BUG PRESERVED: No thread synchronization
        self.tool_activation_history.clear()
        self.logger.info("Tool activation history cleared")
    
    def get_activation_count(self) -> int:
        """
        Get count of tools in activation history.
        
        Returns:
            Number of tools currently tracked in activation history
        """
        return len(self.tool_activation_history)
    
    def reset_state(self) -> None:
        """
        Reset all state to initial conditions.
        
        PRESERVES: No thread synchronization (original bugs)
        """
        # BUG PRESERVED: No atomic reset, race conditions possible
        self.message_history.clear()
        self.message_counter = 0
        self.topic_changed = False
        self.tool_activation_history.clear()
        
        self.logger.info("All relevance state reset")
    
    def get_state_summary(self) -> Dict[str, Any]:
        """
        Get current state summary for debugging and monitoring.
        
        Returns:
            Dictionary containing state information
        """
        return {
            "message_counter": self.message_counter,
            "history_length": len(self.message_history),
            "topic_changed": self.topic_changed,
            "active_tools_count": len(self.tool_activation_history),
            "persistence_window": self.tool_persistence_messages,
            "context_window_size": self.context_window_size
        }