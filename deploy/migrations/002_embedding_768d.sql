-- Migration: 384d -> 768d embeddings
-- Model: mdbr-leaf-ir-asym
--
-- REQUIRES DOWNTIME:
-- 1. Stop the application
-- 2. Run this migration
-- 3. Run the re-embedding script (scripts/migrate_embeddings_768.py)
-- 4. Restart the application

BEGIN;

-- =============================================================================
-- MEMORIES TABLE
-- =============================================================================

-- Drop existing index (dimension-specific)
DROP INDEX IF EXISTS idx_memories_embedding_ivfflat;

-- Clear existing 384d embeddings (required before dimension change)
UPDATE memories SET embedding = NULL WHERE embedding IS NOT NULL;

-- Change embedding column dimension
ALTER TABLE memories
    ALTER COLUMN embedding TYPE vector(768);

-- Index will be created AFTER re-embedding is complete
-- Run manually: CREATE INDEX idx_memories_embedding_ivfflat
--               ON memories USING ivfflat (embedding vector_cosine_ops)
--               WITH (lists = 100);

-- =============================================================================
-- MESSAGES TABLE (segment embeddings)
-- =============================================================================

-- Clear existing 384d segment embeddings (required before dimension change)
UPDATE messages SET segment_embedding = NULL WHERE segment_embedding IS NOT NULL;

-- Change segment_embedding column dimension
ALTER TABLE messages
    ALTER COLUMN segment_embedding TYPE vector(768);

-- =============================================================================
-- UPDATE COMMENTS
-- =============================================================================

COMMENT ON COLUMN memories.embedding IS 'mdbr-leaf-ir-asym 768d embedding for semantic similarity search';
COMMENT ON COLUMN messages.segment_embedding IS 'mdbr-leaf-ir-asym 768d embedding for segment search';

COMMIT;
