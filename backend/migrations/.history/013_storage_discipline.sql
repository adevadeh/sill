-- Migration 013: Storage Discipline (Stream B)
--
-- Adds premise tracking and supersession for belief evolution
--
-- Why: Know WHY I concluded something. If a premise is invalidated,
-- know what to rework. Track belief evolution explicitly.

-- Step 1: Add derived_from column for premise tracking
-- Stores array of premise memory IDs and reasoning
-- Example: {"premises": ["uuid1", "uuid2"], "reason": "synthesis of X and Y"}
ALTER TABLE memories
ADD COLUMN IF NOT EXISTS derived_from JSONB DEFAULT NULL;

-- Step 2: Add supersedes_id for explicit belief evolution
-- When a memory supersedes another, link them
ALTER TABLE memories
ADD COLUMN IF NOT EXISTS supersedes_id UUID REFERENCES memories(id);

-- Step 3: Create index on supersedes_id for finding chains
CREATE INDEX IF NOT EXISTS idx_memories_supersedes
ON memories(supersedes_id) WHERE supersedes_id IS NOT NULL;

-- Step 4: Create index for querying by premise
-- Find all conclusions that depend on a given memory
CREATE INDEX IF NOT EXISTS idx_memories_derived_from
ON memories USING gin(derived_from jsonb_path_ops);

-- Step 5: Create function to find memories derived from a premise
CREATE OR REPLACE FUNCTION find_derived_from(p_premise_id UUID)
RETURNS TABLE(
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    derived_from JSONB,
    created_at TIMESTAMPTZ
) LANGUAGE sql AS $$
    SELECT
        m.id,
        m.content,
        m.type,
        m.derived_from,
        m.created_at
    FROM memories m
    WHERE m.status = 'active'
      AND m.derived_from->'premises' ? p_premise_id::text
    ORDER BY m.created_at DESC;
$$;

-- Step 6: Create function to find supersession chain
CREATE OR REPLACE FUNCTION find_supersession_chain(p_memory_id UUID)
RETURNS TABLE(
    memory_id UUID,
    content TEXT,
    supersedes_id UUID,
    created_at TIMESTAMPTZ,
    depth INT
) LANGUAGE sql AS $$
    WITH RECURSIVE chain AS (
        -- Start with the given memory
        SELECT
            m.id,
            m.content,
            m.supersedes_id,
            m.created_at,
            0 as depth
        FROM memories m
        WHERE m.id = p_memory_id

        UNION ALL

        -- Follow supersedes links backward (what did this supersede?)
        SELECT
            m.id,
            m.content,
            m.supersedes_id,
            m.created_at,
            c.depth + 1
        FROM memories m
        JOIN chain c ON m.id = c.supersedes_id
        WHERE c.depth < 10  -- Limit depth
    )
    SELECT * FROM chain ORDER BY depth;
$$;

-- Step 7: Create function to supersede a memory
-- Archives the old memory and links it
CREATE OR REPLACE FUNCTION supersede_memory(
    p_old_id UUID,
    p_new_content TEXT,
    p_reason TEXT DEFAULT NULL
) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE
    old_mem RECORD;
    new_id UUID;
BEGIN
    -- Get the old memory
    SELECT * INTO old_mem FROM memories WHERE id = p_old_id AND status = 'active';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Memory % not found or already archived', p_old_id;
    END IF;

    -- Create the new memory with same type and importance
    INSERT INTO memories (
        content,
        type,
        importance,
        supersedes_id,
        derived_from,
        embedding
    ) VALUES (
        p_new_content,
        old_mem.type,
        old_mem.importance,
        p_old_id,
        jsonb_build_object(
            'premises', jsonb_build_array(p_old_id::text),
            'reason', COALESCE(p_reason, 'supersession')
        ),
        get_embedding(p_new_content)
    ) RETURNING id INTO new_id;

    -- Archive the old memory
    UPDATE memories
    SET status = 'archived',
        archived_at = NOW(),
        archive_reason = 'superseded by ' || new_id::text
    WHERE id = p_old_id;

    RETURN new_id;
END;
$$;

-- Verify the migration
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memories' AND column_name = 'derived_from'
    ) THEN
        RAISE EXCEPTION 'derived_from column not created';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memories' AND column_name = 'supersedes_id'
    ) THEN
        RAISE EXCEPTION 'supersedes_id column not created';
    END IF;

    RAISE NOTICE 'Migration 013 complete: Storage discipline enabled';
END $$;
