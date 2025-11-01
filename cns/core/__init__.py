"""
CNS Core Domain - Pure business logic with no external dependencies.

This package contains the core domain objects and business rules for the
Central Nervous System (CNS). All objects here are immutable and
contain only pure business logic.
"""

from .message import Message
from .continuum import Continuum
from .state import ContinuumState
from .events import ContinuumEvent

__all__ = [
    'Message',
    'Continuum',
    'ContinuumState',
    'ContinuumEvent'
]