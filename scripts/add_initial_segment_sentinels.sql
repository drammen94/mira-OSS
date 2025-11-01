-- Add initial segment boundary sentinels for existing users
-- Run with: psql -U mira_admin -h localhost -d mira_service -f scripts/add_initial_segment_sentinels.sql

BEGIN;

-- For each continuum that doesn't have segment boundaries,
-- create a collapsed segment sentinel at the first message position
INSERT INTO messages (id, continuum_id, user_id, role, content, metadata, created_at)
SELECT
    gen_random_uuid() as id,
    c.id as continuum_id,
    c.user_id,
    'assistant' as role,
    'Historical conversation segment (' ||
        COALESCE(
            (SELECT COUNT(*) FROM messages m2
             WHERE m2.continuum_id = c.id
             AND (m2.metadata->>'is_session_boundary' IS NULL OR m2.metadata->>'is_session_boundary' != 'true')
             AND (m2.metadata->>'is_summary' IS NULL OR m2.metadata->>'is_summary' != 'true')
            ), 0
        ) || ' messages)' as content,
    jsonb_build_object(
        'is_segment_boundary', true,
        'status', 'collapsed',
        'segment_id', gen_random_uuid()::text,
        'segment_start_time', first_msg.created_at,
        'segment_end_time', last_msg.created_at,
        'tools_used', '[]'::jsonb,
        'memories_extracted', false,
        'domain_blocks_updated', false,
        'collapsed_at', NOW(),
        'migration_sentinel', true,
        'summary_generated_at', NOW(),
        'processing_failed', false
    ) as metadata,
    last_msg.created_at as created_at
FROM continuums c
CROSS JOIN LATERAL (
    SELECT created_at
    FROM messages m
    WHERE m.continuum_id = c.id
        AND (m.metadata->>'is_session_boundary' IS NULL OR m.metadata->>'is_session_boundary' != 'true')
        AND (m.metadata->>'is_summary' IS NULL OR m.metadata->>'is_summary' != 'true')
    ORDER BY created_at ASC
    LIMIT 1
) first_msg
CROSS JOIN LATERAL (
    SELECT created_at
    FROM messages m
    WHERE m.continuum_id = c.id
        AND (m.metadata->>'is_session_boundary' IS NULL OR m.metadata->>'is_session_boundary' != 'true')
        AND (m.metadata->>'is_summary' IS NULL OR m.metadata->>'is_summary' != 'true')
    ORDER BY created_at DESC
    LIMIT 1
) last_msg
WHERE NOT EXISTS (
    SELECT 1 FROM messages m
    WHERE m.continuum_id = c.id
        AND m.metadata->>'is_segment_boundary' = 'true'
);

-- Show results
SELECT
    c.user_id,
    c.id as continuum_id,
    COUNT(m.id) as total_messages,
    COUNT(CASE WHEN m.metadata->>'is_segment_boundary' = 'true' THEN 1 END) as segment_boundaries
FROM continuums c
LEFT JOIN messages m ON m.continuum_id = c.id
GROUP BY c.user_id, c.id
ORDER BY c.created_at;

COMMIT;
