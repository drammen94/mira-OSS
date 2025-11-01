"""
Auth fixtures for single-user OSS version.

Provides minimal auth fixtures that set user context for tests.
Compatible with closed-source multi-user version's fixture interface.
"""
import pytest
import pytest_asyncio
from typing import Dict, Any

from utils.user_context import set_current_user_id, clear_user_context

# Test user constants (from core fixtures)
TEST_USER_ID = "443a898d-ed56-495a-b9de-0551c80169fe"
TEST_USER_EMAIL = "test@example.com"


@pytest.fixture
def test_email():
    """Return the consistent test user email."""
    return TEST_USER_EMAIL


@pytest_asyncio.fixture
async def test_user(test_email):
    """
    Get the persistent test user (reuses same user across tests).

    In single-user OSS mode, this always returns the same test user.
    Compatible with closed-source multi-user version's fixture interface.
    """
    from tests.fixtures.core import ensure_test_user_exists

    # Get the persistent test user
    user_record = ensure_test_user_exists()

    # Set user context for the test
    set_current_user_id(user_record["id"])

    yield {
        "id": user_record["id"],
        "email": user_record["email"],
        "is_active": user_record["is_active"],
        "created_at": user_record["created_at"]
    }

    # Clear user context after test
    clear_user_context()


@pytest.fixture
def security_test_vectors():
    """Security test vectors for input validation."""
    return {
        "valid_emails": [
            "user@example.com",
            "user.name@domain.co.uk",
            "user+tag@example.org"
        ],
        "invalid_emails": [
            "notanemail",
            "@domain.com",
            "user@",
            "user space@domain.com",
            "",
            None
        ],
        "malicious_inputs": [
            "<script>alert('xss')</script>@domain.com",
            "user'; DROP TABLE users; --@domain.com",
            "../../../etc/passwd@domain.com"
        ]
    }


@pytest_asyncio.fixture(autouse=True)
async def clean_test_valkey():
    """Clean Valkey test data before and after tests."""
    from clients.valkey_client import get_valkey

    try:
        valkey = get_valkey()
        # Clean any test session keys before test
        keys = valkey.keys("test_*")
        for key in keys:
            valkey.delete(key)
    except Exception:
        pass  # Valkey might not be available

    yield

    # Clean after test
    try:
        valkey = get_valkey()
        keys = valkey.keys("test_*")
        for key in keys:
            valkey.delete(key)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def setup_test_user_context(test_user):
    """
    Automatically set user context for all tests.

    This fixture ensures every test has the single user's context set,
    matching the production single-user behavior where context is set at startup.
    """
    set_current_user_id(test_user["id"])
    yield
    clear_user_context()
