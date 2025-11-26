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
from utils.thread_monitor import ThreadMonitor
from utils.scheduled_task_monitor import ScheduledTaskMonitor

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
                "code": "HEALTH_CHECK_ERROR",
                "message": f"Failed to perform health check: {str(e)}"
            }
        }
        return JSONResponse(status_code=500, content=error_response)


@router.get("/health/threads")
def thread_health_endpoint():
    """
    Thread monitoring endpoint - shows active and stuck operations.

    Returns information about thread pool usage and potentially stuck operations.
    """
    try:
        from anyio import to_thread
        limiter = to_thread.current_default_thread_limiter()

        # Get thread pool stats
        available_threads = limiter.available_tokens
        total_threads = limiter.total_tokens
        used_threads = total_threads - available_threads
        usage_percent = (used_threads / total_threads) * 100 if total_threads > 0 else 0

        # Get stuck operations
        stuck_ops = ThreadMonitor.get_stuck_operations()
        active_ops = ThreadMonitor.get_active_operations()

        # Get scheduled job stats
        job_stats = ScheduledTaskMonitor.get_job_stats()

        # Determine health status
        status = "healthy"
        if usage_percent > 90:
            status = "critical"
        elif usage_percent > 70:
            status = "warning"
        if stuck_ops:
            status = "critical"

        response = {
            "success": True,
            "data": {
                "status": status,
                "thread_pool": {
                    "available": available_threads,
                    "total": total_threads,
                    "used": used_threads,
                    "usage_percent": round(usage_percent, 1)
                },
                "operations": {
                    "active": len(active_ops),
                    "stuck": len(stuck_ops),
                    "active_operations": active_ops[:10],  # First 10 active ops
                    "stuck_operations": stuck_ops  # All stuck ops (these are critical)
                },
                "scheduled_jobs": job_stats,
                "timestamp": format_utc_iso(utc_now())
            }
        }

        # Return 503 if critical
        if status == "critical":
            return JSONResponse(status_code=503, content=response)

        return response

    except Exception as e:
        logger.error(f"Thread health endpoint error: {e}", exc_info=True)
        return {
            "success": False,
            "error": {
                "code": "MONITORING_ERROR",
                "message": f"Failed to get thread monitoring data: {str(e)}"
            }
        }


@router.get("/health/thread-dump")
def thread_dump_endpoint():
    """
    Generate a detailed thread dump for debugging.

    Returns complete thread state information for all threads.
    """
    try:
        dump = ThreadMonitor.dump_thread_states()

        # Also save to file for later analysis
        import time
        dump_file = f"/tmp/thread_dump_api_{int(time.time())}.txt"
        with open(dump_file, 'w') as f:
            f.write(dump)

        return {
            "success": True,
            "data": {
                "thread_dump": dump,
                "saved_to": dump_file,
                "timestamp": format_utc_iso(utc_now())
            }
        }

    except Exception as e:
        logger.error(f"Thread dump endpoint error: {e}", exc_info=True)
        return {
            "success": False,
            "error": {
                "code": "DUMP_ERROR",
                "message": f"Failed to generate thread dump: {str(e)}"
            }
        }