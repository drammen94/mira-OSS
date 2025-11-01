"""
WebSocket chat endpoint - Real-time bidirectional communication for MIRA.

Provides persistent connections with streaming responses, eliminating the
complexity of SSE and dual code paths. Direct service integration with
proper user context management.
"""
import base64
import json
import logging
import asyncio
from typing import Dict, Any, Optional, Union, List
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from cns.services.orchestrator import ContinuumOrchestrator, get_orchestrator
from cns.infrastructure.continuum_pool import get_continuum_pool
from cns.infrastructure.continuum_repository import get_continuum_repository
from utils.distributed_lock import UserRequestLock
from utils.user_context import set_current_user_id, clear_user_context
from utils.timezone_utils import utc_now
from utils.text_sanitizer import sanitize_message_content

logger = logging.getLogger(__name__)

router = APIRouter()

def get_friendly_error_message(error: Exception) -> str:
    """
    Convert technical error messages into user-friendly explanations.
    
    Args:
        error: The exception that occurred
        
    Returns:
        A friendly error message for the user
    """
    error_str = str(error).lower()
    
    # API usage limit errors
    if "usage limit" in error_str or "rate limit" in error_str:
        return ("I'm currently rate limited. Please try again in a few moments. "
               "If this persists, the API usage limits may have been reached.")
    
    # Authentication errors
    if "authentication failed" in error_str or "401" in error_str:
        return ("There's an issue with my API authentication. "
               "Please contact support to resolve this.")
    
    # Model availability errors
    if "no allowed providers" in error_str or "model" in error_str and "404" in error_str:
        return ("The AI model I'm trying to use isn't available. "
               "Please contact support to update the configuration.")
    
    # Network errors
    if "connection" in error_str or "network" in error_str:
        return ("I'm having trouble connecting to the AI service. "
               "Please check your internet connection and try again.")
    
    # Timeout errors
    if "timeout" in error_str:
        return ("The request took too long to process. "
               "Please try again with a simpler message.")
    
    # Server errors
    if any(code in error_str for code in ["500", "502", "503"]):
        return ("The AI service is experiencing technical difficulties. "
               "Please try again in a few moments.")
    
    # Default message for unknown errors
    return ("I encountered an unexpected error while processing your message. "
           "Please try again, and if the problem persists, contact support.")

# Distributed per-user request lock
_user_request_lock = UserRequestLock(ttl=60)

# Connection tracking for graceful shutdown
_active_connections: Dict[str, WebSocket] = {}

# Image validation constants
SUPPORTED_IMAGE_FORMATS = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
MAX_IMAGE_SIZE_MB = 5


async def close_all_connections():
    """Close all active WebSocket connections gracefully."""
    if not _active_connections:
        return

    logger.info(f"Closing {len(_active_connections)} active WebSocket connections")

    # Send shutdown message to all connections
    send_tasks = []
    for conn_id, websocket in list(_active_connections.items()):
        try:
            # Create coroutine for sending shutdown message
            send_tasks.append(websocket.send_json({
                "type": "server_shutdown",
                "message": "Server is shutting down"
            }))
        except:
            pass

    # Wait for all messages to be sent
    if send_tasks:
        await asyncio.gather(*send_tasks, return_exceptions=True)

    # Close all connections
    close_tasks = []
    for conn_id, websocket in list(_active_connections.items()):
        try:
            close_tasks.append(websocket.close())
        except:
            pass

    # Wait for all closes to complete
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)

    _active_connections.clear()


