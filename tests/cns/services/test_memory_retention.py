"""
Tests for memory retention logic in orchestrator.py.

Focus: Retention application and memory merging without full orchestrator setup.
"""
import pytest


class TestRetentionApplication:
    """Tests for _apply_retention() - filtering memories by retained texts."""

    def test_applies_retention_by_text_match(self):
        """CONTRACT: Filters memories keeping only those whose text is in retained_texts."""
        from cns.services.orchestrator import ContinuumOrchestrator

        previous_memories = [
            {"id": "1", "text": "Taylor prefers PgBouncer"},
            {"id": "2", "text": "Taylor's birthday is March 15"},
            {"id": "3", "text": "Production DB on port 5433"},
        ]

        retained_texts = {"Taylor prefers PgBouncer", "Production DB on port 5433"}

        pinned = ContinuumOrchestrator._apply_retention(
            None, previous_memories, retained_texts
        )

        assert len(pinned) == 2
        assert all(m['text'] in retained_texts for m in pinned)
        assert any(m['id'] == "1" for m in pinned)
        assert any(m['id'] == "3" for m in pinned)
        assert not any(m['id'] == "2" for m in pinned)

    def test_returns_empty_list_when_no_retained_texts(self):
        """CONTRACT: Empty retained_texts set means nothing is pinned."""
        from cns.services.orchestrator import ContinuumOrchestrator

        previous_memories = [
            {"id": "1", "text": "Memory A"},
            {"id": "2", "text": "Memory B"},
        ]

        pinned = ContinuumOrchestrator._apply_retention(
            None, previous_memories, set()
        )

        assert pinned == []

    def test_returns_empty_list_when_no_previous_memories(self):
        """CONTRACT: No previous memories means nothing to pin."""
        from cns.services.orchestrator import ContinuumOrchestrator

        retained_texts = {"Some text"}

        pinned = ContinuumOrchestrator._apply_retention(
            None, [], retained_texts
        )

        assert pinned == []

        pinned = ContinuumOrchestrator._apply_retention(
            None, None, retained_texts
        )

        assert pinned == []

    def test_requires_exact_text_match(self):
        """CONTRACT: Only exact text matches count - partial matches don't retain."""
        from cns.services.orchestrator import ContinuumOrchestrator

        previous_memories = [
            {"id": "1", "text": "Taylor prefers PgBouncer over built-in pooling"},
        ]

        # Partial match - should not retain
        retained_texts = {"Taylor prefers PgBouncer"}

        pinned = ContinuumOrchestrator._apply_retention(
            None, previous_memories, retained_texts
        )

        assert pinned == []

    def test_handles_memories_with_empty_text(self):
        """CONTRACT: Memories with empty or missing text are not retained."""
        from cns.services.orchestrator import ContinuumOrchestrator

        previous_memories = [
            {"id": "1", "text": "Valid memory"},
            {"id": "2", "text": ""},
            {"id": "3"},  # No text key
        ]

        retained_texts = {"Valid memory", ""}

        pinned = ContinuumOrchestrator._apply_retention(
            None, previous_memories, retained_texts
        )

        # Only the valid memory should be retained
        assert len(pinned) == 1
        assert pinned[0]['id'] == "1"

    def test_preserves_full_memory_dict(self):
        """CONTRACT: Retained memories keep all their original fields."""
        from cns.services.orchestrator import ContinuumOrchestrator

        previous_memories = [
            {
                "id": "1",
                "text": "Memory text",
                "importance_score": 0.85,
                "created_at": "2024-01-01T00:00:00",
                "linked_memories": [{"id": "linked-1"}]
            },
        ]

        retained_texts = {"Memory text"}

        pinned = ContinuumOrchestrator._apply_retention(
            None, previous_memories, retained_texts
        )

        assert len(pinned) == 1
        assert pinned[0]['importance_score'] == 0.85
        assert pinned[0]['created_at'] == "2024-01-01T00:00:00"
        assert pinned[0]['linked_memories'] == [{"id": "linked-1"}]


