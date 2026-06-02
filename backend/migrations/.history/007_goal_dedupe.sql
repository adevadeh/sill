-- Goal deduplication: normalized key + unique constraint + cleanup

-- 1. Add normalized dedupe_key column
ALTER TABLE goals ADD COLUMN IF NOT EXISTS dedupe_key TEXT;

-- Populate dedupe_key for existing goals
UPDATE goals SET dedupe_key = regexp_replace(
    regexp_replace(lower(trim(title)), '\s+', ' ', 'g'),
    '[^\w\s]', '', 'g'
) WHERE dedupe_key IS NULL;

-- Make it generated for new goals via trigger
CREATE OR REPLACE FUNCTION goals_dedupe_key_trigger() RETURNS TRIGGER AS $$
BEGIN
    NEW.dedupe_key := regexp_replace(
        regexp_replace(lower(trim(NEW.title)), '\s+', ' ', 'g'),
        '[^\w\s]', '', 'g'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS goals_set_dedupe_key ON goals;
CREATE TRIGGER goals_set_dedupe_key
    BEFORE INSERT OR UPDATE OF title ON goals
    FOR EACH ROW EXECUTE FUNCTION goals_dedupe_key_trigger();

-- 2. Cleanup: demote duplicate queued goals to backburner
-- Keep the one with: best description, most recent touch, earliest created
WITH ranked AS (
    SELECT
        id,
        dedupe_key,
        ROW_NUMBER() OVER (
            PARTITION BY dedupe_key
            ORDER BY
                (description IS NOT NULL AND length(trim(description)) > 0) DESC,
                length(coalesce(description, '')) DESC,
                last_touched DESC,
                created_at ASC
        ) as rn
    FROM goals
    WHERE priority = 'queued'
)
UPDATE goals g
SET priority = 'backburner',
    last_touched = CURRENT_TIMESTAMP
FROM ranked r
WHERE g.id = r.id
  AND r.rn > 1;

-- 3. Add partial unique index on open goals (after cleanup so it doesn't fail)
CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_unique_open
ON goals (dedupe_key)
WHERE priority IN ('active', 'queued', 'backburner');

-- 4. Function to auto-abandon stale backburner goals (for homeostasis)
CREATE OR REPLACE FUNCTION abandon_stale_backburner(max_age_days INT DEFAULT 14)
RETURNS INT AS $$
DECLARE
    abandoned_count INT;
BEGIN
    UPDATE goals
    SET priority = 'abandoned',
        abandoned_at = CURRENT_TIMESTAMP,
        abandonment_reason = 'Auto-abandoned: backburner untouched > ' || max_age_days || ' days',
        last_touched = CURRENT_TIMESTAMP
    WHERE priority = 'backburner'
      AND last_touched < CURRENT_TIMESTAMP - (max_age_days || ' days')::INTERVAL;

    GET DIAGNOSTICS abandoned_count = ROW_COUNT;
    RETURN abandoned_count;
END;
$$ LANGUAGE plpgsql;
