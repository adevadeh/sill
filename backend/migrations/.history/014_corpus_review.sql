-- Migration 014: Corpus Review Support (Stream C)
--
-- Adds tracking for systematic corpus review ("Marie Kondo" the 30k memories)
--
-- Why: Need to track which memories have been reviewed, and find candidates
-- for archiving based on access/reuse patterns.

-- Step 1: Add reviewed_at for tracking review progress
ALTER TABLE memories
ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ DEFAULT NULL;

-- Step 2: Create index for finding unreviewed memories
CREATE INDEX IF NOT EXISTS idx_memories_reviewed
ON memories(reviewed_at) WHERE reviewed_at IS NULL AND status = 'active';

-- Step 3: Function to find review candidates
-- High access but low reuse = retrieved but never referenced (worth reviewing)
CREATE OR REPLACE FUNCTION find_review_candidates(
    p_limit INT DEFAULT 20,
    p_min_access INT DEFAULT 3
)
RETURNS TABLE(
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    access_count INT,
    reuse_count INT,
    reuse_ratio FLOAT,
    created_at TIMESTAMPTZ
) LANGUAGE sql AS $$
    SELECT
        m.id,
        m.content,
        m.type,
        m.access_count,
        m.reuse_count,
        CASE WHEN m.access_count > 0
             THEN m.reuse_count::float / m.access_count
             ELSE 0 END as reuse_ratio,
        m.created_at
    FROM memories m
    WHERE m.status = 'active'
      AND m.reviewed_at IS NULL
      AND m.access_count >= p_min_access
    ORDER BY
        -- Prioritize: high access, low reuse (accessed but not valuable)
        CASE WHEN m.access_count > 0
             THEN m.reuse_count::float / m.access_count
             ELSE 0 END ASC,
        m.access_count DESC
    LIMIT p_limit;
$$;

-- Step 4: Function to find stale memories (never accessed, old)
CREATE OR REPLACE FUNCTION find_stale_memories(
    p_limit INT DEFAULT 20,
    p_days_old INT DEFAULT 14
)
RETURNS TABLE(
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    importance FLOAT,
    created_at TIMESTAMPTZ,
    days_old INT
) LANGUAGE sql AS $$
    SELECT
        m.id,
        m.content,
        m.type,
        m.importance,
        m.created_at,
        EXTRACT(DAY FROM NOW() - m.created_at)::int as days_old
    FROM memories m
    WHERE m.status = 'active'
      AND m.reviewed_at IS NULL
      AND m.access_count = 0
      AND m.created_at < NOW() - (p_days_old || ' days')::interval
    ORDER BY m.created_at ASC
    LIMIT p_limit;
$$;

-- Step 5: Function to find duplicate/similar content
-- Uses trigram similarity on content
CREATE OR REPLACE FUNCTION find_similar_memories(
    p_memory_id UUID,
    p_threshold FLOAT DEFAULT 0.4
)
RETURNS TABLE(
    memory_id UUID,
    content TEXT,
    similarity FLOAT,
    created_at TIMESTAMPTZ
) LANGUAGE sql AS $$
    SELECT
        m.id,
        m.content,
        similarity(m.content, (SELECT content FROM memories WHERE id = p_memory_id)) as sim,
        m.created_at
    FROM memories m
    WHERE m.id != p_memory_id
      AND m.status = 'active'
      AND similarity(m.content, (SELECT content FROM memories WHERE id = p_memory_id)) > p_threshold
    ORDER BY sim DESC
    LIMIT 10;
$$;

-- Step 6: Function to mark memory as reviewed
CREATE OR REPLACE FUNCTION mark_reviewed(p_memory_id UUID)
RETURNS VOID LANGUAGE sql AS $$
    UPDATE memories
    SET reviewed_at = NOW()
    WHERE id = p_memory_id;
$$;

-- Step 7: Function to get review progress
CREATE OR REPLACE FUNCTION get_review_progress()
RETURNS TABLE(
    total_active INT,
    reviewed INT,
    unreviewed INT,
    progress_pct FLOAT
) LANGUAGE sql AS $$
    SELECT
        COUNT(*)::int as total_active,
        COUNT(*) FILTER (WHERE reviewed_at IS NOT NULL)::int as reviewed,
        COUNT(*) FILTER (WHERE reviewed_at IS NULL)::int as unreviewed,
        ROUND(100.0 * COUNT(*) FILTER (WHERE reviewed_at IS NOT NULL) / NULLIF(COUNT(*), 0), 2) as progress_pct
    FROM memories
    WHERE status = 'active';
$$;

-- Verify the migration
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memories' AND column_name = 'reviewed_at'
    ) THEN
        RAISE EXCEPTION 'reviewed_at column not created';
    END IF;

    RAISE NOTICE 'Migration 014 complete: Corpus review support enabled';
END $$;

-- =============================================================================
-- ACTUAL IMPLEMENTATION (2026-01-28)
-- =============================================================================
-- Instead of using reviewed_at column, corpus review used source_attribution jsonb:
--
-- UPDATE memories
-- SET source_attribution = COALESCE(source_attribution, '{}'::jsonb)
--     || '{"reviewed": "operational"}'::jsonb
-- WHERE status = 'active' AND archived_at IS NULL
--   AND (source_attribution IS NULL OR source_attribution->>'verified' IS NULL);
--
-- Result: 894 memories marked as reviewed: "operational"
--
-- This distinguishes three categories:
-- 1. reviewed = "operational" (894) - passed corpus review 2026-01-28
-- 2. No reviewed key but has trust (606) - from library ingestion
-- 3. No reviewed key, no trust (future) - unreviewed
--
-- The reviewed_at column above was not used; the jsonb approach was simpler.
-- =============================================================================
