-- Add full-text search capabilities to memories table
-- This migration adds PostgreSQL text search features for hybrid BM25 + vector retrieval

-- Add text search vector column
ALTER TABLE memories ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- Add preprocessed search text column (for debugging and custom search logic)
ALTER TABLE memories ADD COLUMN IF NOT EXISTS search_text TEXT;

-- Create GIN index for fast text search
CREATE INDEX IF NOT EXISTS idx_memories_search_vector ON memories USING GIN(search_vector);

-- Populate search vectors from existing memories
UPDATE memories
SET search_vector = to_tsvector('english', text),
    search_text = text
WHERE search_vector IS NULL;

-- Create trigger function to maintain search vectors on insert/update
CREATE OR REPLACE FUNCTION update_memories_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', NEW.text);
    NEW.search_text := NEW.text;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

-- Drop trigger if exists (to make migration idempotent)
DROP TRIGGER IF EXISTS memories_search_vector_update ON memories;

-- Create trigger to maintain search vectors
CREATE TRIGGER memories_search_vector_update
BEFORE INSERT OR UPDATE OF text
ON memories
FOR EACH ROW
EXECUTE FUNCTION update_memories_search_vector();

-- Add comment explaining the columns
COMMENT ON COLUMN memories.search_vector IS 'Full-text search vector for BM25-style retrieval';
COMMENT ON COLUMN memories.search_text IS 'Preprocessed text for search operations';