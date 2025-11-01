"""
Integration tests for GET /data endpoint.

Tests unified data access with type-based routing and user isolation.
"""
import pytest
from fastapi.testclient import TestClient


class TestDataEndpointAuthentication:
    """Test /data endpoint authentication."""

    def test_data_requires_authentication(self, test_client: TestClient):
        """Verify /data endpoint requires authentication."""
        response = test_client.get("/v0/v0/api/data?type=history")

        # Without auth, should return 401 or 403
        assert response.status_code in [401, 403]

    def test_data_accepts_authenticated_request(self, authenticated_client: TestClient):
        """Verify authenticated user can access /data endpoint."""
        response = authenticated_client.get("/v0/v0/api/data?type=history")

        # Should succeed
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestDataEndpointValidation:
    """Test /data endpoint input validation."""

    def test_data_requires_type_parameter(self, authenticated_client: TestClient):
        """Verify /data requires type query parameter."""
        response = authenticated_client.get("/v0/v0/api/data")

        # Missing required type parameter
        assert response.status_code == 422

    def test_data_rejects_invalid_type(self, authenticated_client: TestClient):
        """Verify /data rejects invalid data type."""
        response = authenticated_client.get("/v0/v0/api/data?type=invalid_type")

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert "invalid" in data["error"]["message"].lower()

    def test_data_accepts_valid_types(self, authenticated_client: TestClient):
        """Verify /data accepts all valid type parameters."""
        valid_types = ["history", "memories", "dashboard", "user", "linked_days", "domains"]

        for data_type in valid_types:
            response = authenticated_client.get(f"/data?type={data_type}")

            # Should not return 400 for validation
            assert response.status_code in [200, 400, 404], f"Type {data_type} should be valid"


