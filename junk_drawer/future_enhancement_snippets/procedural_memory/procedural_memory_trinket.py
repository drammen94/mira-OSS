"""Procedural memory trinket for displaying active procedural memory information."""
import logging
from typing import Dict, Any, Optional

from .base import EventAwareTrinket

logger = logging.getLogger(__name__)


class ProceduralMemoryTrinket(EventAwareTrinket):
    """
    Manages procedural memory guidance for the system prompt.
    
    Receives procedural memory state and generates comprehensive procedural memory guidance.
    """
    
    def _get_variable_name(self) -> str:
        """Procedural memory content publishes to 'procedural_memory_guidance'."""
        return "procedural_memory_guidance"
    
    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate procedural memory guidance content.
        
        Args:
            context: Update context containing procedural memory information:
                - procedural_memory_id: Active procedural memory ID
                - procedural_memory_content: Dict with header, steps, data, checklist, navigation
                - procedural_memory_hint: Optional hint text
            
        Returns:
            Formatted procedural memory section or empty string
        """
        procedural_memory_id = context.get('procedural_memory_id')
        if not procedural_memory_id:
            logger.debug("No active procedural memory")
            return ""
        
        # Start with procedural memory hint if provided
        parts = []
        procedural_memory_hint = context.get('procedural_memory_hint')
        if procedural_memory_hint:
            parts.append(procedural_memory_hint)
        
        # Add procedural memory content if provided
        procedural_memory_content = context.get('procedural_memory_content', {})
        
        if procedural_memory_content:
            # Header
            if 'header' in procedural_memory_content:
                parts.append(procedural_memory_content['header'])
            
            # Steps
            if 'steps' in procedural_memory_content:
                for step_id in sorted(procedural_memory_content['steps'].keys()):
                    parts.append(procedural_memory_content['steps'][step_id])
            
            # Data fields
            if 'data' in procedural_memory_content:
                for field in sorted(procedural_memory_content['data'].keys()):
                    parts.append(procedural_memory_content['data'][field])
            
            # Checklist
            if 'checklist' in procedural_memory_content:
                parts.append(procedural_memory_content['checklist'])
            
            # Navigation
            if 'navigation' in procedural_memory_content:
                parts.append(procedural_memory_content['navigation'])
        
        if not parts:
            return f"# Active Procedural Memory: {procedural_memory_id}\nNo procedural memory content available."
        
        # Join all parts with appropriate spacing
        result = "\n\n".join(filter(None, parts))
        
        logger.debug(f"Generated procedural memory guidance for {procedural_memory_id}")
        return result