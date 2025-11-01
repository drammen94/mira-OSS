#!/usr/bin/env python3
"""
Command-line tool for manually triggering memory extraction or refinement.

Usage:
    python run_memory_consolidation.py extract            # Run extraction/consolidation
    python run_memory_consolidation.py refine             # Run memory refinement
    python run_memory_consolidation.py extract --user ID  # Run for specific user
    python run_memory_consolidation.py --verbose          # Show detailed output
"""

import argparse
import logging
import sys
from typing import Optional

# Setup logging
def setup_logging(verbose: bool = False):
    """Configure logging for the script."""
    level = logging.DEBUG if verbose else logging.INFO
    format_str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s' if verbose else '%(message)s'

    logging.basicConfig(
        level=level,
        format=format_str,
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    # Reduce noise from HTTP libraries unless verbose
    if not verbose:
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def run_extraction(user_id: Optional[str] = None):
    """Trigger the memory extraction service."""
    logger.info("🧠 Starting memory extraction/consolidation...")

    from lt_memory.memory_extraction_service import MemoryExtractionService

    try:
        service = MemoryExtractionService()

        if user_id:
            logger.info(f"Running for user: {user_id}")
            results = service.run_extraction_for_user(user_id)
        else:
            logger.info("Running manual extraction for all users...")
            results = service.run_extraction_for_all_users()

        # Print summary
        logger.info("✅ Extraction complete:")
        logger.info(f"   Users processed: {results.get('users_processed', 0)}")
        logger.info(f"   Memories extracted: {results.get('memories_extracted', 0)}")
        logger.info(f"   Memories stored: {results.get('memories_stored', 0)}")

        if results.get('errors'):
            logger.warning(f"   ⚠️  Errors: {len(results['errors'])}")

        return 0

    except Exception as e:
        logger.error(f"❌ Extraction failed: {e}")
        return 1


def run_refinement(user_id: Optional[str] = None):
    """Trigger the memory refinement service."""
    logger.info("✨ Starting memory refinement...")

    try:
        # Import and initialize the refinement pipeline
        from lt_memory.memory_refiner import MemoryRefiner
        from lt_memory.vector_store import VectorStore
        from clients.hybrid_embeddings_provider import get_hybrid_embeddings_provider
        from utils.database_session_manager import LTMemorySessionManager
        from config.config_manager import config
        from clients.vault_client import get_api_key
        from auth.database import AuthDatabase
        from auth import set_current_user_id

        # Setup components (this matches what extraction service does)
        lt_memory_config = config.lt_memory
        embeddings = get_hybrid_embeddings_provider()
        session_manager = LTMemorySessionManager()
        vector_store = VectorStore(lt_memory_config, embeddings, session_manager)

        # Setup LLM for refinement
        from clients.llm_provider import GenericProviderClient
        provider_key = get_api_key("openrouter_key")
        llm_provider = GenericProviderClient(
            api_key=provider_key,
            model="google/gemini-2.0-flash-exp:free",
            api_endpoint="https://openrouter.ai/api/v1/chat/completions"
        )

        refiner = MemoryRefiner(lt_memory_config, llm_provider)

        if user_id:
            # Single user refinement
            logger.info(f"Finding refinement candidates for user: {user_id}")

            # Set user context
            set_current_user_id(user_id)

            try:
                candidates = refiner.identify_refinement_candidates(vector_store, user_id)

                if not candidates:
                    logger.info("✅ No memories need refinement")
                    return 0

                logger.info(f"Found {len(candidates)} candidates, refining...")
                refined = refiner.refine_memory_batch(candidates)

                if refined:
                    # Process the refined memories
                    from lt_memory.memory_processor import MemoryProcessor
                    processor = MemoryProcessor(lt_memory_config, vector_store)
                    results = processor.process_memories(refined, user_id)

                    logger.info(f"✅ Refinement complete:")
                    logger.info(f"   Candidates found: {len(candidates)}")
                    logger.info(f"   Memories refined: {len(refined)}")
                    logger.info(f"   Updates stored: {results.get('stored', 0)}")
                else:
                    logger.info("✅ No improvements found during refinement")
            finally:
                set_current_user_id(None)

        else:
            # All users refinement
            logger.info("Running refinement for all users...")
            auth_db = AuthDatabase()
            users = auth_db.get_users_with_memory_enabled()

            total_candidates = 0
            total_refined = 0
            total_stored = 0

            for user in users:
                user_id = str(user['id'])
                logger.info(f"\nProcessing user {user_id}...")

                # Set user context
                set_current_user_id(user_id)

                try:
                    candidates = refiner.identify_refinement_candidates(vector_store, user_id)

                    if not candidates:
                        logger.info(f"  No memories need refinement for user {user_id}")
                        continue

                    logger.info(f"  Found {len(candidates)} candidates")
                    refined = refiner.refine_memory_batch(candidates)

                    if refined:
                        from lt_memory.memory_processor import MemoryProcessor
                        processor = MemoryProcessor(lt_memory_config, vector_store)
                        results = processor.process_memories(refined, user_id)

                        total_candidates += len(candidates)
                        total_refined += len(refined)
                        total_stored += results.get('stored', 0)

                        logger.info(f"  Refined {len(refined)} memories, stored {results.get('stored', 0)} updates")

                except Exception as e:
                    logger.error(f"  Failed to refine memories for user {user_id}: {e}")
                    continue
                finally:
                    set_current_user_id(None)

            logger.info(f"\n✅ All-users refinement complete:")
            logger.info(f"   Total candidates: {total_candidates}")
            logger.info(f"   Total refined: {total_refined}")
            logger.info(f"   Total stored: {total_stored}")

        return 0

    except Exception as e:
        logger.error(f"❌ Refinement failed: {e}")
        if logger.isEnabledFor(logging.DEBUG):
            import traceback
            traceback.print_exc()
        return 1


def run_consolidation(user_id: Optional[str] = None, similarity: float = 0.85,
                      stable_days: int = 7, max_clusters: int = 10, force: bool = False):
    """Trigger similarity-based memory consolidation."""
    logger.info("🔄 Starting similarity-based memory consolidation...")

    try:
        # Import and initialize components
        from lt_memory.memory_refiner import MemoryRefiner
        from lt_memory.vector_store import VectorStore
        from clients.hybrid_embeddings_provider import get_hybrid_embeddings_provider
        from utils.database_session_manager import LTMemorySessionManager
        from config.config_manager import config
        from clients.vault_client import get_api_key
        from auth.database import AuthDatabase
        from auth import set_current_user_id

        # Setup components
        lt_memory_config = config.lt_memory
        embeddings = get_hybrid_embeddings_provider()
        session_manager = LTMemorySessionManager()
        vector_store = VectorStore(lt_memory_config, embeddings, session_manager)

        # Setup LLM for consolidation
        from clients.llm_provider import GenericProviderClient
        provider_key = get_api_key("openrouter_key")
        llm_provider = GenericProviderClient(
            api_key=provider_key,
            model="google/gemini-2.0-flash-exp:free",
            api_endpoint="https://openrouter.ai/api/v1/chat/completions"
        )

        refiner = MemoryRefiner(lt_memory_config, llm_provider)

        if user_id:
            # Single user consolidation
            logger.info(f"Finding consolidation candidates for user: {user_id}")

            # Use 0 days if force flag is set
            effective_stable_days = 0 if force else stable_days

            if force:
                logger.info(f"⚠️  FORCE mode: Processing ALL memories regardless of stability")
            logger.info(f"Parameters: similarity={similarity}, stable_days={effective_stable_days}, max_clusters={max_clusters}")

            # Set user context
            set_current_user_id(user_id)

            try:
                clusters = refiner.identify_consolidation_candidates(
                    vector_store, user_id,
                    similarity_threshold=similarity,
                    stable_days=effective_stable_days,
                    max_clusters=max_clusters
                )

                if not clusters:
                    logger.info("✅ No consolidation opportunities found")
                    return 0

                logger.info(f"Found {len(clusters)} potential consolidation clusters")

                # Consolidate the clusters
                consolidated = refiner.consolidate_memory_clusters(clusters)

                if consolidated:
                    # Process the consolidated memories
                    from lt_memory.memory_processor import MemoryProcessor
                    processor = MemoryProcessor(lt_memory_config, vector_store)
                    results = processor.process_memories(consolidated, user_id)

                    logger.info(f"✅ Consolidation complete:")
                    logger.info(f"   Clusters analyzed: {len(clusters)}")
                    logger.info(f"   Consolidations created: {len(consolidated)}")
                    logger.info(f"   Updates stored: {results.get('stored', 0)}")
                else:
                    logger.info("✅ No consolidations needed after analysis")
            finally:
                set_current_user_id(None)

        else:
            # All users consolidation
            logger.info("Running consolidation for all users...")

            # Use 0 days if force flag is set
            effective_stable_days = 0 if force else stable_days

            if force:
                logger.info(f"⚠️  FORCE mode: Processing ALL memories regardless of stability")
            logger.info(f"Parameters: similarity={similarity}, stable_days={effective_stable_days}, max_clusters={max_clusters}")

            auth_db = AuthDatabase()
            users = auth_db.get_users_with_memory_enabled()

            total_clusters = 0
            total_consolidated = 0
            total_stored = 0

            for user in users:
                user_id = str(user['id'])
                logger.info(f"\nProcessing user {user_id}...")

                # Set user context
                set_current_user_id(user_id)

                try:
                    clusters = refiner.identify_consolidation_candidates(
                        vector_store, user_id,
                        similarity_threshold=similarity,
                        stable_days=effective_stable_days,
                        max_clusters=max_clusters
                    )

                    if not clusters:
                        logger.info(f"  No consolidation opportunities for user {user_id}")
                        continue

                    logger.info(f"  Found {len(clusters)} clusters")
                    consolidated = refiner.consolidate_memory_clusters(clusters)

                    if consolidated:
                        from lt_memory.memory_processor import MemoryProcessor
                        processor = MemoryProcessor(lt_memory_config, vector_store)
                        results = processor.process_memories(consolidated, user_id)

                        total_clusters += len(clusters)
                        total_consolidated += len(consolidated)
                        total_stored += results.get('stored', 0)

                        logger.info(f"  Created {len(consolidated)} consolidations, stored {results.get('stored', 0)} updates")

                except Exception as e:
                    logger.error(f"  Failed to consolidate for user {user_id}: {e}")
                    continue
                finally:
                    set_current_user_id(None)

            logger.info(f"\n✅ All-users consolidation complete:")
            logger.info(f"   Total clusters: {total_clusters}")
            logger.info(f"   Total consolidated: {total_consolidated}")
            logger.info(f"   Total stored: {total_stored}")

        return 0

    except Exception as e:
        logger.error(f"❌ Consolidation failed: {e}")
        if logger.isEnabledFor(logging.DEBUG):
            import traceback
            traceback.print_exc()
        return 1


def main():
    parser = argparse.ArgumentParser(
        description='Trigger memory extraction/consolidation or refinement',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    subparsers.required = True

    # Extract command
    extract_parser = subparsers.add_parser('extract', help='Run memory extraction and consolidation')
    extract_parser.add_argument('--user', '-u', type=str, help='Specific user ID to process')
    extract_parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed output')

    # Refine command
    refine_parser = subparsers.add_parser('refine', help='Run memory refinement')
    refine_parser.add_argument('--user', '-u', type=str, help='Specific user ID (optional, defaults to all users)')
    refine_parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed output')

    # Consolidate command
    consolidate_parser = subparsers.add_parser('consolidate', help='Run similarity-based memory consolidation')
    consolidate_parser.add_argument('--user', '-u', type=str, help='Specific user ID (optional, defaults to all users)')
    consolidate_parser.add_argument('--similarity', '-s', type=float, default=0.85, help='Similarity threshold (0.0-1.0, default 0.85)')
    consolidate_parser.add_argument('--stable-days', '-d', type=int, default=7, help='Only consider memories not updated in N days (default 7)')
    consolidate_parser.add_argument('--max-clusters', '-m', type=int, default=10, help='Maximum clusters to process (default 10)')
    consolidate_parser.add_argument('--force', '-f', action='store_true', help='Skip stability requirements, process ALL memories')
    consolidate_parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed output')

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Run the appropriate command
    if args.command == 'extract':
        sys.exit(run_extraction(args.user))
    elif args.command == 'refine':
        sys.exit(run_refinement(args.user))
    elif args.command == 'consolidate':
        sys.exit(run_consolidation(
            args.user,
            args.similarity,
            args.stable_days,
            args.max_clusters,
            args.force
        ))


if __name__ == "__main__":
    main()
