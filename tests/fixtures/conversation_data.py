"""
Simple test user management for integration tests.

Handles cleaning up test user data and loading realistic continuum from JSON.
"""
import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import List
import pytest

from cns.core.message import Message
from cns.core.continuum import Continuum
from cns.infrastructure.continuum_repository import ContinuumRepository
from clients.postgres_client import PostgresClient
from utils.user_context import set_current_user_id
from utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

# Hardcoded test user for consistent testing
TEST_USER_ID = "443a898d-ed56-495a-b9de-0551c80169fe"
TEST_USER_EMAIL = "test@example.com"

# Second test user for RLS testing
SECOND_TEST_USER_ID = "7b8e4c2a-f3d1-4a5b-9e6c-1d2f3a4b5c6d"
SECOND_TEST_USER_EMAIL = "test2@example.com"

def cleanup_test_user_data():
    """
    Clean up all test user data while preserving the user records.

    This comprehensive cleanup removes:
    - All continuum history (messages, conversations)
    - Authentication tokens (sessions, magic_links)
    - User credentials
    - All memories and embeddings

    Cleans up both primary and secondary test users.
    The user records themselves are preserved for reuse across tests.
    """
    from auth.database import AuthDatabase
    from clients.postgres_client import PostgresClient

    auth_db = AuthDatabase()

    # Clean up both test users
    for user_email in [TEST_USER_EMAIL, SECOND_TEST_USER_EMAIL]:
        # Get the actual test user ID
        user_record = auth_db.get_user_by_email(user_email)
        if not user_record:
            logger.debug(f"User {user_email} doesn't exist - skipping cleanup")
            continue

        actual_user_id = user_record["id"]
        logger.info(f"Cleaning up test data for user {actual_user_id} ({user_email})")

        try:
            # Set user context for RLS
            set_current_user_id(actual_user_id)

            # Clean up all test data (unified mira_service database)
            db = PostgresClient("mira_service", user_id=actual_user_id)
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    # Delete in order respecting foreign key constraints
                    cur.execute("DELETE FROM messages WHERE user_id = %s", (actual_user_id,))
                    cur.execute("DELETE FROM continuums WHERE user_id = %s", (actual_user_id,))
                    cur.execute("DELETE FROM sessions WHERE user_id = %s", (actual_user_id,))
                    cur.execute("DELETE FROM magic_links WHERE user_id = %s", (actual_user_id,))
                    cur.execute("DELETE FROM user_credentials WHERE user_id = %s", (actual_user_id,))
                    cur.execute("DELETE FROM memories WHERE user_id = %s", (actual_user_id,))
                    conn.commit()
                    logger.debug(f"Cleaned up test data from mira_service for {user_email}")

            try:
                pass  # All cleanup now in unified database above
                logger.debug("Cleaned up mira_memory data")
            except Exception as e:
                logger.warning(f"Failed to clean up memory data: {e}")

            # Clean up SQLite user database (tool data)
            try:
                import sqlite3
                from pathlib import Path
                user_db_path = Path(f"data/users/{actual_user_id}/userdata.db")
                if user_db_path.exists():
                    conn = sqlite3.connect(str(user_db_path))
                    cursor = conn.cursor()

                    # Get all table names
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [row[0] for row in cursor.fetchall()]

                    # Delete all data from all tables
                    for table in tables:
                        cursor.execute(f"DELETE FROM {table}")

                    conn.commit()
                    conn.close()
                    logger.debug(f"Cleaned up SQLite database for {user_email}")
            except Exception as e:
                logger.warning(f"Failed to clean up SQLite database: {e}")

            # Clean up any cached data in Valkey/Redis
            try:
                from clients.valkey_client import get_valkey
                valkey = get_valkey()
                # Clean user-specific cache keys
                cache_patterns = [
                    f"user:{actual_user_id}:*",
                    f"continuum:{actual_user_id}:*",
                    f"memory:{actual_user_id}:*",
                    f"embeddings:{actual_user_id}:*",
                    f"session:{actual_user_id}:*"
                ]
                for pattern in cache_patterns:
                    for key in valkey.scan_iter(match=pattern):
                        valkey.delete(key)
                logger.debug(f"Cleaned up Valkey cache data for {user_email}")
            except Exception as e:
                logger.warning(f"Failed to clean up cache data: {e}")

            logger.info(f"Successfully cleaned up all test data for user {actual_user_id} ({user_email})")

        except Exception as e:
            logger.error(f"Failed to clean up test data for {user_email}: {e}")
            # Don't raise - cleanup failures shouldn't break tests

