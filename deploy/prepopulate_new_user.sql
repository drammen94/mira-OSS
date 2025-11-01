-- Prepopulate memories and continuum for a new user
-- Prerequisites: User and continuum must already exist
-- Usage: psql -U mira_admin -d mira_service -v user_id='UUID' -v user_email='email' -f prepopulate_new_user.sql
--
-- NOTE: Connect to mira_service database when running this script

BEGIN;

-- Verify continuum exists (will fail if not)
DO $$
DECLARE
    conv_id uuid;
BEGIN
    SELECT id INTO conv_id FROM continuums WHERE user_id = :'user_id'::uuid;
    IF conv_id IS NULL THEN
        RAISE EXCEPTION 'Continuum does not exist for user %. Run this script after continuum creation.', :'user_id';
    END IF;
END $$;

-- Insert initial messages
WITH conv AS (
    SELECT id FROM continuums WHERE user_id = :'user_id'::uuid
)
INSERT INTO messages (continuum_id, user_id, role, content, metadata)
SELECT
    conv.id,
    :'user_id'::uuid,
    role,
    content,
    metadata
FROM conv, (VALUES
    ('user', 'I''m excited to start using MIRA. I''d love for you to get to know me better.', '{"system_generated": true}'::jsonb),
    ('assistant', 'I''m excited to get to know you! Understanding your needs and preferences helps me be more helpful. Feel free to share anything about yourself - your interests, goals, or what brought you here.', '{"system_generated": true}'::jsonb),
    ('user', 'Hello!', '{}'::jsonb),
    ('assistant', 'Hi, I''m MIRA. Tell me about yourself! What are you most interested to try out with MIRA?', '{}'::jsonb)
) AS initial_messages(role, content, metadata);

-- Insert memories (embeddings will be generated on first access)
INSERT INTO memories (user_id, text, importance_score, confidence, is_refined, last_refined_at)
VALUES
    (:'user_id'::uuid, 'I am MIRA, your AI assistant. Welcome! I''m here to help you with various tasks and maintain context across our conversations.', 0.9, 1.0, true, NOW()),
    (:'user_id'::uuid, 'Your email address is ' || :'user_email' || '. I''ll use this for authentication and important notifications.', 0.8, 1.0, true, NOW()),
    (:'user_id'::uuid, 'I maintain both working memory (for current conversations) and long-term memory (for important information across sessions). This helps me provide more contextual and personalized assistance.', 0.7, 1.0, true, NOW()),
    (:'user_id'::uuid, 'I can help you with various tasks including: scheduling reminders, managing information, writing code, answering questions, and maintaining context about your preferences and ongoing projects.', 0.6, 1.0, true, NOW()),
    (:'user_id'::uuid, 'Your data is private and isolated. I maintain separate memory spaces for each user, ensuring your information is never accessible to others.', 0.7, 1.0, true, NOW());

COMMIT;

-- Example usage:
-- psql -U mira_admin -d mira_service -v user_id='550e8400-e29b-41d4-a716-446655440000' -v user_email='user@example.com' -f prepopulate_new_user.sql