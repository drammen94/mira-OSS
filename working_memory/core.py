"""
Event-driven working memory core.

Coordinates trinkets and system prompt composition through CNS events.
All operations are synchronous - events are published and handled immediately.
"""
import logging
from typing import Dict, List, Optional, TYPE_CHECKING

from .composer import SystemPromptComposer, ComposerConfig

if TYPE_CHECKING:
    from cns.integration.event_bus import EventBus

logger = logging.getLogger(__name__)


class WorkingMemory:
    """
    Event-driven working memory coordinator.
    
    This class orchestrates the system prompt composition by:
    1. Managing trinket registrations
    2. Routing UpdateTrinketEvent to specific trinkets
    3. Collecting trinket content via TrinketContentEvent
    4. Composing and publishing the final system prompt
    
    All operations are synchronous to maintain simplicity.
    """
    
    def __init__(self, event_bus: 'EventBus', composer_config: Optional[ComposerConfig] = None):
        """
        Initialize working memory with event bus connection.
        
        Args:
            event_bus: CNS event bus for subscribing and publishing
            composer_config: Optional configuration for the composer
        """
        self.event_bus = event_bus
        self.composer = SystemPromptComposer(composer_config)
        self._trinkets: Dict[str, object] = {}
        self._current_continuum_id: Optional[str] = None
        self._current_user_id: Optional[str] = None
        ## @CLAUDE Why are these optional?
        
        # Subscribe to core events
        # System prompt composition request
        self.event_bus.subscribe('ComposeSystemPromptEvent', self._handle_compose_prompt)
        
        # Trinket update routing
        self.event_bus.subscribe('UpdateTrinketEvent', self._handle_update_trinket)
        
        # Trinket content collection
        self.event_bus.subscribe('TrinketContentEvent', self._handle_trinket_content)
        
        logger.debug("Subscribed to working memory events")
        logger.info("WorkingMemory initialized")
        
    def register_trinket(self, trinket: object) -> None:
        """
        Register a trinket with working memory.
        
        Args:
            trinket: Trinket instance to register
        """
        trinket_name = trinket.__class__.__name__
        self._trinkets[trinket_name] = trinket
        logger.info(f"Registered trinket: {trinket_name}")
    
    def _handle_compose_prompt(self, event) -> None:
        """
        Handle request to compose system prompt.
        
        This triggers all trinkets to update and then composes the final prompt.
        """
        from cns.core.events import ComposeSystemPromptEvent, UpdateTrinketEvent, SystemPromptComposedEvent
        event: ComposeSystemPromptEvent
        
        # Store context for future trinket updates
        self._current_continuum_id = event.continuum_id
        self._current_user_id = event.user_id
        
        # Set base prompt
        self.composer.set_base_prompt(event.base_prompt)
        
        # Clear previous sections except base
        self.composer.clear_sections(preserve_base=True)
        
        # Request updates from all registered trinkets
        for trinket_name in self._trinkets.keys():
            self.event_bus.publish(UpdateTrinketEvent.create(
                continuum_id=event.continuum_id,
                target_trinket=trinket_name,
                context={'user_id': event.user_id}  # Provide user_id for trinkets that need it
            ))
        
        # After all trinkets have updated (synchronously), compose the prompt
        structured = self.composer.compose()

        # Publish composed prompt event with structured content
        self.event_bus.publish(SystemPromptComposedEvent.create(
            continuum_id=event.continuum_id,
            cached_content=structured['cached_content'],
            non_cached_content=structured['non_cached_content']
        ))

        logger.info(f"Composed and published structured system prompt (cached: {len(structured['cached_content'])} chars, non-cached: {len(structured['non_cached_content'])} chars)")
    
    def _handle_update_trinket(self, event) -> None:
        """
        Route update request to specific trinket.

        Event handler continues processing even if individual trinkets fail,
        but distinguishes infrastructure failures from logic errors for observability.
        """
        from cns.core.events import UpdateTrinketEvent
        event: UpdateTrinketEvent

        trinket = self._trinkets.get(event.target_trinket)
        if not trinket:
            logger.warning(f"No trinket registered with name: {event.target_trinket}")
            return

        # Call the trinket's update method if it has one
        if hasattr(trinket, 'handle_update_request'):
            try:
                trinket.handle_update_request(event)
                logger.debug(f"Routed update to {event.target_trinket}")
            except Exception as e:
                # Event handler continues - isolate trinket failures
                # Use exception type to distinguish infrastructure from logic errors
                error_type = type(e).__name__
                if 'Database' in error_type or 'Valkey' in error_type or 'Connection' in error_type:
                    logger.error(
                        f"Infrastructure failure in trinket {event.target_trinket}: {e}",
                        exc_info=True,
                        extra={'error_category': 'infrastructure'}
                    )
                else:
                    logger.error(
                        f"Trinket {event.target_trinket} failed: {e}",
                        exc_info=True,
                        extra={'error_category': 'logic'}
                    )
        else:
            logger.warning(f"Trinket {event.target_trinket} has no handle_update_request method")
    
    def _handle_trinket_content(self, event) -> None:
        """
        Handle trinket content updates.

        Trinkets publish their sections which we add to the composer.
        """
        from cns.core.events import TrinketContentEvent
        event: TrinketContentEvent

        self.composer.add_section(event.variable_name, event.content, cache_policy=event.cache_policy)
        logger.debug(f"Received content for '{event.variable_name}' from {event.trinket_name} (cache={event.cache_policy})")
    
    def publish_trinket_update(self, target_trinket: str, context: Optional[Dict] = None) -> None:
        """
        Publish an update request for a specific trinket.
        
        This is used when external events need to trigger specific trinket updates.
        For example, when memories are surfaced, we update ProactiveMemoryTrinket.
        
        Args:
            target_trinket: Name of the trinket class to update
            context: Optional context data for the trinket
        """
        if not self._current_continuum_id or not self._current_user_id:
            logger.warning("No active continuum context for trinket update")
            return
        
        from cns.core.events import UpdateTrinketEvent

        self.event_bus.publish(UpdateTrinketEvent.create(
            continuum_id=self._current_continuum_id,
            target_trinket=target_trinket,
            context=context or {}
        ))
    
    
    def get_active_trinkets(self) -> List[str]:
        """
        Get list of registered trinket names.

        Returns:
            List of trinket class names
        """
        return list(self._trinkets.keys())

    def get_trinket(self, name: str) -> Optional[object]:
        """
        Get a registered trinket by name.

        Used when components need direct access to trinket state (e.g., orchestrator
        accessing ProactiveMemoryTrinket's cached memories for retention evaluation).

        Args:
            name: Trinket class name (e.g., 'ProactiveMemoryTrinket')

        Returns:
            Trinket instance or None if not found
        """
        return self._trinkets.get(name)

    ## @CLAUDE I see no mentions of this being invoked in the codebase. Dead code?
    
    def cleanup_all_trinkets(self) -> None:
        """
        Clean up all registered trinkets.
        
        Calls cleanup method on each trinket if available.
        """
        for trinket_name, trinket in self._trinkets.items():
            try:
                if hasattr(trinket, 'cleanup'):
                    trinket.cleanup()
                    logger.debug(f"Cleaned up trinket: {trinket_name}")
            except Exception as e:
                logger.error(f"Error cleaning up {trinket_name}: {e}")
        
        self._trinkets.clear()
        logger.info("All trinkets cleaned up")
        
        ## @CLAUDE This also only appears once. Dead code?