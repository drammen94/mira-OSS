"""
Global pytest configuration and fixtures for MIRA tests.

Provides automatic cleanup between tests to ensure isolation.
"""
import pytest

# Import fixtures to make them available globally
from tests.fixtures.reset import full_reset
from tests.fixtures.isolation import *
from tests.fixtures.auth import *
from tests.fixtures.core import *


@pytest.fixture(autouse=True, scope="function")
def reset_test_environment():
    """
    Reset the test environment before and after each test.

    Ensures each test starts with completely fresh state by clearing
    all connection pools, singletons, and caches.
    """
    # Reset before test
    full_reset()

    yield

    # Reset after test
    full_reset()


@pytest.fixture(scope="session", autouse=True)
def event_loop_policy():
    """
    Set event loop policy for the entire test session.

    Ensures consistent event loop behavior across all tests.
    """
    import asyncio
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
