"""
Migration script to add encrypted__ prefix to PII columns in user databases.

This script migrates existing user databases from plain column names (name, email, phone,
title, description, additional_notes) to prefixed column names (encrypted__name, etc.)
to make encryption boundaries explicit throughout the system.
"""

import sqlite3
import sys
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def migrate_contacts_table(conn):
    """Migrate contacts table to use encrypted__ prefixed columns."""
    cursor = conn.cursor()

    # Check if old schema exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'")
    if not cursor.fetchone():
        logger.info("No contacts table found, skipping")
        return

    # Check if already migrated
    cursor.execute("PRAGMA table_info(contacts)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'encrypted__name' in columns:
        logger.info("Contacts table already migrated, skipping")
        return

    logger.info("Migrating contacts table...")

    # Create new table with encrypted__ prefixes
    cursor.execute("""
        CREATE TABLE contacts_new (
            id TEXT PRIMARY KEY,
            encrypted__name TEXT NOT NULL,
            encrypted__email TEXT,
            encrypted__phone TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Copy data from old table to new table
    cursor.execute("""
        INSERT INTO contacts_new (id, encrypted__name, encrypted__email, encrypted__phone, created_at, updated_at)
        SELECT id, name, email, phone, created_at, updated_at
        FROM contacts
    """)

    # Drop old table
    cursor.execute("DROP TABLE contacts")

    # Rename new table
    cursor.execute("ALTER TABLE contacts_new RENAME TO contacts")

    # Recreate indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(LOWER(encrypted__name))")

    conn.commit()
    logger.info("Contacts table migrated successfully")


def migrate_reminders_table(conn):
    """Migrate reminders table to use encrypted__ prefixed columns."""
    cursor = conn.cursor()

    # Check if old schema exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'")
    if not cursor.fetchone():
        logger.info("No reminders table found, skipping")
        return

    # Check if already migrated
    cursor.execute("PRAGMA table_info(reminders)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'encrypted__title' in columns:
        logger.info("Reminders table already migrated, skipping")
        return

    logger.info("Migrating reminders table...")

    # Create new table with encrypted__ prefixes
    cursor.execute("""
        CREATE TABLE reminders_new (
            id TEXT PRIMARY KEY,
            encrypted__title TEXT NOT NULL,
            encrypted__description TEXT,
            reminder_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            completed_at TEXT,
            contact_uuid TEXT,
            encrypted__additional_notes TEXT,
            category TEXT DEFAULT 'user'
        )
    """)

    # Copy data from old table to new table
    cursor.execute("""
        INSERT INTO reminders_new
        (id, encrypted__title, encrypted__description, reminder_date, created_at, updated_at,
         completed, completed_at, contact_uuid, encrypted__additional_notes, category)
        SELECT id, title, description, reminder_date, created_at, updated_at,
               completed, completed_at, contact_uuid, additional_notes,
               COALESCE(category, 'user')
        FROM reminders
    """)

    # Drop old table
    cursor.execute("DROP TABLE reminders")

    # Rename new table
    cursor.execute("ALTER TABLE reminders_new RENAME TO reminders")

    # Recreate indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_date ON reminders(reminder_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_completed ON reminders(completed)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_contact ON reminders(contact_uuid)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_category ON reminders(category)")

    conn.commit()
    logger.info("Reminders table migrated successfully")


def migrate_database(db_path: Path):
    """Migrate a single user database."""
    logger.info(f"Migrating database: {db_path}")

    try:
        conn = sqlite3.connect(str(db_path))

        migrate_contacts_table(conn)
        migrate_reminders_table(conn)

        conn.close()
        logger.info(f"Successfully migrated: {db_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to migrate {db_path}: {e}")
        return False


def main():
    """Find and migrate all user databases."""
    users_dir = Path("data/users")

    if not users_dir.exists():
        logger.info("No users directory found, nothing to migrate")
        return

    # Find all userdata.db files
    db_files = list(users_dir.glob("*/userdata.db"))

    if not db_files:
        logger.info("No user databases found")
        return

    logger.info(f"Found {len(db_files)} user databases to migrate")

    success_count = 0
    fail_count = 0

    for db_file in db_files:
        if migrate_database(db_file):
            success_count += 1
        else:
            fail_count += 1

    logger.info(f"\nMigration complete:")
    logger.info(f"  Successful: {success_count}")
    logger.info(f"  Failed: {fail_count}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
