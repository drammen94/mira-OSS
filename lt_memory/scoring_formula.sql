-- ============================================================================
-- LT_MEMORY IMPORTANCE SCORING FORMULA
-- ============================================================================
-- Single source of truth for memory importance calculation.
-- Uses activity-based decay to prevent vacation-induced degradation.
--
-- FORMULA STRUCTURE:
-- 1. Expiration check: expires_at > 5 days past → score = 0.0
-- 1b. Expiration trailoff: 5-day linear decay AFTER expiration (1.0 → 0.0)
-- 2. Activity deltas: current_activity_days - activity_days_at_[creation|last_access]
-- 3. Momentum decay: access_count * 0.95^(activity_days_since_last_access)
-- 4. Access rate: effective_access_count / MAX(7, activity_days_since_creation)
-- 5. Value score: LN(1 + access_rate / 0.02) * 0.8
-- 6. Hub score: f(inbound_links) with diminishing returns after 10 links
-- 7. Entity hub score: f(entity_links × entity.link_count × type_weight) with diminishing returns
-- 8. Raw score: value_score + hub_score + entity_hub_score
-- 9. Recency boost: 1.0 / (1.0 + activity_days_since_last_access * 0.03)
-- 10. Temporal multiplier: happens_at proximity boost (calendar-based)
-- 11. Sigmoid transform: 1.0 / (1.0 + EXP(-(raw_score * recency * temporal - 2.0)))
--
-- CONSTANTS:
-- - BASELINE_ACCESS_RATE = 0.02 (1 access per 50 activity days)
-- - MOMENTUM_DECAY_RATE = 0.95 (5%% fade per activity day)
-- - MIN_AGE_DAYS = 7 (prevents spikes for new memories)
-- - SIGMOID_CENTER = 2.0 (maps average memories to ~0.5 importance)
-- - EXPIRATION_TRAILOFF_DAYS = 5 (grace period after expires_at)
-- - ENTITY_LINEAR_THRESHOLD = 50 (weighted entity links before diminishing returns)
-- - ENTITY_TYPE_WEIGHTS: PERSON=1.0, EVENT=0.9, ORG=0.8, PRODUCT=0.7, etc.
--
-- ACTIVITY DAYS vs CALENDAR DAYS:
-- - Decay calculations use ACTIVITY DAYS (user engagement days) to prevent
--   incorrect degradation during vacations
-- - Temporal events (happens_at, expires_at) use CALENDAR DAYS since
--   real-world deadlines don't pause
--
-- USAGE:
-- This formula expects two aliases:
-- - m: memories table
-- - u: users table
-- And requires memories.user_id = u.id join condition
-- ============================================================================