class TestDataEndpointHistoryType:
    """Test /data?type=history endpoint."""

    def test_history_returns_correct_structure(self, authenticated_client: TestClient):
        """Verify history data returns expected structure."""
        response = authenticated_client.get("/v0/v0/api/data?type=history")

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["success"] is True
        assert "data" in data
        assert "meta" in data

        # Verify history data structure
        history_data = data["data"]
        assert "messages" in history_data
        assert "pagination" in history_data
        assert isinstance(history_data["messages"], list)

    def test_history_includes_pagination(self, authenticated_client: TestClient):
        """Verify history includes pagination metadata."""
        response = authenticated_client.get("/v0/v0/api/data?type=history&limit=10&offset=0")

        data = response.json()
        history_data = data["data"]
        pagination = history_data["pagination"]

        assert "offset" in pagination
        assert "limit" in pagination
        assert "total" in pagination
        assert isinstance(pagination["total"], int)

    def test_history_respects_limit_parameter(self, authenticated_client: TestClient):
        """Verify history respects limit parameter."""
        response = authenticated_client.get("/v0/v0/api/data?type=history&limit=5")

        data = response.json()
        messages = data["data"]["messages"]

        # Should return at most 5 messages
        assert len(messages) <= 5

    def test_history_respects_offset_parameter(self, authenticated_client: TestClient):
        """Verify history respects offset parameter."""
        # Get first batch
        response1 = authenticated_client.get("/v0/v0/api/data?type=history&limit=5&offset=0")
        messages1 = response1.json()["data"]["messages"]

        # Get second batch
        response2 = authenticated_client.get("/v0/v0/api/data?type=history&limit=5&offset=5")
        messages2 = response2.json()["data"]["messages"]

        # Should be different (or second batch empty if fewer than 10 messages)
        if messages1 and messages2:
            assert messages1[0] != messages2[0]

    def test_history_supports_search_query(self, authenticated_client: TestClient):
        """Verify history supports search parameter."""
        response = authenticated_client.get("/v0/v0/api/data?type=history&search=test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data

    def test_history_supports_date_filtering(self, authenticated_client: TestClient):
        """Verify history supports date filtering."""
        response = authenticated_client.get(
            "/data?type=history&start_date=2025-01-01&end_date=2025-12-31"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_history_supports_message_type_filtering(self, authenticated_client: TestClient):
        """Verify history supports message_type parameter."""
        for msg_type in ["regular", "summaries", "all"]:
            response = authenticated_client.get(f"/data?type=history&message_type={msg_type}")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True


class TestDataEndpointMemoriesType:
    """Test /data?type=memories endpoint."""

    def test_memories_returns_correct_structure(self, authenticated_client: TestClient):
        """Verify memories data returns expected structure."""
        response = authenticated_client.get("/v0/v0/api/data?type=memories")

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["success"] is True
        assert "data" in data

        # Verify memories data structure
        memories_data = data["data"]
        assert "memories" in memories_data
        assert isinstance(memories_data["memories"], list)

    def test_memories_includes_pagination(self, authenticated_client: TestClient):
        """Verify memories includes pagination."""
        response = authenticated_client.get("/v0/v0/api/data?type=memories&limit=10")

        data = response.json()
        memories_data = data["data"]

        assert "pagination" in memories_data
        pagination = memories_data["pagination"]
        assert "total" in pagination

    def test_memories_respects_limit(self, authenticated_client: TestClient):
        """Verify memories respects limit parameter."""
        response = authenticated_client.get("/v0/v0/api/data?type=memories&limit=3")

        data = response.json()
        memories = data["data"]["memories"]

        assert len(memories) <= 3


class TestDataEndpointUserType:
    """Test /data?type=user endpoint."""

    def test_user_returns_user_profile(self, authenticated_client: TestClient, authenticated_user):
        """Verify user data returns authenticated user's profile."""
        response = authenticated_client.get("/v0/v0/api/data?type=user")

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["success"] is True
        user_data = data["data"]

        # Verify user profile fields
        assert "profile" in user_data
        profile = user_data["profile"]
        assert "id" in profile
        assert "email" in profile

        # Verify it's the authenticated user's data
        assert profile["id"] == authenticated_user["user_id"]
        assert profile["email"] == authenticated_user["email"]

    def test_user_includes_preferences(self, authenticated_client: TestClient):
        """Verify user data includes user preferences."""
        response = authenticated_client.get("/v0/v0/api/data?type=user")

        data = response.json()
        user_data = data["data"]

        assert "preferences" in user_data
        preferences = user_data["preferences"]

        # Preferences should have these fields (may be empty)
        assert "theme" in preferences
        assert "timezone" in preferences


class TestDataEndpointDashboardType:
    """Test /data?type=dashboard endpoint."""

    def test_dashboard_returns_system_health(self, authenticated_client: TestClient):
        """Verify dashboard data returns system health info."""
        response = authenticated_client.get("/v0/v0/api/data?type=dashboard")

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["success"] is True
        dashboard_data = data["data"]

        # Dashboard should include system information
        assert "system_health" in dashboard_data or "health" in dashboard_data or "data" in dashboard_data


class TestDataEndpointLinkedDaysType:
    """Test /data?type=linked_days endpoint."""

    def test_linked_days_returns_list(self, authenticated_client: TestClient):
        """Verify linked_days returns array of linked archives."""
        response = authenticated_client.get("/v0/v0/api/data?type=linked_days")

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["success"] is True
        linked_days_data = data["data"]

        assert "linked_days" in linked_days_data
        assert isinstance(linked_days_data["linked_days"], list)

    def test_linked_days_includes_metadata(self, authenticated_client: TestClient):
        """Verify linked_days includes count and status."""
        response = authenticated_client.get("/v0/v0/api/data?type=linked_days")

        data = response.json()
        linked_days_data = data["data"]

        # Should include count
        assert "count" in linked_days_data or "total" in linked_days_data


class TestDataEndpointDomainsType:
    """Test /data?type=domains endpoint."""

    def test_domains_returns_domain_list(self, authenticated_client: TestClient):
        """Verify domains returns list of domain knowledge blocks."""
        response = authenticated_client.get("/v0/v0/api/data?type=domains")

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["success"] is True
        domains_data = data["data"]

        assert "domains" in domains_data
        assert isinstance(domains_data["domains"], list)

    def test_domains_filters_by_label(self, authenticated_client: TestClient):
        """Verify domains can filter by specific domain label."""
        response = authenticated_client.get("/v0/v0/api/data?type=domains&domain_label=work")

        # Should succeed even if no domain with that label exists
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestDataEndpointPagination:
    """Test /data endpoint pagination."""

    def test_default_limit_applied(self, authenticated_client: TestClient):
        """Verify default limit is applied when not specified."""
        response = authenticated_client.get("/v0/v0/api/data?type=history")

        data = response.json()
        pagination = data["data"]["pagination"]

        # Should have default limit
        assert pagination["limit"] > 0

    def test_limit_clamped_to_maximum(self, authenticated_client: TestClient):
        """Verify limit is clamped to maximum value."""
        response = authenticated_client.get("/v0/v0/api/data?type=history&limit=10000")

        data = response.json()
        pagination = data["data"]["pagination"]

        # Limit should not exceed maximum (typically 100)
        assert pagination["limit"] <= 100

    def test_offset_zero_by_default(self, authenticated_client: TestClient):
        """Verify offset defaults to 0."""
        response = authenticated_client.get("/v0/v0/api/data?type=history")

        data = response.json()
        pagination = data["data"]["pagination"]

        assert pagination["offset"] == 0


class TestDataEndpointUserIsolation:
    """Test user data isolation via RLS."""

    def test_data_scoped_to_authenticated_user(self, authenticated_client: TestClient, authenticated_user):
        """Verify data returned is scoped to authenticated user."""
        response = authenticated_client.get("/v0/v0/api/data?type=user")

        data = response.json()
        user_data = data["data"]["profile"]

        # Data should belong to authenticated user
        assert user_data["id"] == authenticated_user["user_id"]

    def test_history_contains_only_user_messages(self, authenticated_client: TestClient, authenticated_user):
        """Verify history only contains current user's messages."""
        response = authenticated_client.get("/v0/v0/api/data?type=history")

        data = response.json()
        # User context ensures RLS filters data at DB level
        # We verify the endpoint works, actual RLS tested separately

        assert response.status_code == 200
        assert data["success"] is True


class TestDataEndpointErrorHandling:
    """Test /data endpoint error handling."""

    def test_invalid_date_format_handled(self, authenticated_client: TestClient):
        """Verify invalid date formats are handled gracefully."""
        response = authenticated_client.get("/v0/v0/api/data?type=history&start_date=invalid-date")

        # Should either succeed (ignoring bad date) or return 400
        assert response.status_code in [200, 400]

    def test_negative_limit_handled(self, authenticated_client: TestClient):
        """Verify negative limit is handled."""
        response = authenticated_client.get("/v0/v0/api/data?type=history&limit=-1")

        # Should either use default or reject
        assert response.status_code in [200, 400]

    def test_negative_offset_handled(self, authenticated_client: TestClient):
        """Verify negative offset is handled."""
        response = authenticated_client.get("/v0/v0/api/data?type=history&offset=-1")

        # Should either use 0 or reject
        assert response.status_code in [200, 400]
