# Event-Aware Trinket Architecture

## Overview

The Event-Aware Trinket pattern enables modular system prompt composition through a decoupled plugin architecture. Trinkets are self-registering components that respond to events, generate content, and contribute sections to the system prompt without central orchestration.

## Implementation

### Base Trinket Class

All trinkets inherit from `EventAwareTrinket`, which handles event subscription and content publishing:

```python
# working_memory/trinkets/base.py:17-43

class EventAwareTrinket:
    """
    Base class for event-driven trinkets.

    Trinkets inherit from this class to:
    1. Receive update requests via UpdateTrinketEvent
    2. Generate content when requested
    3. Publish their content via TrinketContentEvent
    """

    def __init__(self, event_bus: 'EventBus', working_memory: 'WorkingMemory'):
        self.event_bus = event_bus
        self.working_memory = working_memory
        self._variable_name: str = self._get_variable_name()

        # Register with working memory
        self.working_memory.register_trinket(self)

        logger.info(f"{self.__class__.__name__} initialized and registered")
```

### Event-Driven Update Flow

When an update is requested, trinkets generate content and publish it back:

```python
# working_memory/trinkets/base.py:56-85

def handle_update_request(self, event) -> None:
    """
    Handle an update request from working memory.

    Generates content and publishes it.
    """
    try:
        # Generate content
        content = self.generate_content(event.context)

        # Publish if we have content
        if content and content.strip():
            self.event_bus.publish(TrinketContentEvent.create(
                continuum_id=event.continuum_id,
                user_id=event.user_id,
                variable_name=self._variable_name,
                content=content,
                trinket_name=self.__class__.__name__
            ))
            logger.debug(f"{self.__class__.__name__} published content ({len(content)} chars)")

    except Exception as e:
        logger.error(f"Error updating {self.__class__.__name__}: {e}")
```

### Working Memory Coordination

The WorkingMemory class orchestrates trinkets without knowing their implementation details:

```python
# working_memory/core.py:94-112

def _handle_compose_prompt(self, event) -> None:
    """
    Handle request to compose system prompt.

    This triggers all trinkets to update and then composes the final prompt.
    """
    # Set base prompt
    self.composer.set_base_prompt(event.base_prompt)

    # Request updates from all registered trinkets
    for trinket_name in self._trinkets.keys():
        self.event_bus.publish(UpdateTrinketEvent.create(
            continuum_id=event.continuum_id,
            user_id=event.user_id,
            target_trinket=trinket_name,
            context={}
        ))

    # After all trinkets have updated (synchronously), compose the prompt
    composed_prompt = self.composer.compose()
```

### Self-Registration Pattern

Trinkets register themselves during initialization:

```python
# working_memory/core.py:62-72

def register_trinket(self, trinket: object) -> None:
    """
    Register a trinket with working memory.

    Args:
        trinket: Trinket instance to register
    """
    trinket_name = trinket.__class__.__name__
    self._trinkets[trinket_name] = trinket
    logger.info(f"Registered trinket: {trinket_name}")
```

## Event Flow

1. **ComposeSystemPromptEvent** → WorkingMemory requests all trinkets to update
2. **UpdateTrinketEvent** → Each trinket receives update request with context
3. **TrinketContentEvent** → Trinkets publish their generated content
4. **SystemPromptComposedEvent** → Final composed prompt is published

## Key Properties

### Decoupled
Trinkets don't know about each other or the composition order. They only know how to generate their own content based on context.

### Self-Contained
Each trinket manages its own lifecycle, from registration to content generation. No external configuration required.

### Extensible
New trinkets can be added by simply inheriting from `EventAwareTrinket` and implementing `generate_content()`. The system automatically discovers and integrates them.

### Fail-Safe
Individual trinket failures don't crash the system. Failed trinkets simply don't contribute content to the prompt.

## Design Rationale

Traditional prompt composition requires central orchestration with explicit knowledge of all components. This pattern instead:

1. Allows trinkets to be added/removed without modifying core code
2. Enables trinkets to decide when they have relevant content
3. Maintains clear separation of concerns
4. Provides natural extension points for new functionality

The architecture treats system prompt sections as independent plugins that can be composed in any order, making the system highly modular and maintainable.