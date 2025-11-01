"""
Tool Relevance Engine - Consolidated Architecture

This package contains the consolidated tool relevance system extracted and merged
from the original monolithic tool_relevance_engine.py. All complexity and bugs
are preserved exactly from the original implementation, now organized into
6 focused, well-bounded components.

Components:
- ToolRelevanceService: Main CNS integration point (simplified orchestrator)
- ClassificationEngine: ML classification + matrix operations + embedding cache
- ExampleManager: Example loading, synthetic generation, and change detection
- ToolDiscovery: Tool discovery and data directory management
- RelevanceState: Continuum context + tool persistence + state management
- CacheManager: Cross-cutting unified caching (injected into components)

Architecture Benefits:
- CNS Integration: Same clean 4-method interface
- Maintainability: 6 focused components with clear single responsibilities  
- Bug Consolidation: Related bugs now consolidated for easier systematic fixing
- Reduced Coupling: Pipeline architecture with cleaner boundaries

Usage:
    from .tool_relevance_service import ToolRelevanceService
    
    service = ToolRelevanceService(tool_repo, model)
    enabled_tools = service.manage_tool_relevance(user_message)
"""

from .tool_relevance_service import ToolRelevanceService

__all__ = ['ToolRelevanceService']