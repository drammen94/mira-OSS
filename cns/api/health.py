"""
Health API endpoint - basic system health checks.

Simple health monitoring for database, basic system status.
"""
import logging
from typing import Dict, Any
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .base import BaseHandler, APIResponse, create_success_response
from clients.postgres_client import PostgresClient
from utils.timezone_utils import utc_now, format_utc_iso

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthEndpoint(BaseHandler):
    """Health endpoint handler with basic system checks."""

    def process_request(self, **params) -> APIResponse:
        """Check system health components."""
        start_time = time.time()
        components = {}
        overall_status = "healthy"

        # Check database connectivity (unified mira_service database)
        try:
            db = PostgresClient("mira_service")
            db.execute_single("SELECT 1")
            components["database"] = {"status": "healthy", "latency_ms": round((time.time() - start_time) * 1000, 1)}
        except Exception as e:
            components["database"] = {"status": "unhealthy", "error": str(e)}
            overall_status = "unhealthy"

        # Basic system info
        components["system"] = {
            "status": "healthy",
            "uptime_seconds": int(time.time()),  # Placeholder - actual uptime would need process start tracking
            "version": "1.0.0"
        }

        # Federation moved to separate service
        # See https://github.com/taylorsatula/gossip-federation

        total_time = round((time.time() - start_time) * 1000, 1)

        health_data = {
            "status": overall_status,
            "timestamp": format_utc_iso(utc_now()),
            "components": components,
            "meta": {
                "check_duration_ms": total_time,
                "checks_run": len(components)
            }
        }

        # Return appropriate response based on health status
        if overall_status == "unhealthy":
            return APIResponse(
                success=False,
                data=health_data,
                error={
                    "code": "SYSTEM_UNHEALTHY",
                    "message": "One or more system components are unhealthy"
                }
            )

        return create_success_response(health_data)


def get_health_handler() -> HealthEndpoint:
    """Get health endpoint handler instance."""
    return HealthEndpoint()


@router.get("/health")
def health_endpoint():
    """System health check endpoint (no authentication required)."""
    try:
        handler = get_health_handler()
        response = handler.handle_request()

        # Return appropriate HTTP status based on health
        response_dict = response.to_dict()
        if response_dict["data"]["status"] == "unhealthy":
            return JSONResponse(status_code=503, content=response_dict)

        return response_dict

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Health endpoint error: {e}", exc_info=True)
        error_response = {
            "success": False,
            "error": {
                "code": "HEALTH_CHECK_FAILED",
                "message": "Health check failed"
            },
            "meta": {
                "timestamp": format_utc_iso(utc_now())
            }
        }
        return JSONResponse(status_code=503, content=error_response)