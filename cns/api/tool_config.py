"""
Tool Configuration API endpoints.

Provides RESTful endpoints for managing user-specific tool configurations.
Tools with registered Pydantic config classes are automatically discoverable
and configurable through these endpoints.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from auth.api import get_current_user
from auth.types import SessionData
from cns.api.base import (
    APIResponse,
    ValidationError,
    NotFoundError,
    create_success_response,
    create_error_response,
    generate_request_id,
)
from tools.registry import registry
from utils.user_credentials import UserCredentialService
from utils.timezone_utils import format_utc_iso, utc_now

logger = logging.getLogger(__name__)

router = APIRouter()


# Response models
class ToolListItem(BaseModel):
    """Tool information for listing."""
    name: str = Field(..., description="Tool identifier")
    config_class: str = Field(..., description="Pydantic config class name")
    has_user_config: bool = Field(..., description="Whether user has saved config")


class ToolConfigResponse(BaseModel):
    """Response containing tool configuration."""
    tool_name: str
    config: Dict[str, Any]
    has_user_config: bool
    defaults: Dict[str, Any]


# Helper functions
def _get_configurable_tools() -> Dict[str, type]:
    """Get all tools with registered config classes."""
    return {name: config_class for name, config_class in registry._registry.items()}


def _get_user_tool_config(tool_name: str) -> Optional[Dict[str, Any]]:
    """Get user's saved config for a tool, or None if not set."""
    try:
        credential_service = UserCredentialService()
        config_json = credential_service.get_credential(
            credential_type="tool_config",
            service_name=tool_name
        )
        if config_json:
            return json.loads(config_json)
        return None
    except Exception as e:
        logger.error(f"Error retrieving tool config for {tool_name}: {e}")
        return None


def _save_user_tool_config(tool_name: str, config: Dict[str, Any]) -> None:
    """Save user's config for a tool."""
    credential_service = UserCredentialService()
    credential_service.store_credential(
        credential_type="tool_config",
        service_name=tool_name,
        credential_value=json.dumps(config)
    )


def _delete_user_tool_config(tool_name: str) -> bool:
    """Delete user's config for a tool. Returns True if deleted."""
    credential_service = UserCredentialService()
    return credential_service.delete_credential(
        credential_type="tool_config",
        service_name=tool_name
    )


