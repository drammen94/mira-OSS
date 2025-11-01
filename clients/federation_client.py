"""
Federation client for communicating with external gossip-federation service.

This module provides a client interface to the federation service that was
extracted to https://github.com/taylorsatula/gossip-federation
"""

import logging
from typing import Dict, Any, Optional
import os

import httpx

logger = logging.getLogger(__name__)


class FederationClient:
    """
    Client for the external gossip-federation service.

    This replaces the direct imports of federation modules that were
    previously part of MIRA.
    """

    def __init__(self):
        """Initialize federation client with service URL from environment."""
        # Default to localhost for development
        self.base_url = os.getenv("FEDERATION_SERVICE_URL", "http://localhost:8302")
        self.timeout = 30
        self._client = None

    @property
    def client(self) -> httpx.Client:
        """Lazy initialization of HTTP client."""
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def send_federated_message(
        self,
        to_address: str,
        from_address: str,
        content: str,
        content_type: str = "text/plain",
        reply_to: Optional[str] = None,
        federation_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send a message through federation to a remote user.

        Args:
            to_address: Federated address (user@domain)
            from_address: Sender's federated address
            content: Message content
            content_type: Content MIME type
            reply_to: Optional message ID this replies to
            federation_metadata: Optional metadata dict

        Returns:
            Response with message_id and status

        Raises:
            httpx.HTTPError: If request fails
        """
        message_data = {
            "to_address": to_address,
            "from_address": from_address,
            "content": content,
            "content_type": content_type
        }

        if reply_to:
            message_data["reply_to"] = reply_to

        if federation_metadata:
            message_data["federation_metadata"] = federation_metadata

        response = self.client.post(
            f"{self.base_url}/api/v1/messages/send",
            json=message_data
        )
        response.raise_for_status()
        return response.json()

    def get_federation_status(self) -> Dict[str, Any]:
        """
        Get federation service status.

        Returns:
            Status dict with health information

        Raises:
            httpx.HTTPError: If federation service is unreachable or returns error
        """
        response = self.client.get(f"{self.base_url}/api/v1/health")
        response.raise_for_status()
        return response.json()

    def cleanup(self):
        """Close HTTP client connection."""
        if self._client:
            self._client.close()
            self._client = None


# Global singleton instance
_federation_client = None


def get_federation_client() -> FederationClient:
    """Get or create the global federation client instance."""
    global _federation_client
    if _federation_client is None:
        _federation_client = FederationClient()
    return _federation_client