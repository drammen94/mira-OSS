-- Migration: Add max_tier column to users table
-- Purpose: Per-account restriction of which LLM tiers a user can access
-- Default 'balanced' means users can access 'fast' and 'balanced' but not 'nuanced'
-- Admin can grant higher access via: UPDATE users SET max_tier = 'nuanced' WHERE email = '...'

ALTER TABLE users
ADD COLUMN max_tier VARCHAR(20) NOT NULL DEFAULT 'nuanced'
REFERENCES account_tiers(name);

COMMENT ON COLUMN users.max_tier IS 'Maximum LLM tier this user can access (hierarchical: fast < balanced < nuanced)';