# Endpoints
@router.get("/actions/tools")
async def list_configurable_tools(
    response: Response,
    current_user: SessionData = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    List all tools with registered configuration classes.

    Returns tools that can be configured through the /actions/tools/{tool} endpoints.
    """
    request_id = generate_request_id()

    try:
        tools = _get_configurable_tools()
        credential_service = UserCredentialService()
        user_configs = credential_service.list_user_credentials()
        tool_configs = user_configs.get("tool_config", {})

        tool_list = []
        for name, config_class in tools.items():
            tool_list.append({
                "name": name,
                "config_class": config_class.__name__,
                "has_user_config": name in tool_configs
            })

        # Sort by name for consistent ordering
        tool_list.sort(key=lambda x: x["name"])

        api_response = create_success_response(
            data={"tools": tool_list, "count": len(tool_list)},
            meta={
                "request_id": request_id,
                "timestamp": format_utc_iso(utc_now())
            }
        )
        return api_response.to_dict()

    except Exception as e:
        logger.error(f"Error listing configurable tools: {e}", exc_info=True)
        api_response = create_error_response(e, request_id)
        response.status_code = 500
        return api_response.to_dict()


@router.get("/actions/tools/{tool_name}")
async def get_tool_config(
    tool_name: str,
    response: Response,
    current_user: SessionData = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get current configuration for a tool.

    Returns the user's saved config merged with defaults, or just defaults
    if no user config exists.
    """
    request_id = generate_request_id()

    try:
        # Check if tool exists in registry
        config_class = registry.get(tool_name)
        if config_class is None:
            raise NotFoundError("tool", tool_name)

        # Get defaults from config class
        defaults = config_class().model_dump()

        # Get user's saved config
        user_config = _get_user_tool_config(tool_name)
        has_user_config = user_config is not None

        # Merge user config over defaults
        if user_config:
            config = {**defaults, **user_config}
        else:
            config = defaults

        api_response = create_success_response(
            data={
                "tool_name": tool_name,
                "config": config,
                "has_user_config": has_user_config,
                "defaults": defaults
            },
            meta={
                "request_id": request_id,
                "timestamp": format_utc_iso(utc_now())
            }
        )
        return api_response.to_dict()

    except NotFoundError as e:
        api_response = create_error_response(e, request_id)
        response.status_code = 404
        return api_response.to_dict()
    except Exception as e:
        logger.error(f"Error getting tool config for {tool_name}: {e}", exc_info=True)
        api_response = create_error_response(e, request_id)
        response.status_code = 500
        return api_response.to_dict()


@router.get("/actions/tools/{tool_name}/schema")
async def get_tool_schema(
    tool_name: str,
    response: Response,
    current_user: SessionData = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get JSON Schema for a tool's configuration.

    The schema is derived from the tool's Pydantic config class and includes
    field descriptions, types, and defaults for form generation.
    """
    request_id = generate_request_id()

    try:
        # Check if tool exists in registry
        config_class = registry.get(tool_name)
        if config_class is None:
            raise NotFoundError("tool", tool_name)

        # Generate JSON Schema from Pydantic model
        schema = config_class.model_json_schema()

        api_response = create_success_response(
            data={
                "tool_name": tool_name,
                "schema": schema
            },
            meta={
                "request_id": request_id,
                "timestamp": format_utc_iso(utc_now())
            }
        )
        return api_response.to_dict()

    except NotFoundError as e:
        api_response = create_error_response(e, request_id)
        response.status_code = 404
        return api_response.to_dict()
    except Exception as e:
        logger.error(f"Error getting tool schema for {tool_name}: {e}", exc_info=True)
        api_response = create_error_response(e, request_id)
        response.status_code = 500
        return api_response.to_dict()


class ToolConfigUpdateRequest(BaseModel):
    """Request body for updating tool configuration."""
    config: Dict[str, Any] = Field(..., description="Tool configuration values")


@router.put("/actions/tools/{tool_name}")
async def update_tool_config(
    tool_name: str,
    request_body: ToolConfigUpdateRequest,
    response: Response,
    current_user: SessionData = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Update configuration for a tool.

    Validates the config against the tool's Pydantic model before saving.
    """
    request_id = generate_request_id()

    try:
        # Check if tool exists in registry
        config_class = registry.get(tool_name)
        if config_class is None:
            raise NotFoundError("tool", tool_name)

        # Validate config by instantiating the Pydantic model
        try:
            validated_config = config_class(**request_body.config)
        except PydanticValidationError as e:
            # Convert Pydantic validation errors to our format
            errors = []
            for err in e.errors():
                field = ".".join(str(loc) for loc in err["loc"])
                errors.append({
                    "field": field,
                    "message": err["msg"],
                    "type": err["type"]
                })
            raise ValidationError(
                f"Invalid configuration for {tool_name}",
                details={"validation_errors": errors}
            )

        # Save the validated config
        config_dict = validated_config.model_dump()
        _save_user_tool_config(tool_name, config_dict)

        api_response = create_success_response(
            data={
                "tool_name": tool_name,
                "config": config_dict,
                "message": f"Configuration for {tool_name} saved successfully"
            },
            meta={
                "request_id": request_id,
                "timestamp": format_utc_iso(utc_now())
            }
        )
        return api_response.to_dict()

    except NotFoundError as e:
        api_response = create_error_response(e, request_id)
        response.status_code = 404
        return api_response.to_dict()
    except ValidationError as e:
        api_response = create_error_response(e, request_id)
        response.status_code = 400
        return api_response.to_dict()
    except Exception as e:
        logger.error(f"Error updating tool config for {tool_name}: {e}", exc_info=True)
        api_response = create_error_response(e, request_id)
        response.status_code = 500
        return api_response.to_dict()


@router.post("/actions/tools/{tool_name}/validate")
async def validate_tool_config(
    tool_name: str,
    request_body: ToolConfigUpdateRequest,
    response: Response,
    current_user: SessionData = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Validate tool configuration without saving.

    Tests the config (e.g., connection test for email) and returns
    any discovered data (e.g., available folders for email).
    """
    request_id = generate_request_id()

    try:
        # Check if tool exists in registry
        config_class = registry.get(tool_name)
        if config_class is None:
            raise NotFoundError("tool", tool_name)

        # Validate config by instantiating the Pydantic model
        try:
            validated_config = config_class(**request_body.config)
        except PydanticValidationError as e:
            errors = []
            for err in e.errors():
                field = ".".join(str(loc) for loc in err["loc"])
                errors.append({
                    "field": field,
                    "message": err["msg"],
                    "type": err["type"]
                })
            raise ValidationError(
                f"Invalid configuration for {tool_name}",
                details={"validation_errors": errors}
            )

        config_dict = validated_config.model_dump()
        discovered_data = {}

        # Call tool-specific validation if the tool implements it
        discovered_data = _call_tool_validation(tool_name, config_dict)

        api_response = create_success_response(
            data={
                "tool_name": tool_name,
                "valid": True,
                "config": config_dict,
                "discovered": discovered_data,
                "message": f"Configuration for {tool_name} validated successfully"
            },
            meta={
                "request_id": request_id,
                "timestamp": format_utc_iso(utc_now())
            }
        )
        return api_response.to_dict()

    except NotFoundError as e:
        api_response = create_error_response(e, request_id)
        response.status_code = 404
        return api_response.to_dict()
    except ValidationError as e:
        api_response = create_error_response(e, request_id)
        response.status_code = 400
        return api_response.to_dict()
    except Exception as e:
        logger.error(f"Error validating tool config for {tool_name}: {e}", exc_info=True)
        api_response = create_error_response(e, request_id)
        response.status_code = 500
        return api_response.to_dict()


def _call_tool_validation(tool_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call the tool's validate_config method if it exists.

    Tools can implement custom validation (connection tests, auto-discovery)
    by overriding the validate_config classmethod.
    """
    from tools.repo import ToolRepository

    # Get the tool repository to look up tool classes
    tool_repo = ToolRepository()
    tool_repo.discover_tools()

    tool_class = tool_repo.tool_classes.get(tool_name)
    if not tool_class:
        return {}

    # Check if tool has custom validation
    if hasattr(tool_class, 'validate_config'):
        try:
            return tool_class.validate_config(config)
        except ValueError as e:
            raise ValidationError(str(e))

    return {}


@router.delete("/actions/tools/{tool_name}")
async def reset_tool_config(
    tool_name: str,
    response: Response,
    current_user: SessionData = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Reset tool configuration to defaults.

    Deletes the user's saved configuration, reverting to the tool's defaults.
    """
    request_id = generate_request_id()

    try:
        # Check if tool exists in registry
        config_class = registry.get(tool_name)
        if config_class is None:
            raise NotFoundError("tool", tool_name)

        # Delete user's config
        deleted = _delete_user_tool_config(tool_name)

        # Get defaults for response
        defaults = config_class().model_dump()

        api_response = create_success_response(
            data={
                "tool_name": tool_name,
                "config": defaults,
                "was_reset": deleted,
                "message": f"Configuration for {tool_name} reset to defaults"
            },
            meta={
                "request_id": request_id,
                "timestamp": format_utc_iso(utc_now())
            }
        )
        return api_response.to_dict()

    except NotFoundError as e:
        api_response = create_error_response(e, request_id)
        response.status_code = 404
        return api_response.to_dict()
    except Exception as e:
        logger.error(f"Error resetting tool config for {tool_name}: {e}", exc_info=True)
        api_response = create_error_response(e, request_id)
        response.status_code = 500
        return api_response.to_dict()
