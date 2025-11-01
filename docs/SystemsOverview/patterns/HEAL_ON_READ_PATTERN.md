# Heal-on-Read Pattern

## Overview

The heal-on-read pattern in MIRA's memory system automatically removes dead memory links during traversal, eliminating the need for scheduled cleanup jobs. Dead links are detected and removed precisely when and where they matter - during actual usage.

## Implementation

### Detection During Traversal

When traversing memory links, the system batch-fetches all linked memories and compares requested vs. found IDs:

```python
# lt_memory/memory_link_traverser.py:79-93

# Batch fetch all directly linked memories
linked_ids = [link['uuid'] for link in links]
memories = self.vector_store.get_memories_by_ids(linked_ids)

# Create a map of memory_id to link info for quick lookup
link_map = {link['uuid']: link for link in links}

# Detect dead links (UUIDs that didn't return memories)
found_memory_ids = {str(memory['id']) for memory in memories}
dead_links = [uuid for uuid in linked_ids if uuid not in found_memory_ids]

# Lazy cleanup: remove dead links from all memories
if dead_links:
    self._cleanup_dead_links(dead_links)
```

### Cleanup Execution

Dead links are removed from the database in a single operation:

```python
# lt_memory/memory_link_traverser.py:121-136

def _cleanup_dead_links(self, dead_uuids: List[str]) -> None:
    """
    Remove specific dead UUIDs from all memory link arrays.

    Args:
        dead_uuids: List of UUIDs to remove from link arrays
    """
    if not dead_uuids:
        return

    try:
        removed_count = self.vector_store.remove_dead_links(dead_uuids)
        if removed_count > 0:
            logger.info(f"Lazy cleanup removed {removed_count} dead link references for UUIDs: {dead_uuids}")
    except Exception as e:
        logger.warning(f"Failed to clean up dead links {dead_uuids}: {e}")
```

## Key Properties

### Self-Maintaining
The system maintains itself through normal usage. Frequently accessed memory paths stay clean automatically, while rarely accessed areas can safely accumulate dead links without impact.

### Zero Overhead
No background jobs, no scheduled scans, no maintenance windows. Cleanup work is proportional to actual usage, not data size.

### Transparent
Dead link cleanup is silent and non-blocking. Users experience no errors or delays - the system continues with valid links while dead ones are removed asynchronously.

### Scalable
Whether managing 100 or 100 million memories, cleanup cost remains constant per traversal, not per total memory count.

## Design Rationale

Traditional approaches require batch jobs that scan entire datasets looking for broken references. This pattern instead:

1. Only spends compute on actively used data
2. Scales with usage patterns, not data volume
3. Provides immediate consistency for active paths
4. Allows dormant data to remain untouched

The pattern emerges from a simple principle: broken links only matter when someone tries to follow them. At that precise moment, they're detected and removed, keeping the active working set clean while avoiding unnecessary work on unused data.