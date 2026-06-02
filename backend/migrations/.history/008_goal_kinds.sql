-- Goal kinds: distinguish completable tasks from standing directives
-- Directives spawn tasks; tasks can be completed; directives are retired

-- 1. Add goal_kind enum
DO $$ BEGIN
    CREATE TYPE goal_kind AS ENUM ('task', 'directive');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- 2. Add columns
ALTER TABLE goals ADD COLUMN IF NOT EXISTS kind goal_kind DEFAULT 'task';
ALTER TABLE goals ADD COLUMN IF NOT EXISTS review_interval INTERVAL;
ALTER TABLE goals ADD COLUMN IF NOT EXISTS next_review_at TIMESTAMPTZ;
ALTER TABLE goals ADD COLUMN IF NOT EXISTS parent_goal_id UUID REFERENCES goals(id);

-- 3. Index for finding due reviews
CREATE INDEX IF NOT EXISTS idx_goals_next_review ON goals(next_review_at)
WHERE kind = 'directive' AND priority = 'active';

-- 4. Convert existing active goals to directives (they're all standing priorities)
UPDATE goals
SET kind = 'directive',
    review_interval = INTERVAL '7 days',
    next_review_at = CURRENT_TIMESTAMP + INTERVAL '7 days'
WHERE priority = 'active'
  AND kind = 'task';

-- 5. Function to get directives due for review
CREATE OR REPLACE FUNCTION get_due_directives()
RETURNS TABLE (
    id UUID,
    title TEXT,
    description TEXT,
    overdue_by INTERVAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        g.id,
        g.title,
        g.description,
        CURRENT_TIMESTAMP - g.next_review_at as overdue_by
    FROM goals g
    WHERE g.kind = 'directive'
      AND g.priority = 'active'
      AND g.next_review_at <= CURRENT_TIMESTAMP
    ORDER BY g.next_review_at ASC;
END;
$$ LANGUAGE plpgsql;

-- 6. Function to complete a directive review (bump next_review_at)
CREATE OR REPLACE FUNCTION complete_directive_review(directive_id UUID)
RETURNS TIMESTAMPTZ AS $$
DECLARE
    new_review_at TIMESTAMPTZ;
BEGIN
    UPDATE goals
    SET next_review_at = CURRENT_TIMESTAMP + review_interval,
        last_touched = CURRENT_TIMESTAMP
    WHERE id = directive_id
      AND kind = 'directive'
    RETURNING next_review_at INTO new_review_at;

    RETURN new_review_at;
END;
$$ LANGUAGE plpgsql;

-- 7. Function to spawn a task from a directive
CREATE OR REPLACE FUNCTION spawn_task_from_directive(
    directive_id UUID,
    task_title TEXT,
    task_description TEXT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    new_task_id UUID;
BEGIN
    INSERT INTO goals (title, description, priority, kind, parent_goal_id)
    VALUES (task_title, task_description, 'queued', 'task', directive_id)
    RETURNING id INTO new_task_id;

    -- Touch the parent directive
    UPDATE goals SET last_touched = CURRENT_TIMESTAMP WHERE id = directive_id;

    RETURN new_task_id;
END;
$$ LANGUAGE plpgsql;
