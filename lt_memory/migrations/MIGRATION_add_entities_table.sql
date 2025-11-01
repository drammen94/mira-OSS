-- Migration: Add entities table and entity_links to memories
-- Purpose: Transform entities from ephemeral extraction artifacts into persistent knowledge anchors
-- Author: Claude Code
-- Date: 2025-10-08

-- Create entities table
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,  -- PERSON, ORG, GPE, PRODUCT, EVENT, WORK_OF_ART, LAW, LANGUAGE, NORP, FAC
    embedding VECTOR(300),       -- spaCy en_core_web_lg word vector (300-dimensional)
    link_count INTEGER DEFAULT 0,
    last_linked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    is_archived BOOLEAN DEFAULT FALSE,
    archived_at TIMESTAMP,

    CONSTRAINT entities_user_name_type_unique UNIQUE (user_id, name, entity_type)
);

-- Indexes for entity operations
CREATE INDEX idx_entities_user_id ON entities(user_id);
CREATE INDEX idx_entities_link_count ON entities(user_id, link_count DESC);
CREATE INDEX idx_entities_last_linked ON entities(user_id, last_linked_at);
CREATE INDEX idx_entities_type ON entities(user_id, entity_type);

-- Vector similarity index (IVFFlat for approximate nearest neighbor search)
-- Note: Requires pgvector extension
CREATE INDEX idx_entities_embedding ON entities USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Add entity_links column to memories table
-- Stores JSONB array of entity references: [{"uuid": "...", "type": "PERSON", "name": "Taylor"}, ...]
ALTER TABLE memories ADD COLUMN IF NOT EXISTS entity_links JSONB DEFAULT '[]'::jsonb;

-- GIN index for efficient entity link queries
CREATE INDEX idx_memories_entity_links ON memories USING gin (entity_links);

-- Comments for documentation
COMMENT ON TABLE entities IS 'Persistent knowledge anchors (people, organizations, products, etc.) that memories link to';
COMMENT ON COLUMN entities.name IS 'Canonical normalized entity name';
COMMENT ON COLUMN entities.entity_type IS 'spaCy NER entity type (PERSON, ORG, GPE, PRODUCT, etc.)';
COMMENT ON COLUMN entities.embedding IS 'spaCy word vector for semantic similarity (300d from en_core_web_lg)';
COMMENT ON COLUMN entities.link_count IS 'Number of memories linking to this entity';
COMMENT ON COLUMN entities.last_linked_at IS 'Timestamp of most recent memory link (for dormancy detection)';
COMMENT ON COLUMN memories.entity_links IS 'JSONB array of entity references [{"uuid": "entity-id", "type": "PERSON", "name": "Taylor"}]';
