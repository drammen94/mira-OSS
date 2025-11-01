#!/usr/bin/env python3
"""
Schema migration script for tool databases.

Run this when deploying schema changes to apply updates to all existing user databases.

Usage:
    python scripts/migrate_schemas.py --schema contacts_tool   # Apply specific schema
    python scripts/migrate_schemas.py --all                    # Apply all schemas
    python scripts/migrate_schemas.py --check                  # Check which users need updates
    python scripts/migrate_schemas.py --backup                 # Backup all user databases
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tools.schema_distribution import (
    apply_schema_to_all_users,
    apply_all_schemas_to_all_users
)


def backup_all_databases():
    """Backup all user databases before schema migration."""
    users_dir = Path("data/users")

    if not users_dir.exists():
        print("❌ No users directory found")
        return {"success": 0, "failed": 0}

    # Create backup directory with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(f"data/backups/user_databases_{timestamp}")
    backup_dir.mkdir(parents=True, exist_ok=True)

    print(f"Backing up databases to: {backup_dir}")
    print()

    success_count = 0
    failed_count = 0

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue

        user_id = user_dir.name
        db_path = user_dir / "userdata.db"

        if not db_path.exists():
            continue

        try:
            backup_path = backup_dir / f"{user_id}_userdata.db"
            shutil.copy2(db_path, backup_path)

            size_kb = db_path.stat().st_size / 1024
            success_count += 1
            print(f"✓ {user_id}: {size_kb:.1f}KB backed up")

        except Exception as e:
            failed_count += 1
            print(f"✗ {user_id}: backup failed - {e}")

    print()
    print(f"Backed up {success_count} databases to {backup_dir}")

    if failed_count > 0:
        print(f"⚠️  {failed_count} backups failed")

    return {"success": success_count, "failed": failed_count, "backup_dir": str(backup_dir)}


def check_user_databases():
    """Check which users have databases and report status."""
    users_dir = Path("data/users")

    if not users_dir.exists():
        print("❌ No users directory found")
        return

    user_dirs = [d for d in users_dir.iterdir() if d.is_dir()]

    if not user_dirs:
        print("No user directories found")
        return

    print(f"Found {len(user_dirs)} user directories")
    print()

    has_db_count = 0
    no_db_count = 0

    for user_dir in user_dirs:
        user_id = user_dir.name
        db_path = user_dir / "userdata.db"

        if db_path.exists():
            has_db_count += 1
            size_kb = db_path.stat().st_size / 1024
            print(f"✓ {user_id}: {size_kb:.1f}KB")
        else:
            no_db_count += 1
            print(f"✗ {user_id}: no database")

    print()
    print(f"Summary: {has_db_count} with database, {no_db_count} without")


def main():
    parser = argparse.ArgumentParser(
        description="Apply schema updates to user databases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Apply specific schema to all users:
    python scripts/migrate_schemas.py --schema contacts_tool

  Apply all schemas to all users (development/major migration):
    python scripts/migrate_schemas.py --all

  Check database status:
    python scripts/migrate_schemas.py --check
        """
    )

    parser.add_argument(
        "--schema",
        help="Specific schema to apply (e.g., contacts_tool)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Apply all schemas to all users"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check which users have databases"
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Backup all user databases (automatically done before migrations)"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip automatic backup before migration (not recommended)"
    )

    args = parser.parse_args()

    if args.check:
        print("Checking user databases...")
        print()
        check_user_databases()

    elif args.backup:
        print("Backing up user databases...")
        print()
        backup_all_databases()

    elif args.all:
        print("Applying all schemas to all users...")
        print("This will re-run all schema files (they must be idempotent)")
        print()

        # Automatic backup unless explicitly skipped
        if not args.no_backup:
            print("Creating backup first...")
            print()
            backup_result = backup_all_databases()
            print()

            if backup_result["success"] == 0:
                print("❌ No databases to backup - aborting migration")
                return

        response = input("Continue with migration? [y/N]: ")
        if response.lower() != 'y':
            print("Aborted")
            return

        print()
        results = apply_all_schemas_to_all_users()

        print()
        print(f"✓ Updated: {results['updated']} users")
        print(f"✗ Failed: {results['failed']} users")

        if results['updated'] > 0:
            print()
            print("Success! All schemas applied to existing users")

    elif args.schema:
        print(f"Applying {args.schema} schema to all users...")
        print()

        # Automatic backup unless explicitly skipped
        if not args.no_backup:
            print("Creating backup first...")
            print()
            backup_result = backup_all_databases()
            print()

            if backup_result["success"] == 0:
                print("❌ No databases to backup - aborting migration")
                return

        try:
            results = apply_schema_to_all_users(args.schema)

            print()
            print(f"✓ Success: {len(results['success'])} users")
            print(f"✗ Failed: {len(results['failed'])} users")

            if results['failed']:
                print()
                print("Failures:")
                for failure in results['failed']:
                    print(f"  {failure['user_id']}: {failure['error']}")

            if results['success']:
                print()
                print(f"Success! {args.schema} schema applied to {len(results['success'])} users")

        except ValueError as e:
            print(f"✗ Error: {e}")
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