ROUND(CAST(
    CASE
        -- Hard zero if expired more than 5 days ago (calendar-based)
        WHEN m.expires_at IS NOT NULL
             AND EXTRACT(EPOCH FROM (NOW() - m.expires_at)) / 86400 > 5 THEN 0.0
        ELSE
            -- "Earning Your Keep" scoring with activity-based decay
            1.0 / (1.0 + EXP(-(
                -- Raw score calculation
                (
                    -- VALUE SCORE: access rate vs baseline with momentum decay
                    LN(1 + (
                        -- Effective access count with momentum decay (5%% per activity day)
                        (m.access_count * POWER(0.95,
                            GREATEST(0, u.cumulative_activity_days - COALESCE(m.activity_days_at_last_access, m.activity_days_at_creation, 0))
                        )) /
                        -- Access rate: normalize by age in activity days
                        GREATEST(7, u.cumulative_activity_days - COALESCE(m.activity_days_at_creation, 0))
                    ) / 0.02) * 0.8 +

                    -- HUB SCORE: diminishing returns after 10 links
                    (
                        CASE
                            WHEN jsonb_array_length(COALESCE(m.inbound_links, '[]'::jsonb)) = 0 THEN 0.0
                            WHEN jsonb_array_length(COALESCE(m.inbound_links, '[]'::jsonb)) <= 10 THEN
                                jsonb_array_length(COALESCE(m.inbound_links, '[]'::jsonb)) * 0.04
                            ELSE
                                0.4 + (jsonb_array_length(COALESCE(m.inbound_links, '[]'::jsonb)) - 10) * 0.02
                                    / (1 + (jsonb_array_length(COALESCE(m.inbound_links, '[]'::jsonb)) - 10) * 0.05)
                        END
                    ) +

                    -- ENTITY HUB SCORE: entity link value with type weighting and diminishing returns
                    -- Memories linked to important/frequently-referenced entities score higher
                    (
                        CASE
                            WHEN jsonb_array_length(COALESCE(m.entity_links, '[]'::jsonb)) = 0 THEN 0.0
                            ELSE
                                -- Calculate weighted entity links via subquery
                                COALESCE((
                                    SELECT
                                        CASE
                                            WHEN SUM(entity_weight) <= 0 THEN 0.0
                                            -- Linear scaling up to 50 weighted links
                                            WHEN SUM(entity_weight) <= 50 THEN SUM(entity_weight) * 0.005
                                            -- Diminishing returns above 50
                                            ELSE 0.25 + LN(SUM(entity_weight) / 50) * 0.075
                                        END
                                    FROM (
                                        SELECT
                                            e.link_count * CASE e.entity_type
                                                WHEN 'PERSON' THEN 1.0
                                                WHEN 'EVENT' THEN 0.9
                                                WHEN 'ORG' THEN 0.8
                                                WHEN 'PRODUCT' THEN 0.7
                                                WHEN 'WORK_OF_ART' THEN 0.6
                                                WHEN 'GPE' THEN 0.5
                                                WHEN 'NORP' THEN 0.5
                                                WHEN 'LAW' THEN 0.5
                                                WHEN 'FAC' THEN 0.4
                                                WHEN 'LANGUAGE' THEN 0.3
                                                ELSE 0.5
                                            END as entity_weight
                                        FROM jsonb_array_elements(m.entity_links) AS el
                                        JOIN entities e ON (el->>'uuid')::uuid = e.id
                                    ) entity_weights
                                ), 0.0)
                        END
                    )
                ) *

                -- RECENCY BOOST: smooth transition to cold storage (activity-based)
                (1.0 / (1.0 + GREATEST(0, u.cumulative_activity_days - COALESCE(m.activity_days_at_last_access, m.activity_days_at_creation, 0)) * 0.03)) *

                -- TEMPORAL MULTIPLIER: happens_at proximity boost (calendar-based)
                CASE
                    WHEN m.happens_at IS NOT NULL THEN
                        CASE
                            -- Event has passed: 14-day gradual decay (0.8 → 0.1)
                            WHEN m.happens_at < NOW() THEN
                                CASE
                                    WHEN EXTRACT(EPOCH FROM (NOW() - m.happens_at)) / 86400 <= 14 THEN
                                        0.8 * (1.0 - (EXTRACT(EPOCH FROM (NOW() - m.happens_at)) / 86400) / 14.0) + 0.1
                                    ELSE 0.1
                                END
                            -- Event upcoming: boost based on proximity
                            WHEN EXTRACT(EPOCH FROM (m.happens_at - NOW())) / 86400 <= 1 THEN 2.0
                            WHEN EXTRACT(EPOCH FROM (m.happens_at - NOW())) / 86400 <= 7 THEN 1.5
                            WHEN EXTRACT(EPOCH FROM (m.happens_at - NOW())) / 86400 <= 14 THEN 1.2
                            ELSE 1.0
                        END
                    ELSE 1.0
                END *

                -- EXPIRATION TRAILOFF: 5-day crash-out after expires_at (calendar-based)
                CASE
                    WHEN m.expires_at IS NOT NULL AND m.expires_at < NOW() THEN
                        -- Linear decay from 1.0 to 0.0 over 5 days post-expiration
                        GREATEST(0.0, 1.0 - (EXTRACT(EPOCH FROM (NOW() - m.expires_at)) / 86400) / 5.0)
                    ELSE 1.0
                END

                -- Sigmoid center shift (maps average memories to ~0.5 score)
                - 2.0
            )))
    END
AS NUMERIC), 3)
