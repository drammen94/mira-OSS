-- Migration: Add multi-provider support to account_tiers
-- Enables tiers to route to different LLM providers (Anthropic, Groq, OpenRouter, etc.)

-- Add provider routing columns
ALTER TABLE account_tiers
ADD COLUMN IF NOT EXISTS provider VARCHAR(20) NOT NULL DEFAULT 'anthropic',
ADD COLUMN IF NOT EXISTS endpoint_url TEXT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS api_key_name VARCHAR(50) DEFAULT NULL;

-- Add check constraint for provider values
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'account_tiers_provider_check'
    ) THEN
        ALTER TABLE account_tiers
        ADD CONSTRAINT account_tiers_provider_check
        CHECK (provider IN ('anthropic', 'generic'));
    END IF;
END $$;

-- Restructure tiers: fast and balanced now use Groq
-- fast: Qwen3-32b via Groq
UPDATE account_tiers SET
    model = 'qwen/qwen3-32b',
    thinking_budget = 0,
    description = 'Qwen3 32B via Groq',
    provider = 'generic',
    endpoint_url = 'https://api.groq.com/openai/v1/chat/completions',
    api_key_name = 'provider_key'
WHERE name = 'fast';

-- balanced: Kimi K2 via Groq
UPDATE account_tiers SET
    model = 'moonshotai/kimi-k2-instruct-0905',
    thinking_budget = 0,
    description = 'Kimi K2 via Groq',
    provider = 'generic',
    endpoint_url = 'https://api.groq.com/openai/v1/chat/completions',
    api_key_name = 'provider_key'
WHERE name = 'balanced';

-- nuanced: stays Anthropic Opus (already has correct provider default)
UPDATE account_tiers SET
    description = 'Opus with nuanced reasoning'
WHERE name = 'nuanced';