class WebSocketChatHandler:
    """Handler for WebSocket chat connections."""
    
    def __init__(self):
        """Initialize with singleton service dependencies."""
        self.orchestrator = get_orchestrator()
        self.continuum_pool = get_continuum_pool()
        self.continuum_repo = get_continuum_repository()
        self.auth_service = AuthService()
        self.session_manager = SessionManager()
    
    async def authenticate(self, websocket: WebSocket) -> Optional[str]:
        """
        Authenticate WebSocket connection via first message.

        Returns user_id if successful, None otherwise.
        """
        try:
            # Wait for auth message (with timeout)
            auth_data = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=10.0
            )
            
            if auth_data.get("type") != "auth":
                await websocket.send_json({
                    "type": "error",
                    "message": "First message must be authentication"
                })
                return None
            
            # Accept header token or cookie-based session
            token = auth_data.get("token") or websocket.cookies.get("session")
            if not token:
                await websocket.send_json({
                    "type": "error",
                    "message": "Missing authentication token"
                })
                return None

            # Validate session
            session_data = self.session_manager.validate_session(token)
            if not session_data:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid or expired session"
                })
                return None

            # Send auth success
            user_id = session_data.get('user_id') if isinstance(session_data, dict) else session_data.user_id
            await websocket.send_json({
                "type": "auth_success",
                "user_id": user_id
            })

            # Set user context for the websocket connection
            set_current_user_id(user_id)

            return user_id
            
        except asyncio.TimeoutError:
            await websocket.send_json({
                "type": "error",
                "message": "Authentication timeout"
            })
            return None
        except Exception as e:
            logger.error(f"Authentication error: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": f"Authentication failed: {str(e)}"
            })
            return None
    
    async def process_message_streaming(
        self,
        websocket: WebSocket,
        user_id: str,
        message_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process a chat message with real-time streaming via queue.

        Returns metadata about the completed request.
        """
        try:
            # Extract and validate message components
            content = message_data.get("content", "").strip()
            if not content:
                await websocket.send_json({"type": "error", "message": "Message cannot be empty"})
                return {"error": "Message cannot be empty"}

            # Sanitize content
            content = sanitize_message_content(content)

            # Extract optional image data
            image_base64 = message_data.get("image")
            image_type = message_data.get("image_type")

            # Validate image data if provided
            if image_base64:
                if not image_type:
                    await websocket.send_json({"type": "error", "message": "image_type is required when image is provided"})
                    return {"error": "image_type is required"}

                if image_type not in SUPPORTED_IMAGE_FORMATS:
                    msg = f"Unsupported image format. Supported: {', '.join(SUPPORTED_IMAGE_FORMATS)}"
                    await websocket.send_json({"type": "error", "message": msg})
                    return {"error": msg}

                # Validate base64 encoding and size
                try:
                    decoded = base64.b64decode(image_base64, validate=True)
                    if len(decoded) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
                        msg = f"Image exceeds maximum size of {MAX_IMAGE_SIZE_MB}MB"
                        await websocket.send_json({"type": "error", "message": msg})
                        return {"error": msg}
                except Exception as e:
                    msg = f"Invalid base64 image: {str(e)}"
                    await websocket.send_json({"type": "error", "message": msg})
                    return {"error": msg}

            # Create queue for streaming
            queue = asyncio.Queue(maxsize=100)
            loop = asyncio.get_event_loop()

            # Create callback that pushes to queue
            def stream_to_queue(event_data: Dict[str, Any]):
                """Push event to queue from sync context."""
                future = asyncio.run_coroutine_threadsafe(
                    queue.put(event_data),
                    loop
                )
                # Wait for put to complete (with timeout)
                future.result(timeout=1.0)

            # User context already set in authenticate() method

            try:
                logger.info(f"Getting continuum for user {user_id}")
                # Get user's continuum
                continuum = await run_in_threadpool(
                    self._get_user_continuum
                )
                logger.info(f"Got continuum {continuum.id}, starting orchestrator")

                # Start orchestrator processing in thread pool with streaming
                process_task = asyncio.create_task(
                    run_in_threadpool(
                        self._process_with_orchestrator_streaming,
                        continuum,
                        content,
                        image_base64,
                        image_type,
                        stream_to_queue  # Pass our callback
                    )
                )

                # Consume queue and stream to websocket
                try:
                    while True:
                        # Wait for events with timeout
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=60.0)
                        except asyncio.TimeoutError:
                            # Check if process is still running
                            if process_task.done():
                                break
                            continue

                        # Handle different event types
                        if event.get("type") == "text":
                            await websocket.send_json({
                                "type": "text",
                                "content": event.get("content", "")
                            })
                        elif event.get("type") == "thinking":
                            await websocket.send_json({
                                "type": "thinking",
                                "content": event.get("content", "")
                            })
                        elif event.get("type") == "tool_event":
                            await websocket.send_json({
                                "type": "tool",
                                "event": event.get("event"),
                                "name": event.get("tool")
                            })
                        elif event.get("type") == "complete":
                            # Orchestrator finished
                            break
                        elif event.get("type") == "error":
                            await websocket.send_json({
                                "type": "error",
                                "message": event.get("message", "Unknown error")
                            })
                            return {"error": event.get("message")}

                    # Get final result from orchestrator
                    result = await process_task
                    return result

                except asyncio.CancelledError:
                    process_task.cancel()
                    raise
                finally:
                    # Clean up
                    if not process_task.done():
                        process_task.cancel()

            finally:
                clear_user_context()

        except Exception as e:
            logger.error(f"Streaming message error: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": get_friendly_error_message(e)
            })
            return {"error": str(e)}

    def _process_with_orchestrator_streaming(
        self,
        continuum,
        content: str,
        image_base64: Optional[str] = None,
        image_type: Optional[str] = None,
        stream_callback = None
    ) -> Dict[str, Any]:
        """Process message through orchestrator with streaming callback.

        TODO: Rename to _process_with_orchestrator now that non-streaming version is deleted.
        """
        # Get system prompt from config
        from config.config_manager import config
        if not config.system_prompt:
            raise ValueError("System prompt not configured")

        # Create multimodal content if image is provided
        if image_base64 and image_type:
            message_content = [
                {"type": "text", "text": content},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_type,
                        "data": image_base64
                    }
                }
            ]
        else:
            message_content = content

        # Create unit of work for batch operations
        unit_of_work = self.continuum_pool.begin_work(continuum)

        try:
            # Process message with streaming callback
            continuum, response_text, metadata = self.orchestrator.process_message(
                continuum,
                message_content,
                config.system_prompt,
                stream=True,
                stream_callback=stream_callback,  # Pass the callback!
                unit_of_work=unit_of_work
            )

            # Commit all changes atomically
            unit_of_work.commit()

            # Signal completion
            if stream_callback:
                stream_callback({"type": "complete"})

            return {
                "continuum": continuum,
                "response": response_text,
                "metadata": metadata
            }
        except Exception as e:
            logger.error(f"Orchestrator processing error: {e}", exc_info=True)
            # Send error through callback
            if stream_callback:
                stream_callback({
                    "type": "error",
                    "message": get_friendly_error_message(e)
                })
            raise
    
    def _get_user_continuum(self):
        """Get user's single continuum."""
        # Context already set by async handler and copied by run_in_threadpool
        # Get or create the user's single continuum
        continuum = self.continuum_pool.get_or_create()
        return continuum
    
    async def handle_connection(self, websocket: WebSocket, user_id: str):
        """
        Main message loop for authenticated WebSocket connection.
        """
        connection_id = str(uuid4())
        _active_connections[connection_id] = websocket

        # Acquire user lock
        if not _user_request_lock.acquire(user_id):
            logger.warning(f"Failed to acquire lock for user {user_id} - stale lock from previous connection still active")
            await websocket.send_json({
                "type": "error",
                "message": "MIRA is designed in a way where each user has a 'lock' on a connection to the server. For some reason yours didn't expire last time you disconnected. It will clear in 60 seconds. Please refresh the page in one minute."
            })
            await websocket.close()
            return
        
        try:
            while True:
                # Receive message
                message_data = await websocket.receive_json()
                
                if message_data.get("type") == "ping":
                    # Simple keepalive
                    await websocket.send_json({"type": "pong"})
                    continue
                
                if message_data.get("type") != "message":
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Unknown message type: {message_data.get('type')}"
                    })
                    continue

                logger.info(f"Received message from user {user_id}: {message_data.get('content', '')[:100]}")

                # Process the message with real-time streaming
                start_time = utc_now()
                result = await self.process_message_streaming(websocket, user_id, message_data)

                logger.info(f"Message processing result: {result.get('error', 'success')}")

                if "error" in result:
                    # Error already sent via websocket in process_message_streaming
                    continue

                # Send completion message
                processing_time_ms = int((utc_now() - start_time).total_seconds() * 1000)
                await websocket.send_json({
                    "type": "complete",
                    "continuum_id": str(result["continuum"].id),
                    "response": result.get("response", ""),  # Include the response text!
                    "metadata": {
                        "tools_used": result["metadata"].get("tools_used", []),
                        "processing_time_ms": processing_time_ms
                    }
                })
                
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected for user {user_id}")
        except Exception as e:
            logger.error(f"WebSocket error for user {user_id}: {e}", exc_info=True)
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": get_friendly_error_message(e)
                })
            except:
                pass
        finally:
            # Cleanup
            _user_request_lock.release(user_id)
            _active_connections.pop(connection_id, None)


# WebSocket endpoint
@router.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    """
    WebSocket chat endpoint with authentication and streaming.
    
    Protocol:
    1. Accept connection
    2. Receive auth message: {"type": "auth", "token": "..."}
    3. Send auth result
    4. Enter message loop
    
    Message format:
    - Client: {"type": "message", "content": "...", "stream": bool, "image": "base64...", "image_type": "image/jpeg"}
    - Server: Various message types (text, tool, error, complete)
    """
    await websocket.accept()
    
    try:
        handler = WebSocketChatHandler()
        
        # Authenticate
        user_id = await handler.authenticate(websocket)
        if not user_id:
            await websocket.close()
            return
        
        # Handle connection
        await handler.handle_connection(websocket, user_id)
        
    except Exception as e:
        logger.error(f"WebSocket endpoint error: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Server error: {str(e)}"
            })
        except:
            pass
        await websocket.close()
