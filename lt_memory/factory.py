"""
Factory for creating and managing LT_Memory service instances.

Replaces global singleton pattern with explicit dependency management,
enabling easier testing and clearer lifecycle control.
"""
import anthropic
import logging
from typing import Optional

from config.config import (
    LTMemoryConfig,
    ExtractionConfig,
    BatchingConfig,
    LinkingConfig,
    RefinementConfig,
    ProactiveConfig,
    EntityGarbageCollectionConfig
)
from lt_memory.db_access import LTMemoryDB
from lt_memory.vector_ops import VectorOps
from lt_memory.extraction import ExtractionService
from lt_memory.linking import LinkingService
from lt_memory.refinement import RefinementService
from lt_memory.batching import BatchingService
from lt_memory.proactive import ProactiveService
from lt_memory.entity_extraction import EntityExtractor
from lt_memory.entity_gc import EntityGCService
from utils.database_session_manager import LTMemorySessionManager, get_shared_session_manager

logger = logging.getLogger(__name__)

# Singleton instance
_lt_memory_factory_instance: Optional['LTMemoryFactory'] = None


class LTMemoryFactory:
    """
    Creates and manages all LT_Memory service instances with explicit dependencies.

    Usage:
        # Application initialization
        factory = LTMemoryFactory(
            config=config,
            session_manager=session_manager,
            embeddings_provider=embeddings,
            llm_provider=llm,
            anthropic_client=anthropic,
            conversation_repo=repo
        )

        # Access services
        memories = factory.extraction.extract_from_chunk(chunk)
        similar = factory.vector_ops.find_similar_memories(query)

        # Cleanup on shutdown
        factory.cleanup()

    Benefits over singleton pattern:
    - Explicit dependencies (no hidden global state)
    - Testable (create fresh instances with mocks)
    - Clear lifecycle (construct -> use -> cleanup)
    - Each factory instance is independent (thread-safe within instance)
    """

    def __init__(
        self,
        config: LTMemoryConfig,
        session_manager: LTMemorySessionManager,
        embeddings_provider,
        llm_provider,
        anthropic_client,
        conversation_repo
    ):
        """
        Initialize LT_Memory factory and create all service instances.

        Args:
            config: LT_Memory configuration
            session_manager: Database session manager
            embeddings_provider: Embeddings provider (AllMiniLM)
            llm_provider: LLM provider for extraction/linking/refinement
            anthropic_client: Anthropic SDK client for Batch API
            conversation_repo: Continuum repository for message loading
        """
        logger.info("Initializing LTMemoryFactory")

        self.config = config
        self._session_manager = session_manager
        self._embeddings_provider = embeddings_provider
        self._llm_provider = llm_provider
        self._anthropic_client = anthropic_client
        self._conversation_repo = conversation_repo

        # Track initialization order for reverse cleanup
        self._service_init_order = []

        # Build dependency graph in order
        self._init_services()

        logger.info("LTMemoryFactory initialization complete")

    def _init_services(self):
        """Initialize all services in dependency order, tracking for reverse cleanup."""
        try:
            # Layer 1: Database access (no dependencies)
            logger.debug("Initializing LTMemoryDB...")
            self.db = LTMemoryDB(self._session_manager)
            self._service_init_order.append(self.db)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize LTMemoryDB: {e}") from e

        try:
            # Layer 2: Vector operations (depends on db)
            logger.debug("Initializing VectorOps...")
            self.vector_ops = VectorOps(
                embeddings_provider=self._embeddings_provider,
                db=self.db
            )
            self._service_init_order.append(self.vector_ops)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize VectorOps: {e}") from e

        try:
            # Layer 3: Core services (depend on db + vector_ops)
            logger.debug("Initializing ExtractionService...")
            self.extraction = ExtractionService(
                config=self.config.extraction,
                vector_ops=self.vector_ops,
                db=self.db,
                llm_provider=self._llm_provider
            )
            self._service_init_order.append(self.extraction)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize ExtractionService: {e}") from e

        try:
            logger.debug("Initializing LinkingService...")
            self.linking = LinkingService(
                config=self.config.linking,
                vector_ops=self.vector_ops,
                db=self.db,
                llm_provider=self._llm_provider
            )
            self._service_init_order.append(self.linking)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize LinkingService: {e}") from e

        try:
            logger.debug("Initializing RefinementService...")
            self.refinement = RefinementService(
                config=self.config.refinement,
                vector_ops=self.vector_ops,
                db=self.db,
                llm_provider=self._llm_provider
            )
            self._service_init_order.append(self.refinement)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize RefinementService: {e}") from e

        try:
            # Layer 4: Higher-level services (depend on multiple services)
            logger.debug("Initializing BatchingService...")
            self.batching = BatchingService(
                config=self.config.batching,
                db=self.db,
                extraction_service=self.extraction,
                linking_service=self.linking,
                vector_ops=self.vector_ops,
                anthropic_client=self._anthropic_client,
                conversation_repo=self._conversation_repo,
                llm_provider=self._llm_provider
            )
            self._service_init_order.append(self.batching)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize BatchingService: {e}") from e

        try:
            logger.debug("Initializing ProactiveService...")
            self.proactive = ProactiveService(
                config=self.config.proactive,
                vector_ops=self.vector_ops,
                linking_service=self.linking,
                db=self.db
            )
            self._service_init_order.append(self.proactive)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize ProactiveService: {e}") from e

        try:
            logger.debug("Initializing EntityGCService...")
            self.entity_extractor = EntityExtractor()
            self._service_init_order.append(self.entity_extractor)

            self.entity_gc = EntityGCService(
                config=self.config.entity_gc,
                db=self.db,
                entity_extractor=self.entity_extractor,
                llm_provider=self._llm_provider
            )
            self._service_init_order.append(self.entity_gc)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize EntityGCService: {e}") from e

        logger.debug("All LT_Memory services initialized")

    def cleanup(self):
        """
        Clean up all service resources.

        Call this during application shutdown to release resources properly.
        Services are cleaned up in reverse initialization order automatically.
        """
        logger.info("Cleaning up LTMemoryFactory")

        # Cleanup in reverse initialization order
        for service in reversed(self._service_init_order):
            if service and hasattr(service, 'cleanup'):
                try:
                    service.cleanup()
                except Exception as e:
                    logger.warning(f"Error cleaning up {service.__class__.__name__}: {e}")

        self._service_init_order.clear()
        logger.info("LTMemoryFactory cleanup complete")

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"LTMemoryFactory(services=[db, vector_ops, extraction, linking, "
            f"refinement, batching, proactive, entity_gc])"
        )


