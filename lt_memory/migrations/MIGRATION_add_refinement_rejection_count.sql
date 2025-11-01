-- Migration: Add refinement_rejection_count column to memories table
-- Purpose: Track do_nothing responses to avoid infinite refinement retry loops
-- After 3 rejections, memory is excluded from future refinement candidates

ALTER TABLE memories
ADD COLUMN IF NOT EXISTS refinement_rejection_count INTEGER DEFAULT 0;

-- Add comment for documentation
COMMENT ON COLUMN memories.refinement_rejection_count IS
'Number of times memory was marked do_nothing during refinement. After 3 rejections, excluded from future refinement.';
