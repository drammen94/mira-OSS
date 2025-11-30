"""
Generic OpenAI-compatible client with Anthropic format translation.

This client accepts Anthropic-format messages and tools, translates them to
OpenAI format, makes HTTP requests to any OpenAI-compatible endpoint, and
translates responses back to Anthropic format.

Use cases:
- Emergency failover during Anthropic outages
- On-demand calls to alternative providers (Groq, OpenRouter, local models)
- Cost optimization for simple operations (sentiment, classification, etc.)
- Local inference for development/testing

⚠️ IMPORTANT FOR CODE GENERATORS (CLAUDE):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DO NOT instantiate this class directly when writing application code!

WRONG:
    from utils.generic_openai_client import GenericOpenAIClient
    client = GenericOpenAIClient(endpoint="...", api_key="...", model="...")
    response = client.messages.create(...)

CORRECT:
    from clients.llm_provider import LLMProvider
    llm = LLMProvider()
    response = llm.generate_response(
        messages=[...],
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        model_override="anthropic/claude-3-5-sonnet",
        api_key_override=api_key
    )

WHY: Using LLMProvider.generate_response() provides:
- Consistent interface for all LLM calls (Anthropic + third-party)
- Easy migration between providers (just omit overrides for Anthropic)
- Automatic failover and error handling
- Unified logging and monitoring
- Future flexibility for routing changes

This class is ONLY used internally by LLMProvider. Application code should
never import or instantiate GenericOpenAIClient directly.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import json
import logging
import requests
from types import SimpleNamespace
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class GenericOpenAIResponse:
    """
    Mimics anthropic.types.Message structure for compatibility.

    This allows GenericOpenAIClient to be used as a drop-in replacement
    for anthropic.Anthropic client in LLMProvider codepaths.

    ⚠️ INTERNAL CLASS - DO NOT USE DIRECTLY IN APPLICATION CODE
    This is only used by LLMProvider when routing to third-party providers.
    """

    def __init__(self, content: List, stop_reason: str, usage: Dict):
        """
        Initialize response with Anthropic-compatible structure.

        Args:
            content: List of content blocks (text, tool_use)
            stop_reason: Anthropic-style stop reason (end_turn, tool_use, max_tokens)
            usage: Token usage dictionary
        """
        self.content = content
        self.stop_reason = stop_reason
        self.usage = SimpleNamespace(**usage)


class GenericOpenAIClient:
    """
    Generic client for OpenAI-compatible API endpoints with Anthropic translation.

    Provides a messages.create() interface that accepts Anthropic-format inputs
    and returns Anthropic-compatible response objects, enabling seamless integration
    with existing LLMProvider codepaths.

    ⚠️⚠️⚠️ CRITICAL - DO NOT USE THIS CLASS DIRECTLY ⚠️⚠️⚠️
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    This class is INTERNAL to LLMProvider. Application code should NEVER import
    or instantiate GenericOpenAIClient directly.

    WRONG EXAMPLE (DO NOT DO THIS):
        from utils.generic_openai_client import GenericOpenAIClient
        client = GenericOpenAIClient(
            endpoint="https://api.groq.com/openai/v1/chat/completions",
            api_key="gsk_...",
            model="llama-3.1-70b-versatile"
        )
        response = client.messages.create(messages=[...])

    CORRECT EXAMPLE (ALWAYS DO THIS):
        from clients.llm_provider import LLMProvider
        llm = LLMProvider()
        response = llm.generate_response(
            messages=[{"role": "user", "content": "Hello"}],
            endpoint_url="https://api.groq.com/openai/v1/chat/completions",
            model_override="llama-3.1-70b-versatile",
            api_key_override=groq_api_key
        )

    The ONLY code that should instantiate this class is LLMProvider._generate_non_streaming()
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: Optional[str] = None,
        timeout: int = 60,
        max_tokens: int = 4096,
        temperature: float = 1.0
    ):
        """
        Initialize generic OpenAI-compatible client.

        Args:
            endpoint: Full URL to chat completions endpoint
            model: Model identifier to use
            api_key: API key for authentication (optional for local providers like Ollama)
            timeout: Request timeout in seconds
            max_tokens: Default max tokens (can be overridden per request)
            temperature: Default temperature (can be overridden per request)
        """
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.default_max_tokens = max_tokens
        self.default_temperature = temperature

        # Create messages namespace to mimic anthropic.Anthropic interface
        self.messages = SimpleNamespace(create=self._create_message)

        logger.info(f"GenericOpenAIClient initialized: {endpoint} / {model}")

    def _create_message(
        self,
        messages: List[Dict],
        system: Optional[Any] = None,
        tools: Optional[List[Dict]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> GenericOpenAIResponse:
        """
        Create a message using OpenAI-compatible endpoint (non-streaming).

        Mimics anthropic.Anthropic.messages.create() interface for drop-in compatibility.

        Args:
            messages: Anthropic-format messages (user/assistant with content blocks)
            system: System prompt (string or list of blocks with cache_control)
            tools: Anthropic-format tool definitions
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional parameters (ignored for compatibility)

        Returns:
            GenericOpenAIResponse with Anthropic-compatible structure

        Raises:
            TimeoutError: If request times out
            PermissionError: If authentication fails
            RuntimeError: On other API errors
        """
        # Use defaults if not specified
        max_tokens = max_tokens if max_tokens is not None else self.default_max_tokens
        temperature = temperature if temperature is not None else self.default_temperature

        # Prepare messages with system prompt
        openai_messages = []
        if system:
            openai_messages.append(self._convert_system_prompt(system))

        openai_messages.extend(self._convert_messages(messages))

        logger.debug(f"Converted {len(messages)} Anthropic messages to {len(openai_messages)} OpenAI messages")

        # Build request payload
        payload = {
            "model": self.model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        # Add tools if provided
        if tools:
            openai_tools = self._convert_tools(tools)
            payload["tools"] = openai_tools
            logger.debug(f"Converted {len(tools)} Anthropic tools to {len(openai_tools)} OpenAI tools")

        # Make HTTP request
        try:
            logger.debug(f"Generic OpenAI client request to {self.endpoint} with model {self.model}")
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            response = requests.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            return self._wrap_response(response.json())

        except requests.Timeout:
            logger.error("Generic OpenAI client request timed out")
            raise TimeoutError("Generic OpenAI client request timed out")
        except requests.HTTPError as e:
            # Log full error for debugging and extract error details
            error_body = None
            try:
                error_body = e.response.json()
                logger.error(f"Generic OpenAI client HTTP error: {e.response.status_code} - {error_body}")
            except:
                logger.error(f"Generic OpenAI client HTTP error: {e.response.status_code} - {e.response.text}")
            self._handle_http_error(e, error_body)
        except Exception as e:
            logger.error(f"Generic OpenAI client error: {e}")
            raise RuntimeError(f"Generic OpenAI client error: {e}")

    def _convert_system_prompt(self, system: Any) -> Dict:
        """
        Convert Anthropic system prompt to OpenAI system message.

        Args:
            system: String or list of blocks with cache_control

        Returns:
            OpenAI system message dict
        """
        if isinstance(system, list):
            # Extract text from structured blocks (strip cache_control)
            text_parts = [b.get("text", "") for b in system if b.get("type") == "text"]
            text = "".join(text_parts)
            return {"role": "system", "content": text}
        elif isinstance(system, str):
            return {"role": "system", "content": system}
        else:
            return {"role": "system", "content": str(system)}

    def _convert_messages(self, anthropic_messages: List[Dict]) -> List[Dict]:
        """
        Convert Anthropic messages to OpenAI format.

        Handles:
        - Text content blocks → string content
        - Tool use blocks → tool_calls array
        - Tool result blocks → tool role messages
        - Thinking blocks → stripped (not supported in OpenAI)

        Args:
            anthropic_messages: Messages in Anthropic format

        Returns:
            Messages in OpenAI format
        """
        openai_messages = []
        logger.debug(f"Converting {len(anthropic_messages)} Anthropic messages")

        for msg in anthropic_messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                # Handle user messages with text or tool results
                if isinstance(content, list):
                    # Check for tool_result blocks
                    tool_results = [b for b in content if b.get("type") == "tool_result"]
                    if tool_results:
                        # Add tool result messages
                        for tr in tool_results:
                            # Handle content - could be string, dict, or other types
                            result_content = tr.get("content", "")
                            if isinstance(result_content, (dict, list)):
                                result_content = json.dumps(result_content)
                            else:
                                result_content = str(result_content)

                            openai_messages.append({
                                "role": "tool",
                                "tool_call_id": tr["tool_use_id"],
                                "content": result_content
                            })
                    else:
                        # Extract text from content blocks
                        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                        if text:
                            openai_messages.append({"role": "user", "content": text})
                else:
                    # Simple string content
                    openai_messages.append({"role": "user", "content": content})

            elif role == "assistant":
                # Handle assistant messages with text and tool_use blocks
                text_parts = []
                tool_calls = []

                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block["input"])
                                }
                            })
                        elif block.get("type") == "thinking":
                            # Skip thinking blocks (not supported in generic providers)
                            logger.debug("Skipping thinking block in generic OpenAI client")
                elif isinstance(content, str):
                    # Simple string content
                    text_parts.append(content)

                msg_obj = {"role": "assistant"}
                if text_parts:
                    msg_obj["content"] = "".join(text_parts)
                if tool_calls:
                    msg_obj["tool_calls"] = tool_calls

                openai_messages.append(msg_obj)

        logger.debug(f"Converted to {len(openai_messages)} OpenAI messages")
        return openai_messages

    def _convert_tools(self, anthropic_tools: List[Dict]) -> List[Dict]:
        """
        Convert Anthropic tool schemas to OpenAI format.

        Args:
            anthropic_tools: Tool definitions in Anthropic format

        Returns:
            Tool definitions in OpenAI format
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"]
                }
            }
            for tool in anthropic_tools
        ]

    def _wrap_response(self, openai_response: Dict) -> GenericOpenAIResponse:
        """
        Convert OpenAI response to Anthropic-compatible structure.

        Args:
            openai_response: Full OpenAI API response

        Returns:
            GenericOpenAIResponse with Anthropic-compatible structure

        Raises:
            ValueError: If response structure is invalid
        """
        # Validate response structure
        if "choices" not in openai_response or not openai_response["choices"]:
            logger.error(f"Invalid OpenAI response: missing or empty choices - {openai_response}")
            raise ValueError("Invalid OpenAI response: missing or empty choices")

        if "usage" not in openai_response:
            logger.error(f"Invalid OpenAI response: missing usage data - {openai_response}")
            raise ValueError("Invalid OpenAI response: missing usage data")

        choice = openai_response["choices"][0]
        message = choice.get("message", {})

        if not message:
            logger.error(f"Invalid OpenAI response: empty message - {openai_response}")
            raise ValueError("Invalid OpenAI response: empty message")

        content_blocks = []

        # Add text content
        if message.get("content"):
            content_blocks.append(SimpleNamespace(
                type="text",
                text=message["content"]
            ))

        # Add tool calls (preserve IDs unchanged)
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                content_blocks.append(SimpleNamespace(
                    type="tool_use",
                    id=tc["id"],
                    name=tc["function"]["name"],
                    input=json.loads(tc["function"]["arguments"])
                ))

        # Map finish reason to Anthropic stop_reason
        stop_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens"
        }
        stop_reason = stop_reason_map.get(choice["finish_reason"], "end_turn")

        # Build usage object
        usage = {
            "input_tokens": openai_response["usage"]["prompt_tokens"],
            "output_tokens": openai_response["usage"]["completion_tokens"],
            "cache_creation_input_tokens": 0,  # N/A for OpenAI
            "cache_read_input_tokens": 0
        }

        logger.debug(f"Generic OpenAI response: {len(content_blocks)} blocks, {stop_reason}")
        return GenericOpenAIResponse(content_blocks, stop_reason, usage)

    def _handle_http_error(self, error: requests.HTTPError, error_body: dict = None):
        """
        Map HTTP errors to exceptions that LLMProvider expects.

        Args:
            error: requests.HTTPError from failed request
            error_body: Optional parsed JSON error response

        Raises:
            PermissionError: For authentication failures
            ValueError: For context length exceeded errors
            RuntimeError: For rate limits and server errors
        """
        status = error.response.status_code

        # Check for context length exceeded error (400 with specific error code)
        if status == 400 and error_body:
            error_info = error_body.get("error", {})
            error_code = error_info.get("code", "")
            error_message = error_info.get("message", "")

            if "context_length" in error_code or "reduce the length" in error_message.lower():
                logger.error("Generic OpenAI client context length exceeded")
                raise ValueError(
                    "Content too large for model context window. "
                    "Reduce the message length or use a model with larger context."
                )

        if status == 401 or status == 403:
            logger.error(f"Generic OpenAI client authentication failed: {status}")
            raise PermissionError("Generic OpenAI client authentication failed")
        elif status == 429:
            logger.error("Generic OpenAI client rate limit exceeded")
            raise RuntimeError("Generic OpenAI client rate limit exceeded")
        elif status >= 500:
            logger.error(f"Generic OpenAI client server error: {status}")
            raise RuntimeError(f"Generic OpenAI client server error: {status}")
        else:
            logger.error(f"Generic OpenAI client API error: {status}")
            raise RuntimeError(f"Generic OpenAI client API error: {status}")
