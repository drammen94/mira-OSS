"""
LT_Memory Module - Long-term memory system for MIRA.

Factory-based initialization with explicit dependency management.
"""
import logging

from config.config import LTMemoryConfig
from lt_memory.factory import LTMemoryFactory, get_lt_memory_factory
from lt_memory.db_access import LTMemoryDB
from lt_memory.vector_ops import VectorOps
from lt_memory.extraction import ExtractionService
from lt_memory.linking import LinkingService
from lt_memory.refinement import RefinementService
from lt_memory.batching import BatchingService
from lt_memory.proactive import ProactiveService
from lt_memory.models import (
    Memory,
    ExtractedMemory,
    MemoryLink,
    Entity,
    ProcessingChunk,
    ExtractionBatch,
    PostProcessingBatch,
    RefinementCandidate,
    ConsolidationCluster
)

logger = logging.getLogger(__name__)

__all__ = [
    # Factory
    'LTMemoryFactory',
    'get_lt_memory_factory',

    # Classes (for type hints)
    'LTMemoryDB',
    'VectorOps',
    'ExtractionService',
    'LinkingService',
    'RefinementService',
    'BatchingService',
    'ProactiveService',
    'LTMemoryConfig',

    # Models
    'Memory',
    'ExtractedMemory',
    'MemoryLink',
    'Entity',
    'ProcessingChunk',
    'ExtractionBatch',
    'PostProcessingBatch',
    'RefinementCandidate',
    'ConsolidationCluster',
]
