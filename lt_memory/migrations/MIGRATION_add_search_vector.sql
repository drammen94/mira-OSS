-- Migration: Add full-text search vector to memories
-- Purpose: Enable BM25 text search for hybrid search (vector + BM25)
-- Author: Claude Code
-- Date: 2025-10-24

-- Add search_vector column for full-text search
ALTER TABLE memories ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- Create GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS idx_memories_search_vector ON memories USING gin (search_vector);

-- Create trigger function to automatically update search_vector
CREATE OR REPLACE FUNCTION update_memories_search_vector()
RETURNS TRIGGER AS $$
BEGIN
    -- Generate tsvector from text column using English configuration
    NEW.search_vector := to_tsvector('english', NEW.text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to update search_vector on INSERT/UPDATE
DROP TRIGGER IF EXISTS trigger_update_memories_search_vector ON memories;
CREATE TRIGGER trigger_update_memories_search_vector
    BEFORE INSERT OR UPDATE OF text ON memories
    FOR EACH ROW
    EXECUTE FUNCTION update_memories_search_vector();

-- Populate search_vector for existing records
UPDATE memories SET search_vector = to_tsvector('english', text) WHERE search_vector IS NULL;

-- Add comment for documentation
COMMENT ON COLUMN memories.search_vector IS 'Full-text search vector for BM25 ranking (auto-updated from text column)';
