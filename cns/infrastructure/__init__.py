"""
CNS Infrastructure Layer - External system integrations.

This package contains adapters for external systems like databases,
caching, and other infrastructure concerns.
"""

from .continuum_repository import ContinuumRepository

__all__ = [
    'ContinuumRepository'
]