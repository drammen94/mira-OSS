-- Migration: Add Activity Day Tracking for Vacation-Proof Scoring
--
-- This migration switches from calendar-based decay to activity-based decay.
-- Memories now track user activity days (days with messages) instead of calendar days.
-- This prevents incorrect degradation during user vacations or inactive periods.
--
-- Changes:
-- 1. Add cumulative activity day tracking to users table (mira_service)
-- 2. Add activity day snapshots to memories table (mira_memory)
-- 3. Create granular activity tracking table (mira_service)

-- ============================================================================
-- MIRA_SERVICE DATABASE: User Activity Tracking
-- ============================================================================

\c mira_service;

-- Add cumulative activity days to users table
ALTER TABLE users
ADD COLUMN cumulative_activity_days INT DEFAULT 0,
ADD COLUMN last_activity_date DATE;

COMMENT ON COLUMN users.cumulative_activity_days IS 'Total number of days user has sent at least one message (activity-based time metric)';
COMMENT ON COLUMN users.last_activity_date IS 'Last date user sent a message (prevents double-counting same day)';

-- Create granular activity tracking table for historical analysis
CREATE TABLE IF NOT EXISTS user_activity_days (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    activity_date DATE NOT NULL,
    first_message_at TIMESTAMPTZ NOT NULL,
    message_count INT DEFAULT 1,
    PRIMARY KEY (user_id, activity_date)
);

CREATE INDEX idx_user_activity_days_lookup
ON user_activity_days(user_id, activity_date);

COMMENT ON TABLE user_activity_days IS 'Granular per-day activity tracking for users (one row per active day)';
COMMENT ON COLUMN user_activity_days.first_message_at IS 'Timestamp of first message on this day';
COMMENT ON COLUMN user_activity_days.message_count IS 'Number of messages sent by user on this day';

-- ============================================================================
-- MIRA_MEMORY DATABASE: Activity Day Snapshots on Memories
-- ============================================================================

\c mira_memory;

-- Add activity day snapshots to memories table
ALTER TABLE memories
ADD COLUMN activity_days_at_creation INT,
ADD COLUMN activity_days_at_last_access INT;

COMMENT ON COLUMN memories.activity_days_at_creation IS 'User cumulative_activity_days when memory was created (snapshot for decay calculation)';
COMMENT ON COLUMN memories.activity_days_at_last_access IS 'User cumulative_activity_days when memory was last accessed (snapshot for recency calculation)';

-- ============================================================================
-- VERIFICATION QUERIES (Run manually to verify migration)
-- ============================================================================

-- Verify users table columns
-- \c mira_service;
-- SELECT column_name, data_type, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'users' AND column_name IN ('cumulative_activity_days', 'last_activity_date');

-- Verify user_activity_days table
-- SELECT table_name FROM information_schema.tables WHERE table_name = 'user_activity_days';

-- Verify memories table columns
-- \c mira_memory;
-- SELECT column_name, data_type
-- FROM information_schema.columns
-- WHERE table_name = 'memories' AND column_name IN ('activity_days_at_creation', 'activity_days_at_last_access');