def get_lt_memory_factory(
    config: LTMemoryConfig = None,
    session_manager: LTMemorySessionManager = None,
    embeddings_provider = None,
    llm_provider = None,
    anthropic_client = None,
    conversation_repo = None,
    force_new: bool = False
) -> LTMemoryFactory:
    """
    Get or create the singleton LTMemoryFactory instance.

    ARCHITECTURAL NOTE - Initialization Contract:
    =============================================
    This singleton uses a simple (non-thread-safe) pattern that relies on
    MIRA's sequential initialization contract:

    1. All singletons are created during app startup (main.py:lifespan)
    2. Initialization happens sequentially in a single thread
    3. Server only accepts requests AFTER all singletons are initialized
    4. After initialization, singletons are read-only (no concurrent writes)

    This pattern is shared by most MIRA singletons (orchestrator, conversation_repo,
    temporal_context) and is documented as intentional. Only database_session_manager
    uses thread-safe double-check locking because it manages mutable connection pools
    that could theoretically be accessed during initialization.

    Thread Safety: NOT thread-safe during initialization. If MIRA's initialization
    order changes (e.g., concurrent startup, dynamic plugin loading), this will need
    double-check locking with threading.RLock() like database_session_manager.

    Args:
        config: LT_Memory configuration (required on first call)
        session_manager: Database session manager (required on first call)
        embeddings_provider: Embeddings provider (required on first call)
        llm_provider: LLM provider (required on first call)
        anthropic_client: Anthropic SDK client (required on first call)
        conversation_repo: Continuum repository (required on first call)
        force_new: Force creation of a new instance (for testing)

    Returns:
        LTMemoryFactory singleton instance

    Raises:
        RuntimeError: If called without required arguments on first call
    """
    global _lt_memory_factory_instance

    if force_new and _lt_memory_factory_instance:
        logger.info("Forcing cleanup of existing LTMemoryFactory")
        _lt_memory_factory_instance.cleanup()
        _lt_memory_factory_instance = None

    if _lt_memory_factory_instance is None:
        # First call - all arguments required
        if not all([config, session_manager, embeddings_provider,
                    llm_provider, anthropic_client, conversation_repo]):
            raise RuntimeError(
                "First call to get_lt_memory_factory requires all arguments: "
                "config, session_manager, embeddings_provider, llm_provider, "
                "anthropic_client, conversation_repo"
            )

        logger.info("Creating new LTMemoryFactory singleton")
        _lt_memory_factory_instance = LTMemoryFactory(
            config=config,
            session_manager=session_manager,
            embeddings_provider=embeddings_provider,
            llm_provider=llm_provider,
            anthropic_client=anthropic_client,
            conversation_repo=conversation_repo
        )

    return _lt_memory_factory_instance
