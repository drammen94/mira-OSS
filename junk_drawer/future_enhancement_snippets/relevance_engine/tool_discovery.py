import logging
import os
from typing import List, Set

from config import config


class ToolDiscovery:
    
    def __init__(self, tools_data_dir: str):
        self.logger = logging.getLogger("tool_discovery")
        self.tools_data_dir = tools_data_dir
        
        os.makedirs(self.tools_data_dir, exist_ok=True)
    
    def _sanitize_tool_name(self, tool_name: str) -> str:
        if not tool_name or not isinstance(tool_name, str):
            raise ValueError(f"Invalid tool name: {tool_name}")
        
        if '..' in tool_name or '/' in tool_name or '\\' in tool_name:
            raise ValueError(f"Tool name contains path traversal elements: {tool_name}")
        
        if tool_name.startswith(('/', '\\', '~')) or ':' in tool_name:
            raise ValueError(f"Tool name contains absolute path elements: {tool_name}")
        
        if not all(c.isalnum() or c in ('_', '-') for c in tool_name):
            raise ValueError(f"Tool name contains invalid characters: {tool_name}")
        
        return tool_name
    
    def discover_tools(self) -> Set[str]:
        discovered_tools = set()
        
        tools_dir = 'tools'
        if hasattr(config, 'paths') and hasattr(config.paths, 'tools_dir'):
            tools_dir = config.paths.tools_dir
        
        if os.path.exists(tools_dir):
            try:
                for file in os.listdir(tools_dir):
                    if file.endswith('_tool.py') and not file.startswith('__'):
                        tool_name = file[:-3]
                        try:
                            sanitized_name = self._sanitize_tool_name(tool_name)
                            discovered_tools.add(sanitized_name)
                        except ValueError as e:
                            self.logger.warning(f"Skipping tool with invalid name '{tool_name}': {e}")
                
                self.logger.info(f"Discovered {len(discovered_tools)} tools")
            except Exception as e:
                self.logger.error(f"Error scanning tools directory {tools_dir}: {e}")
        else:
            self.logger.warning(f"Tools directory {tools_dir} does not exist")
        
        return discovered_tools
    
    def get_existing_tool_directories(self) -> Set[str]:
        existing_tools = set()
        
        try:
            if os.path.exists(self.tools_data_dir):
                for entry in os.scandir(self.tools_data_dir):
                    if entry.is_dir():
                        tool_name = entry.name
                        try:
                            sanitized_name = self._sanitize_tool_name(tool_name)
                            existing_tools.add(sanitized_name)
                        except ValueError as e:
                            self.logger.warning(f"Skipping directory with invalid name '{tool_name}': {e}")
        except Exception as e:
            self.logger.error(f"Error scanning tools data directory: {e}")
        
        return existing_tools
    
    def create_tool_data_directories(self, tool_names: Set[str]) -> List[str]:
        created_dirs = []
        
        for tool_name in tool_names:
            try:
                sanitized_name = self._sanitize_tool_name(tool_name)
                tool_data_dir = os.path.join(self.tools_data_dir, sanitized_name)
                os.makedirs(tool_data_dir, exist_ok=True)
                created_dirs.append(tool_data_dir)
            except Exception as e:
                self.logger.error(f"Error creating data directory for tool '{tool_name}': {e}")
        
        return created_dirs
    
    
    def get_all_tool_directories(self) -> List[str]:
        discovered_tools = self.discover_tools()
        existing_tools = self.get_existing_tool_directories()
        
        all_tools = discovered_tools.union(existing_tools)
        tool_dirs = self.create_tool_data_directories(all_tools)
        
        return tool_dirs
    
    def get_tool_source_path(self, tool_name: str) -> str:
        sanitized_name = self._sanitize_tool_name(tool_name)
        
        tools_dir = 'tools'
        if hasattr(config, 'paths') and hasattr(config.paths, 'tools_dir'):
            tools_dir = config.paths.tools_dir
        
        return os.path.join(tools_dir, f"{sanitized_name}.py")
    
    def get_tool_data_directory(self, tool_name: str) -> str:
        sanitized_name = self._sanitize_tool_name(tool_name)
        return os.path.join(self.tools_data_dir, sanitized_name)