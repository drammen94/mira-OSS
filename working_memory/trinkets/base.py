"""
Event-aware base trinket class.

Provides common functionality for all trinkets to participate in the
event-driven working memory system.
"""
import logging
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from cns.integration.event_bus import EventBus
    from working_memory.core import WorkingMemory

logger = logging.getLogger(__name__)


class EventAwareTrinket:
    """
    Base class for event-driven trinkets.

    Trinkets inherit from this class to:
    1. Receive update requests via UpdateTrinketEvent
    2. Generate content when requested
    3. Publish their content via TrinketContentEvent
    """

    # Cache policy for this trinket's content
    # True = content should be cached (static content like tool guidance)
    # False = content changes frequently, don't cache (default)
    cache_policy: bool = False

    def __init__(self, event_bus: 'EventBus', working_memory: 'WorkingMemory'):
        """
        Initialize the trinket with event bus connection.

        Args:
            event_bus: CNS event bus for publishing content
            working_memory: Working memory instance for registration
        """
        self.event_bus = event_bus
        self.working_memory = working_memory
        self._variable_name: str = self._get_variable_name()

        # Register with working memory
        self.working_memory.register_trinket(self)

        logger.info(f"{self.__class__.__name__} initialized and registered")
    
    def _get_variable_name(self) -> str:
        """
        Get the variable name this trinket publishes.
        
        Subclasses should override this to specify their section name.
        
        Returns:
            Variable name for system prompt composition
        """
        # Default implementation - subclasses should override
        return self.__class__.__name__.lower() + "_section"
    
    def handle_update_request(self, event) -> None:
        """
        Handle an update request from working memory.

        Generates content and publishes it. Infrastructure failures propagate
        to the event handler in core.py for proper isolation and logging.

        Args:
            event: UpdateTrinketEvent with context
        """
        from cns.core.events import UpdateTrinketEvent, TrinketContentEvent
        event: UpdateTrinketEvent

        # Generate content - let infrastructure failures propagate
        content = self.generate_content(event.context)

        # Publish if we have content
        if content and content.strip():
            self.event_bus.publish(TrinketContentEvent.create(
                continuum_id=event.continuum_id,
                variable_name=self._variable_name,
                content=content,
                trinket_name=self.__class__.__name__,
                cache_policy=self.cache_policy
            ))
            logger.debug(f"{self.__class__.__name__} published content ({len(content)} chars, cache={self.cache_policy})")
    
    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate content for this trinket.
        
        Subclasses must implement this method to generate their
        specific content based on the provided context.
        
        Args:
            context: Context from UpdateTrinketEvent
            
        Returns:
            Generated content string or empty string if no content
        """
        raise NotImplementedError("Subclasses must implement generate_content()")
    
    def cleanup(self) -> None:
        """
        Clean up trinket resources.
        
        Subclasses can override this to perform specific cleanup.
        """
        logger.debug(f"{self.__class__.__name__} cleaned up")