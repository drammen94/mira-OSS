# Tools System Overview

## What is the Tools System?

MIRA's tools system provides an extensible framework for integrating external capabilities into conversations. It features automatic user isolation, workflow orchestration, and dynamic tool loading through the `invokeother_tool` pattern. All tools are available to the LLM, which determines relevance based on natural language understanding of tool documentation.

## Architecture Overview

### Directory Structure
```
tools/
├── implementations/      # Individual tool implementations
├── supporting_scripts/  # Helper scripts for tools
├── workflows/          # Multi-step workflow definitions
└── repo.py            # Base Tool class and ToolRepository
```

## Core Components

### 1. **Base Tool Class** (`repo.py:29-206`)

The abstract base class that all tools inherit from:

**Key Features**:
- **User Isolation**: Automatic per-user data directories via `user_data_path` property
- **Database Access**: User-scoped database access through `db` property
- **Config Registration**: Automatic configuration model registration
- **File Operations**: User-aware file methods (`make_dir()`, `open_file()`, `file_exists()`)
- **Metadata Generation**: Automatic parameter extraction from docstrings

**Required Implementation**:
```python
class MyTool(Tool):
    name = "my_tool"
    description = "Tool description"
    
    def run(self, **params) -> Dict[str, Any]:
        # Implementation here
        return {"result": "success"}
```

**Tool Properties**:
- `name`: Unique identifier for the tool
- `description`: Human-readable description
- `usage_examples`: List of example input/output pairs
- `openai_schema`: Optional OpenAI function calling schema

### 2. **Tool Repository** (`repo.py:208-525`)

Manages tool registration, instantiation, and invocation:

**Core Operations**:
- **Registration**: `register_tool_class()` - Registers tool classes for lazy instantiation
- **Enable/Disable**: Dynamic tool management with dependency resolution
- **Invocation**: `invoke_tool()` - Creates fresh instances with current user context
- **Metadata**: Provides tool definitions for LLM function calling

**Key Design Decisions**:
- **Lazy Instantiation**: Tools are instantiated per invocation with current user context
- **No Caching**: Prevents user data leakage across requests
- **Dependency Injection**: Automatic injection of LLMProvider, ToolRepository dependencies
- **Auto-discovery**: Scans implementations directory for tool classes

### 3. **Tool Relevance Engine** (Removed)

**Note**: The tool relevance engine has been removed in favor of simpler alternatives. With modern context windows (200k+ tokens) and the `invokeother_tool` pattern for dynamic tool loading, providing all tool definitions to the LLM is more effective than ML-based classification. See `junk_drawer/future_enhancement_snippets/relevance_engine/REINTEGRATION_GUIDE.md` for details on the original architecture and why the all-tools-always approach is preferred.

### 4. **Tool Implementations** (`implementations/`)

Individual tool implementations following the base class pattern:

**Common Tools**:
- **reminder_tool**: SQLite-based reminder management with contact integration
- **calendar_tool**: Calendar event management
- **email_tool**: Email sending/receiving capabilities
- **weather_tool**: Weather information retrieval
- **webaccess_tool**: Web content fetching and analysis
- **customerdatabase_tool**: Customer relationship management
- **maps_tool**: Location and mapping services
- **contacts_tool**: Contact management
- **pager_tool**: Alert and notification system

**Tool Features**:
- User-specific SQLite databases for data storage
- Contact UUID integration for cross-tool references
- Natural language date/time parsing
- Rich parameter validation
- Comprehensive error handling

### 5. **Procedural Memory** (Removed)

**Note**: The procedural memory system has been removed pending architectural redesign. See `junk_drawer/future_enhancement_snippets/procedural_memory/REINTEGRATION_GUIDE.md` for details on the planned invokeother_tool-based approach.

## Data Flow

### Tool Invocation Flow
```
LLM requests tool call → ToolRepository.invoke_tool()
    ↓
Fresh tool instance created with user context
    ↓
Tool.run() executed with parameters
    ↓
Results returned to LLM
    ↓
Tool instance discarded (no cross-request state)
```

## User Isolation

### Data Storage
- Each tool gets a dedicated directory: `data/users/{user_id}/tools/{tool_name}/`
- SQLite databases are user-specific
- File operations automatically scoped to user directory

### Database Access
- Tools use `self.db` property for user-scoped database
- Automatic RLS (Row Level Security) enforcement
- No cross-user data access possible

### Instance Management
- Fresh tool instances per invocation
- No shared state between requests
- User context injected at instantiation

## Configuration

### Tool Configuration
Each tool can register a Pydantic config model:
```python
class MyToolConfig(BaseModel):
    enabled: bool = True
    api_key: Optional[str] = None
    timeout: int = 30

registry.register("my_tool", MyToolConfig)
```

### System Configuration
Tool configuration is managed through individual tool config models registered with the configuration registry. See individual tool implementations for available configuration options.

## Tool Development

### Creating a New Tool
1. Create file in `implementations/` directory
2. Inherit from `Tool` base class
3. Implement `run()` method
4. Add metadata (name, description, examples)
5. Optional: Create OpenAI schema for better LLM integration

### Example Tool Implementation
```python
from tools.repo import Tool
from typing import Dict, Any

class SampleTool(Tool):
    name = "sample_tool"
    description = "A sample tool that does X"
    
    usage_examples = [
        {
            "input": {"param": "value"},
            "output": {"result": "success"}
        }
    ]
    
    def run(self, param: str) -> Dict[str, Any]:
        """
        Execute sample operation.
        
        Args:
            param: Parameter description
            
        Returns:
            Dict with result
        """
        # Use self.user_data_path for file storage
        data_file = self.user_data_path / "data.json"
        
        # Use self.db for database operations
        results = self.db.execute_query("SELECT * FROM table")
        
        return {
            "result": "success",
            "data": results
        }
```

## Integration Points

### With CNS
- All tools available to LLM via function calling
- Tools dynamically loaded via `invokeother_tool` pattern
- Tool calls handled by orchestrator
- Results integrated into conversation

### With Working Memory
- Tool guidance updated in system prompt
- Available tools listed for LLM awareness
- Workflow progress tracked

### With LT Memory
- Tool interactions can trigger memory extraction
- Contact UUIDs shared across tools

## Benefits

1. **Simplicity**: All tools always available - no ML classification overhead
2. **User Isolation**: Complete data separation by design
3. **Extensibility**: Easy to add new tools following the pattern
4. **Performance**: Lazy instantiation prevents unnecessary resource usage
5. **Dynamic Loading**: `invokeother_tool` enables on-demand tool loading
6. **Type Safety**: Pydantic models and type hints throughout