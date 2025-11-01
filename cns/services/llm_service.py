"""
LLM service wrapper for CNS.

Provides a clean interface to LLM operations using the existing
greenfield LLMProvider but with CNS-specific abstractions.
"""
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass

from clients.llm_provider import LLMProvider


@dataclass
class LLMResponse:
    """Response from LLM with parsed content."""
    text: str
    tool_calls: List[Dict[str, Any]]
    raw_response: Dict[str, Any]
    
    @property
    def has_tool_calls(self) -> bool:
        """Check if response has tool calls."""
        return len(self.tool_calls) > 0
    
    @property
    def has_content(self) -> bool:
        """Check if response has text content."""
        return bool(self.text and self.text.strip())


class LLMService:
    """
    Service wrapper around LLMProvider for CNS.
    
    Provides clean interface for LLM operations with CNS-specific
    response handling and abstractions.
    """
    
    def __init__(self, llm_provider: LLMProvider):
        """Initialize with LLM provider."""
        self.llm_provider = llm_provider
    
    def generate_response(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        working_memory_content: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        stream_callback: Optional[Callable] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate response from LLM.
        
        Args:
            messages: Continuum messages in OpenAI format
            system_prompt: Base system prompt
            working_memory_content: Dynamic content from working memory
            tools: Available tool definitions
            stream: Whether to stream response
            stream_callback: Callback for streaming chunks
            **kwargs: Additional LLM parameters
            
        Returns:
            LLMResponse with parsed content
        """
        # Build system content
        system_content = system_prompt
        if working_memory_content:
            system_content += f"\n\n{working_memory_content}"
        
        # Add system message to continuum messages
        complete_messages = [{"role": "system", "content": system_content}] + messages
        
        # Call LLM provider
        response = self.llm_provider.generate_response(
            messages=complete_messages,
            tools=tools,
            stream=stream,
            callback=stream_callback,
            **kwargs
        )
        
        # Parse response
        text_content = self.llm_provider.extract_text_content(response)
        tool_calls = self.llm_provider.extract_tool_calls(response)
        
        return LLMResponse(
            text=text_content,
            tool_calls=tool_calls,
            raw_response=response
        )