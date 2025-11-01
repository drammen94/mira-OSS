# Procedural Memory Reintegration Guide

## Overview
This directory contains the original procedural memory implementation that was decoupled from MIRA pending a redesign. The new vision is to reimagine procedural memory as an extension to the `invokeother_tool` pattern rather than a separate manager system.

## Original Implementation Architecture

### Components Moved Here
1. **procedural_memory_manager.py** - Main manager class with embedding-based detection
2. **procedural_memory_trinket.py** - WorkingMemory trinket for prompt injection
3. **JSON procedure definitions** - appointment_booking.json, smart_home_automation.json, etc.

### Original Integration Points (Now Removed)

#### factory.py
- Import: `from tools.procedural_memory.procedural_memory_manager import ProceduralMemoryManager`
- Instance creation in `_get_procedural_memory_manager()` method
- Passed to orchestrator during initialization
- Trinket registration: `from working_memory.trinkets.procedural_memory_trinket import ProceduralMemoryTrinket`

#### orchestrator.py
- Constructor parameter: `procedural_memory_manager`
- Detection logic: `procedural_memory_manager.detect_procedural_memory(embedded_msg)` (line ~159)
- Response processing: `procedural_memory_manager.process_response_commands()` (line ~308)
- Event publishing: `ProceduralMemoryDetectedEvent`
- Trinket updates via working memory

#### event_bus.py
- Constructor parameter: `procedural_memory_manager`
- Component reference: `self.procedural_memory_manager = procedural_memory_manager`
- Event handler: `_handle_procedural_memory_detected()` subscriber
- Shutdown cleanup: Reset procedural_memory_manager reference

### Cleaned Up (Removed from Codebase)

All dormant code has been removed to maintain clean architecture:

#### cns/core/events.py
- ✅ Removed `ProceduralMemoryEvent` base class
- ✅ Removed `ProceduralMemoryDetectedEvent` concrete event
- ✅ Updated event categories documentation

#### cns/core/conversation.py
- ✅ Removed `procedural_memory_id` property
- ✅ Removed `set_procedural_memory()` method

#### cns/core/state.py
- ✅ Removed `with_procedural_memory()` method

#### tests/
- ⚠️ Test files still reference procedural memory but will be skipped/fail until feature is reimplemented
- Files: `test_embedded_message.py`, `test_orchestrator_embeddings.py`, `test_openai_embeddings_integration.py`
- These tests can be removed or will need updating when procedural memory is reimplemented

## New Design Vision

### Concept
Procedural memory as documentation-based guidance integrated with `invokeother_tool`:

- **lookup_procedure** capability added to `invokeother_tool`
- Procedures stored as structured documentation (markdown/JSON)
- LLM requests procedure details when uncertain about multi-step processes
- Discovery pattern: procedures are discoverable like tools
- Examples: "how to reset user password", "deployment checklist", "debug process for X"

### Design Advantages
1. **Unified Discovery**: Procedures use the same hint/discovery mechanism as tools
2. **On-Demand Loading**: Procedures loaded only when needed, reducing token usage
3. **Simpler Architecture**: No separate manager, embedding model, or detection logic
4. **LLM-Driven**: The model decides when to request procedural guidance
5. **Flexible Format**: Procedures can be simple markdown or structured JSON

## Reintegration Steps (When Ready)

### Phase 1: Design New Procedure Format
1. Define procedure structure (markdown templates or JSON schema)
2. Create `tools/procedures/` directory for procedure storage
3. Add procedure hints to `tools/registry.json` alongside tool hints

### Phase 2: Extend invokeother_tool
1. Add `lookup_procedure` action to `InvokeOtherTool` class
2. Implement procedure search/retrieval logic
3. Format procedure content for LLM consumption
4. Add to tool description: "Can also lookup_procedure for step-by-step guidance"

### Phase 3: Convert Existing Procedures
1. Review JSON procedures in this directory
2. Convert to new format (likely simplified)
3. Place in `tools/procedures/`
4. Add hints to registry

### Phase 4: Testing
1. Create test cases for procedure lookup
2. Verify discovery mechanism works
3. Test procedure surfacing in context
4. Validate token usage optimization

### Phase 5: Documentation
1. Update `docs/tools_system_overview.md`
2. Create `docs/procedural_memory_system.md` for new approach
3. Add examples to `HOW_TO_BUILD_A_TOOL.md`

## Migration Considerations

### What to Preserve
- The JSON procedure definitions contain valuable structured workflow knowledge
- The concept of step-by-step guidance for complex processes
- The idea of contextual activation based on conversation content

### What to Discard
- Embedding-based detection (replaced by LLM-driven requests)
- ProceduralMemoryManager class (replaced by invokeother_tool extension)
- Separate trinket (procedures surface through tool results)
- Response command processing (simplified to tool invocation)

### Data to Migrate
Review these JSON files for valuable procedures:
- appointment_booking.json
- smart_home_automation.json
- create_sequence.json
- create_simple_task.json
- delete_automation.json
- execute_automation.json
- update_automation.json
- view_automations.json

## Questions for Future Implementation
1. Should procedures be pure markdown, structured JSON, or both?
2. How granular should procedure hints be in the registry?
3. Should procedures support parameters/variables?
4. How do we handle procedure versioning?
5. Should procedures be user-specific or system-wide?

## Related Documents
- LOOSEENDS_PUNCHLIST.md - Original task moved to FUTURE_ENHANCEMENTS.md
- FUTURE_ENHANCEMENTS.md - Contains the redesigned procedural memory vision
- docs/ADR_invokeother_tool_pattern.md - Core pattern this will extend

---
**Date Archived**: 2025-10-17
**Reason**: Pending redesign as extension to invokeother_tool pattern
**Status**: Complete implementation archived, awaiting architectural redesign
