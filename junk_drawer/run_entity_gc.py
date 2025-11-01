#!/usr/bin/env python3
"""
Standalone script for manual entity garbage collection runs.

Usage:
    # Dry run (preview only, no changes)
    python junk_drawer/run_entity_gc.py --dry-run

    # Execute for specific user
    python junk_drawer/run_entity_gc.py --user-id <uuid>

    # Execute for all users
    python junk_drawer/run_entity_gc.py --all-users

    # With custom dormancy threshold
    python junk_drawer/run_entity_gc.py --all-users --dormancy-days 60
"""
import argparse
import sys
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.config import Config
from utils.database_session_manager import LTMemorySessionManager
from utils.embeddings_provider import EmbeddingsProvider
from clients.llm_provider import LLMProvider
from lt_memory.factory import LTMemoryFactory
from lt_memory.schema.config import LTMemoryConfig, EntityGarbageCollectionConfig
from auth.database import AuthDatabase
from auth import set_current_user_id

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_entity_gc(
    user_id: str,
    entity_gc_service,
    dry_run: bool = False
) -> dict:
    """
    Run entity GC for a single user.

    Args:
        user_id: User ID
        entity_gc_service: EntityGCService instance
        dry_run: If True, preview only (no changes)

    Returns:
        Statistics dictionary
    """
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Running entity GC for user {user_id}")

    set_current_user_id(user_id)

    try:
        # Find dormant entities
        dormant = entity_gc_service.find_dormant_entities(user_id)

        if not dormant:
            logger.info(f"No dormant entities found for user {user_id}")
            return {"merged": 0, "deleted": 0, "kept": 0, "errors": 0}

        logger.info(f"Found {len(dormant)} dormant entities")

        # Preview dormant entities
        for entity in dormant[:10]:  # Show first 10
            logger.info(
                f"  - {entity.name} ({entity.entity_type}): "
                f"{entity.link_count} links, "
                f"last linked {entity.last_linked_at or 'never'}"
            )

        if len(dormant) > 10:
            logger.info(f"  ... and {len(dormant) - 10} more")

        if dry_run:
            logger.info(
                f"[DRY RUN] Would review {len(dormant)} entities with LLM. "
                f"Run without --dry-run to execute."
            )
            return {
                "merged": 0,
                "deleted": 0,
                "kept": 0,
                "errors": 0,
                "previewed": len(dormant)
            }

        # Execute GC
        stats = entity_gc_service.run_entity_gc_for_user(user_id)

        logger.info(
            f"Entity GC complete for user {user_id}: "
            f"{stats['merged']} merged, {stats['deleted']} deleted, "
            f"{stats['kept']} kept, {stats['errors']} errors"
        )

        return stats

    except Exception as e:
        logger.error(f"Error running entity GC for user {user_id}: {e}", exc_info=True)
        return {"merged": 0, "deleted": 0, "kept": 0, "errors": 1}
    finally:
        set_current_user_id(None)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run entity garbage collection for MIRA memory system"
    )
    parser.add_argument(
        "--user-id",
        help="User ID to run GC for (single user mode)"
    )
    parser.add_argument(
        "--all-users",
        action="store_true",
        help="Run GC for all users with memory enabled"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, no changes (shows dormant entities)"
    )
    parser.add_argument(
        "--dormancy-days",
        type=int,
        help="Override dormancy threshold (days without new links)"
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.user_id and not args.all_users:
        parser.error("Must specify either --user-id or --all-users")

    if args.user_id and args.all_users:
        parser.error("Cannot specify both --user-id and --all-users")

    try:
        # Initialize config
        logger.info("Loading configuration...")
        config = Config.load()

        # Initialize session manager
        logger.info("Initializing database session manager...")
        session_manager = LTMemorySessionManager(config.lt_memory_db_config)

        # Initialize providers
        logger.info("Initializing embeddings provider...")
        embeddings_provider = EmbeddingsProvider()

        logger.info("Initializing LLM provider...")
        llm_provider = LLMProvider()

        # Initialize LT_Memory factory
        logger.info("Initializing LT_Memory factory...")

        # Override entity GC config if needed
        lt_memory_config = LTMemoryConfig()
        if args.dormancy_days:
            lt_memory_config.entity_gc.dormancy_days = args.dormancy_days
            logger.info(f"Using custom dormancy threshold: {args.dormancy_days} days")

        factory = LTMemoryFactory(
            config=lt_memory_config,
            session_manager=session_manager,
            embeddings_provider=embeddings_provider,
            llm_provider=llm_provider,
            anthropic_client=None,  # Not needed for GC
            conversation_repo=None   # Not needed for GC
        )

        # Get users to process
        if args.user_id:
            user_ids = [args.user_id]
            logger.info(f"Running for single user: {args.user_id}")
        else:
            auth_db = AuthDatabase()
            users = auth_db.get_users_with_memory_enabled()
            user_ids = [str(user["id"]) for user in users]
            logger.info(f"Running for {len(user_ids)} users with memory enabled")

        # Run GC for each user
        total_stats = {"merged": 0, "deleted": 0, "kept": 0, "errors": 0}

        for user_id in user_ids:
            stats = run_entity_gc(
                user_id=user_id,
                entity_gc_service=factory.entity_gc,
                dry_run=args.dry_run
            )

            total_stats["merged"] += stats.get("merged", 0)
            total_stats["deleted"] += stats.get("deleted", 0)
            total_stats["kept"] += stats.get("kept", 0)
            total_stats["errors"] += stats.get("errors", 0)

        # Summary
        logger.info("=" * 60)
        logger.info(f"{'[DRY RUN] ' if args.dry_run else ''}Entity GC Summary")
        logger.info("=" * 60)
        logger.info(f"Users processed: {len(user_ids)}")
        logger.info(f"Entities merged: {total_stats['merged']}")
        logger.info(f"Entities deleted: {total_stats['deleted']}")
        logger.info(f"Entities kept: {total_stats['kept']}")
        logger.info(f"Errors: {total_stats['errors']}")
        logger.info("=" * 60)

        if args.dry_run:
            logger.info("\nThis was a DRY RUN. No changes were made.")
            logger.info("Run without --dry-run to execute entity GC.")

        # Cleanup
        logger.info("Cleaning up...")
        factory.cleanup()

        # Exit with error code if there were errors
        if total_stats["errors"] > 0:
            logger.warning(f"Completed with {total_stats['errors']} errors")
            sys.exit(1)

        logger.info("Entity GC completed successfully")
        sys.exit(0)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
