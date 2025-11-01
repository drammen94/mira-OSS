"""
Base API infrastructure for MIRA endpoints.

Provides consistent response patterns, error handling, and middleware.
"""
import logging
from uuid import uuid4
from datetime import datetime
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass

from utils.timezone_utils import utc_now, format_utc_iso

logger = logging.getLogger(__name__)


@dataclass
class APIResponse:
    """Standard API response structure."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {"success": self.success}
        
        if self.data is not None:
            result["data"] = self.data
            
        if self.error is not None:
            result["error"] = self.error
            
        if self.meta is not None:
            result["meta"] = self.meta
            
        return result


class APIError(Exception):
    """Base API error with structured details."""
    
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ValidationError(APIError):
    """Validation error for invalid input."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__("VALIDATION_ERROR", message, details)


class NotFoundError(APIError):
    """Resource not found error."""
    
    def __init__(self, resource: str, identifier: str):
        super().__init__(
            "NOT_FOUND", 
            f"{resource} not found: {identifier}",
            {"resource": resource, "identifier": identifier}
        )


class ServiceUnavailableError(APIError):
    """Service unavailable error."""
    
    def __init__(self, service: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            "SERVICE_UNAVAILABLE",
            f"Service unavailable: {service}",
            details
        )


def create_success_response(
    data: Dict[str, Any], 
    meta: Optional[Dict[str, Any]] = None
) -> APIResponse:
    """Create a successful API response."""
    return APIResponse(success=True, data=data, meta=meta)


def create_error_response(
    error: Union[APIError, Exception],
    request_id: Optional[str] = None
) -> APIResponse:
    """Create an error API response."""
    if isinstance(error, APIError):
        error_dict = {
            "code": error.code,
            "message": error.message,
            "details": error.details
        }
    else:
        error_dict = {
            "code": "INTERNAL_ERROR",
            "message": str(error),
            "details": {}
        }
    
    meta = {
        "timestamp": format_utc_iso(utc_now())
    }
    
    if request_id:
        meta["request_id"] = request_id
    
    return APIResponse(success=False, error=error_dict, meta=meta)


def generate_request_id() -> str:
    """Generate unique request ID."""
    return f"req_{uuid4().hex[:12]}"


class BaseHandler:
    """Base handler for API endpoints."""
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def validate_params(self, **params) -> Dict[str, Any]:
        """Validate input parameters. Override in subclasses."""
        return params
    
    def handle_request(self, **params) -> APIResponse:
        """Handle API request with consistent error handling."""
        request_id = generate_request_id()
        
        try:
            validated_params = self.validate_params(**params)
            result = self.process_request(**validated_params)
            
            if isinstance(result, APIResponse):
                return result
            else:
                return create_success_response(result)
                
        except ValidationError as e:
            # Handle ValidationError same as APIError since it inherits from it
            self.logger.warning(f"Validation error in {self.__class__.__name__}: {e.message}")
            return create_error_response(e, request_id)
        except APIError as e:
            self.logger.warning(f"API error in {self.__class__.__name__}: {e.message}")
            return create_error_response(e, request_id)
        except Exception as e:
            self.logger.error(f"Unexpected error in {self.__class__.__name__}: {e}", exc_info=True)
            return create_error_response(e, request_id)
    
    def process_request(self, **params) -> Union[Dict[str, Any], APIResponse]:
        """Process the actual request. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement process_request")
    


def add_request_meta(response: APIResponse, **meta_data) -> APIResponse:
    """Add metadata to existing response."""
    if response.meta is None:
        response.meta = {}
    
    response.meta.update(meta_data)
    return response