async def load_realistic_conversation_data() -> Continuum:
    """
    Load realistic continuum from JSON and create it in the database.
    
    Returns:
        Continuum object with realistic messages loaded
    """
    from auth.database import AuthDatabase
    
    # Ensure clean state first
    cleanup_test_user_data()
    
    # Get actual user ID from database
    auth_db = AuthDatabase()
    user_record = auth_db.get_user_by_email(TEST_USER_EMAIL)
    if not user_record:
        # User should exist from ensure_test_user_exists()
        from tests.fixtures.core import ensure_test_user_exists
        user_record = ensure_test_user_exists()
    
    actual_user_id = user_record["id"]
    
    # Set user context for RLS using actual ID
    set_current_user_id(actual_user_id)
    
    # Load continuum data from JSON
    fixture_path = Path(__file__).parent / "realistic_conversation.json"
    with open(fixture_path, 'r') as f:
        data = json.load(f)
    
    # Create continuum using repository (normal flow)
    from cns.infrastructure.conversation_repository import get_continuum_repository
    repository =get_continuum_repository()  # Use singleton
    continuum = repository.get_continuum(actual_user_id)
    if not continuum:
        continuum = repository.create_continuum(actual_user_id)
    
    # Create realistic messages from JSON
    messages = []
    base_time = utc_now()
    
    for msg_data in data['messages']:
        # Calculate timestamp based on offset
        timestamp_offset = timedelta(hours=msg_data.get('timestamp_offset_hours', 0))
        message_time = base_time + timestamp_offset
        
        # Create message object
        message = Message(
            content=msg_data['content'],
            role=msg_data['role'],
            created_at=message_time,
            metadata=msg_data.get('metadata', {})
        )
        
        # Save message using repository (normal flow)
        repository.save_message(message, continuum.id, actual_user_id)
        messages.append(message)
    
    # Update continuum cache with messages
    continuum._message_cache = messages
    continuum._cache_loaded = True  # Mark cache as loaded
    
    logger.info(f"Loaded {len(messages)} realistic messages for continuum {continuum.id}")
    return continuum


@pytest.fixture
async def realistic_conversation() -> Continuum:
    """
    Pytest fixture that provides a continuum with realistic messages.
    
    This fixture:
    - Cleans up any existing test user data
    - Loads realistic continuum from JSON
    - Creates messages in the database using normal repository flow
    - Returns a Continuum object ready for testing
    
    The continuum simulates a real multi-day interaction about
    building a memory-enabled AI system.
    """
    return await load_realistic_conversation_data()


@pytest.fixture
async def realistic_messages() -> List[Message]:
    """
    Pytest fixture that provides just the messages from realistic continuum.
    
    Useful when you only need the message list without the full continuum object.
    """
    continuum = await load_realistic_conversation_data()
    return continuum._message_cache


@pytest.fixture(scope="session")
def realistic_conversation_json() -> dict:
    """
    Session-scoped fixture that loads the JSON data once.
    
    This avoids repeated file I/O for tests that need the raw data.
    """
    fixture_path = Path(__file__).parent / "realistic_conversation.json"
    with open(fixture_path, 'r') as f:
        return json.load(f)

def get_test_user_id() -> str:
    """Get the actual test user ID from the database."""
    from auth.database import AuthDatabase
    
    auth_db = AuthDatabase()
    user_record = auth_db.get_user_by_email(TEST_USER_EMAIL)
    if not user_record:
        raise RuntimeError(f"Test user with email {TEST_USER_EMAIL} not found in database")
    
    return user_record["id"]


def get_test_user_email() -> str:
    """Get the hardcoded test user email."""
    return TEST_USER_EMAIL