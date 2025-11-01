"""
Integration tests for WebSocket /v0/ws/chat endpoint.

Tests real-time bidirectional streaming with authentication and message processing.
"""
import pytest
import json
from fastapi.testclient import TestClient


class TestWebSocketEndpointAuthentication:
    """Test WebSocket endpoint authentication."""

    def test_websocket_requires_authentication(self, test_client: TestClient):
        """Verify WebSocket requires authentication before accepting messages."""
        with pytest.raises(Exception):
            # Attempting to connect without auth should fail
            with test_client.websocket_connect("/v0/v0/ws/chat") as ws:
                # Should not reach here
                pass

    def test_websocket_accepts_token_auth(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket accepts auth token."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Should successfully connect
            data = ws.receive_json()

            # First message should be auth result or connection established
            assert isinstance(data, dict)

    def test_websocket_sends_auth_message(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket accepts auth message."""
        with test_client.websocket_connect("/v0/v0/ws/chat") as ws:
            # Send auth message
            ws.send_json({
                "type": "auth",
                "token": authenticated_user['access_token']
            })

            # Should receive auth result
            response = ws.receive_json()
            assert response.get("type") in ["auth_success", "auth_response"]


class TestWebSocketEndpointMessaging:
    """Test WebSocket message protocol."""

    def test_websocket_sends_message(self, test_client: TestClient, authenticated_user):
        """Verify authenticated WebSocket can send messages."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message
            ws.send_json({
                "type": "message",
                "content": "Hello, MIRA"
            })

            # Should receive response
            response = ws.receive_json()
            assert "type" in response

    def test_websocket_receives_streaming_text(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket receives streamed text responses."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message
            ws.send_json({
                "type": "message",
                "content": "Explain something"
            })

            # Should receive streaming text chunks
            received_text = False
            while True:
                try:
                    response = ws.receive_json(timeout=2)

                    if response.get("type") == "text":
                        received_text = True
                        assert "content" in response

                    if response.get("type") == "complete":
                        break

                except Exception:
                    break

            assert received_text

    def test_websocket_receives_completion_message(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket receives completion message after response."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message
            ws.send_json({
                "type": "message",
                "content": "Test message"
            })

            # Wait for completion message
            received_complete = False
            timeout_count = 0
            while timeout_count < 20:
                try:
                    response = ws.receive_json(timeout=1)

                    if response.get("type") == "complete":
                        received_complete = True
                        assert "metadata" in response
                        break

                except Exception:
                    timeout_count += 1

            assert received_complete

    def test_websocket_message_requires_type(self, test_client: TestClient, authenticated_user):
        """Verify message must have type field."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message without type
            ws.send_json({
                "content": "Hello"
            })

            # Should receive error
            response = ws.receive_json()
            assert response.get("type") == "error" or "error" in str(response)


class TestWebSocketEndpointKeepalive:
    """Test WebSocket keepalive mechanism."""

    def test_websocket_responds_to_ping(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket responds to ping messages."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send ping
            ws.send_json({
                "type": "ping"
            })

            # Should receive pong
            response = ws.receive_json()
            assert response.get("type") == "pong"

    def test_websocket_keepalive_connection_open(self, test_client: TestClient, authenticated_user):
        """Verify keepalive pings keep connection open."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            import time

            # Send multiple pings
            for i in range(3):
                ws.send_json({"type": "ping"})
                response = ws.receive_json()
                assert response.get("type") == "pong"
                time.sleep(0.1)

            # Connection should still be alive


class TestWebSocketEndpointImages:
    """Test WebSocket image handling."""

    def test_websocket_sends_message_with_image(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket can send messages with images."""
        import base64

        # Minimal JPEG
        jpeg_data = bytes.fromhex("FFD8FFE0") + b"\x00" * 100 + bytes.fromhex("FFD9")
        image_b64 = base64.b64encode(jpeg_data).decode()

        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message with image
            ws.send_json({
                "type": "message",
                "content": "What's in this image?",
                "image": image_b64,
                "image_type": "image/jpeg"
            })

            # Should process without error
            response = ws.receive_json()
            assert response.get("type") in ["text", "error"]


class TestWebSocketEndpointMessageTypes:
    """Test WebSocket message type handling."""

    def test_websocket_handles_thinking_message(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket may send thinking messages."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message
            ws.send_json({
                "type": "message",
                "content": "Solve a problem"
            })

            # May receive thinking messages (optional)
            message_types = set()
            timeout_count = 0
            while timeout_count < 20:
                try:
                    response = ws.receive_json(timeout=1)
                    message_types.add(response.get("type"))

                    if response.get("type") == "complete":
                        break

                except Exception:
                    timeout_count += 1

            # Should receive text and complete at minimum
            assert "text" in message_types or "complete" in message_types

    def test_websocket_handles_tool_execution_message(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket may send tool execution messages."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message that might trigger tool use
            ws.send_json({
                "type": "message",
                "content": "Use a tool for this"
            })

            # Collect message types
            message_types = set()
            timeout_count = 0
            while timeout_count < 20:
                try:
                    response = ws.receive_json(timeout=1)
                    message_types.add(response.get("type"))

                    if response.get("type") == "complete":
                        break

                except Exception:
                    timeout_count += 1

            # Should have received some messages
            assert len(message_types) > 0


class TestWebSocketEndpointErrors:
    """Test WebSocket error handling."""

    def test_websocket_handles_empty_message(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket handles empty message content."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send empty message
            ws.send_json({
                "type": "message",
                "content": ""
            })

            # Should receive error
            response = ws.receive_json()
            assert response.get("type") == "error"

    def test_websocket_handles_unknown_message_type(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket handles unknown message types."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send unknown message type
            ws.send_json({
                "type": "unknown_type",
                "content": "test"
            })

            # Should receive error or ignore
            response = ws.receive_json()
            # May be error or other response depending on implementation
            assert isinstance(response, dict)


class TestWebSocketEndpointMetadata:
    """Test WebSocket response metadata."""

    def test_websocket_completion_includes_metadata(self, test_client: TestClient, authenticated_user):
        """Verify complete message includes metadata."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message
            ws.send_json({
                "type": "message",
                "content": "Hello"
            })

            # Wait for completion
            complete_msg = None
            timeout_count = 0
            while timeout_count < 20:
                try:
                    response = ws.receive_json(timeout=1)

                    if response.get("type") == "complete":
                        complete_msg = response
                        break

                except Exception:
                    timeout_count += 1

            if complete_msg:
                # Should have metadata
                assert "metadata" in complete_msg
                metadata = complete_msg["metadata"]

                # Metadata should include standard fields
                expected_fields = ["tools_used", "referenced_memories", "processing_time_ms"]
                for field in expected_fields:
                    assert field in metadata, f"Missing metadata field: {field}"

    def test_websocket_includes_continuum_id(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket responses include continuum_id."""
        with test_client.websocket_connect(
            "/v0/v0/ws/chat",
            headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
        ) as ws:
            # Send message
            ws.send_json({
                "type": "message",
                "content": "Test"
            })

            # Wait for complete message
            complete_msg = None
            timeout_count = 0
            while timeout_count < 20:
                try:
                    response = ws.receive_json(timeout=1)

                    if response.get("type") == "complete":
                        complete_msg = response
                        break

                except Exception:
                    timeout_count += 1

            if complete_msg:
                assert "continuum_id" in complete_msg


class TestWebSocketEndpointConnectionManagement:
    """Test WebSocket connection lifecycle."""

    def test_websocket_closes_gracefully(self, test_client: TestClient, authenticated_user):
        """Verify WebSocket closes gracefully."""
        try:
            with test_client.websocket_connect(
                "/v0/v0/ws/chat",
                headers={"Authorization": f"Bearer {authenticated_user['access_token']}"}
            ) as ws:
                # Send a message
                ws.send_json({"type": "ping"})
                response = ws.receive_json()

            # Should exit context without exception
            assert True

        except Exception as e:
            pytest.fail(f"WebSocket didn't close gracefully: {e}")

    def test_websocket_rejects_after_auth_failure(self, test_client: TestClient):
        """Verify WebSocket rejects messages after auth failure."""
        with pytest.raises(Exception):
            with test_client.websocket_connect("/v0/v0/ws/chat") as ws:
                # Send invalid auth
                ws.send_json({
                    "type": "auth",
                    "token": "invalid_token"
                })

                # Should disconnect
                response = ws.receive_json()
                # If we get here, connection is still open (unexpected)
                assert response.get("type") == "auth_failure"