class TestMemoryMerging:
    """Tests for _merge_memories() - combining pinned and fresh memories."""

    def test_pinned_memories_appear_first(self):
        """CONTRACT: Pinned memories always precede fresh memories in output."""
        from cns.services.orchestrator import ContinuumOrchestrator

        pinned = [
            {"id": "pinned-1", "text": "Pinned A"},
            {"id": "pinned-2", "text": "Pinned B"},
        ]

        fresh = [
            {"id": "fresh-1", "text": "Fresh A"},
            {"id": "fresh-2", "text": "Fresh B"},
        ]

        merged = ContinuumOrchestrator._merge_memories(None, pinned, fresh)

        assert len(merged) == 4
        assert merged[0]['id'] == "pinned-1"
        assert merged[1]['id'] == "pinned-2"
        assert merged[2]['id'] == "fresh-1"
        assert merged[3]['id'] == "fresh-2"

    def test_deduplicates_by_memory_id(self):
        """CONTRACT: Same ID in pinned and fresh appears only once (pinned takes precedence)."""
        from cns.services.orchestrator import ContinuumOrchestrator

        duplicate_id = "shared-id"

        pinned = [
            {"id": duplicate_id, "text": "Pinned version", "source": "pinned"},
        ]

        fresh = [
            {"id": duplicate_id, "text": "Fresh version", "source": "fresh"},
            {"id": "fresh-only", "text": "Fresh only"},
        ]

        merged = ContinuumOrchestrator._merge_memories(None, pinned, fresh)

        # Only 2 total - duplicate removed
        assert len(merged) == 2

        # The pinned version is kept, not fresh
        dup_memory = next(m for m in merged if m['id'] == duplicate_id)
        assert dup_memory['source'] == "pinned"

    def test_fresh_memories_added_after_pinned(self):
        """CONTRACT: Fresh memories that aren't duplicates follow pinned memories."""
        from cns.services.orchestrator import ContinuumOrchestrator

        pinned = [{"id": "pinned-1", "text": "Pinned"}]

        fresh = [
            {"id": "fresh-1", "text": "Fresh 1"},
            {"id": "fresh-2", "text": "Fresh 2"},
        ]

        merged = ContinuumOrchestrator._merge_memories(None, pinned, fresh)

        # Fresh IDs should be in positions after pinned
        assert merged[1]['id'] == "fresh-1"
        assert merged[2]['id'] == "fresh-2"

    def test_handles_empty_pinned_list(self):
        """CONTRACT: Empty pinned list returns only fresh memories."""
        from cns.services.orchestrator import ContinuumOrchestrator

        fresh = [
            {"id": "fresh-1", "text": "Fresh 1"},
            {"id": "fresh-2", "text": "Fresh 2"},
        ]

        merged = ContinuumOrchestrator._merge_memories(None, [], fresh)

        assert len(merged) == 2
        assert merged == fresh

    def test_handles_empty_fresh_list(self):
        """CONTRACT: Empty fresh list returns only pinned memories."""
        from cns.services.orchestrator import ContinuumOrchestrator

        pinned = [
            {"id": "pinned-1", "text": "Pinned 1"},
            {"id": "pinned-2", "text": "Pinned 2"},
        ]

        merged = ContinuumOrchestrator._merge_memories(None, pinned, [])

        assert len(merged) == 2
        assert merged == pinned

    def test_handles_both_lists_empty(self):
        """CONTRACT: Both lists empty returns empty list."""
        from cns.services.orchestrator import ContinuumOrchestrator

        merged = ContinuumOrchestrator._merge_memories(None, [], [])

        assert merged == []

    def test_fresh_memories_without_id_are_skipped(self):
        """CONTRACT: Fresh memories without ID are not added (require ID for dedup tracking)."""
        from cns.services.orchestrator import ContinuumOrchestrator

        pinned = [{"text": "Pinned no ID"}]  # Pinned always included

        fresh = [
            {"text": "Fresh no ID"},  # Skipped - no ID
            {"id": "fresh-1", "text": "Fresh with ID"},  # Added
        ]

        merged = ContinuumOrchestrator._merge_memories(None, pinned, fresh)

        # Only 2: pinned (always included) + fresh with ID
        assert len(merged) == 2
        assert merged[0]['text'] == "Pinned no ID"
        assert merged[1]['id'] == "fresh-1"

    def test_preserves_memory_contents(self):
        """CONTRACT: Merged memories retain all original fields unchanged."""
        from cns.services.orchestrator import ContinuumOrchestrator

        pinned = [{
            "id": "1",
            "text": "Memory",
            "importance_score": 0.9,
            "extra_field": "value"
        }]

        fresh = [{
            "id": "2",
            "text": "Fresh",
            "similarity_score": 0.85,
            "linked_memories": []
        }]

        merged = ContinuumOrchestrator._merge_memories(None, pinned, fresh)

        assert merged[0]['importance_score'] == 0.9
        assert merged[0]['extra_field'] == "value"
        assert merged[1]['similarity_score'] == 0.85
        assert merged[1]['linked_memories'] == []


class TestMergeOrderingWithDuplicates:
    """Tests ensuring deduplication preserves correct ordering."""

    def test_multiple_duplicates_all_resolved(self):
        """CONTRACT: Multiple duplicates between lists all resolved correctly."""
        from cns.services.orchestrator import ContinuumOrchestrator

        pinned = [
            {"id": "a", "text": "A"},
            {"id": "b", "text": "B"},
        ]

        fresh = [
            {"id": "a", "text": "A fresh"},  # duplicate
            {"id": "c", "text": "C"},
            {"id": "b", "text": "B fresh"},  # duplicate
            {"id": "d", "text": "D"},
        ]

        merged = ContinuumOrchestrator._merge_memories(None, pinned, fresh)

        # 4 total: 2 pinned + 2 unique fresh
        assert len(merged) == 4

        # Order: pinned first (a, b), then fresh unique (c, d)
        ids = [m['id'] for m in merged]
        assert ids == ["a", "b", "c", "d"]

        # Pinned versions kept
        assert merged[0]['text'] == "A"  # pinned, not "A fresh"
        assert merged[1]['text'] == "B"  # pinned, not "B fresh"
