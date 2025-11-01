#!/bin/bash
# Add initial collapsed segment sentinels for all users

set -e

echo "Adding initial segment sentinels for all users..."

# Get all user IDs
USER_IDS=$(psql -U taylut -h localhost -d mira_service -t -c "SELECT id FROM users;")

for USER_ID in $USER_IDS; do
    USER_ID=$(echo $USER_ID | xargs)  # Trim whitespace

    echo ""
    echo "Processing user: $USER_ID"

    # Get user's continuum
    CONTINUUM_ID=$(psql -U taylut -h localhost -d mira_service -t -c "
        SELECT id FROM continuums
        WHERE user_id = '$USER_ID'
        ORDER BY created_at DESC
        LIMIT 1;
    " | xargs)

    if [ -z "$CONTINUUM_ID" ]; then
        echo "  No continuum found, skipping"
        continue
    fi

    echo "  Continuum: $CONTINUUM_ID"

    # Check if user already has segment boundaries
    BOUNDARY_COUNT=$(psql -U taylut -h localhost -d mira_service -t -c "
        SELECT COUNT(*) FROM messages
        WHERE continuum_id = '$CONTINUUM_ID'
            AND metadata->>'is_segment_boundary' = 'true'
            AND metadata->>'status' = 'collapsed';
    " | xargs)

    if [ "$BOUNDARY_COUNT" -gt "0" ]; then
        echo "  Already has $BOUNDARY_COUNT collapsed sentinels, skipping"
        continue
    fi

    # Generate segment ID
    SEGMENT_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')

    # Insert sentinel
    psql -U taylut -h localhost -d mira_service -c "
        INSERT INTO messages (id, continuum_id, user_id, role, content, metadata, created_at)
        VALUES (
            gen_random_uuid(),
            '$CONTINUUM_ID',
            '$USER_ID',
            'assistant',
            'This is the first sentinel',
            jsonb_build_object(
                'is_segment_boundary', true,
                'status', 'collapsed',
                'segment_id', '$SEGMENT_ID',
                'segment_start_time', NOW(),
                'segment_end_time', NOW(),
                'tools_used', '[]'::jsonb,
                'memories_extracted', false,
                'domain_blocks_updated', false,
                'collapsed_at', NOW(),
                'migration_sentinel', true,
                'summary_generated_at', NOW(),
                'processing_failed', false
            ),
            NOW()
        );
    " > /dev/null

    echo "  ✓ Created collapsed sentinel: $SEGMENT_ID"
done

echo ""
echo "✓ Migration complete!"
