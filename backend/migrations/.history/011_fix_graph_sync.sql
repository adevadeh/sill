-- Migration 011: Fix graph sync for memory relationships
-- Problem: discover_relationship() calls create_memory_relationship() which fails
-- silently when MemoryNodes don't exist in the graph. This caused find_contradictions
-- and other graph functions to return empty results.
--
-- Fix:
-- 1. Backfill all MemoryNodes from existing memories
-- 2. Sync all existing relationships from relationship_discoveries to graph
-- 3. Update create_memory_relationship to MERGE nodes instead of MATCH

-- ============================================================================
-- 1. BACKFILL: Ensure all active memories have MemoryNodes in the graph
-- ============================================================================

DO $$
DECLARE
    rec RECORD;
    batch_count INT := 0;
BEGIN
    FOR rec IN
        SELECT id, type, created_at
        FROM memories
        WHERE status = 'active'
        ORDER BY created_at
    LOOP
        BEGIN
            EXECUTE format(
                'SELECT * FROM cypher(''memory_graph'', $q$
                    MERGE (n:MemoryNode {memory_id: %L})
                    SET n.type = %L, n.created_at = %L
                    RETURN n
                $q$) as (result agtype)',
                rec.id,
                rec.type,
                rec.created_at
            );
            batch_count := batch_count + 1;
        EXCEPTION
            WHEN OTHERS THEN
                NULL; -- Skip failures silently
        END;
    END LOOP;
    RAISE NOTICE 'Backfilled % MemoryNodes', batch_count;
END;
$$;

-- ============================================================================
-- 2. SYNC: Sync all existing relationships to the graph
-- ============================================================================

DO $$
DECLARE
    rec RECORD;
    synced INT := 0;
BEGIN
    FOR rec IN
        SELECT from_id, to_id, relationship_type, confidence
        FROM relationship_discoveries
        WHERE is_valid = true
    LOOP
        BEGIN
            EXECUTE format(
                'SELECT * FROM cypher(''memory_graph'', $q$
                    MATCH (a:MemoryNode {memory_id: %L}),
                          (b:MemoryNode {memory_id: %L})
                    MERGE (a)-[r:%s]->(b)
                    SET r.confidence = %s
                    RETURN r
                $q$) as (result agtype)',
                rec.from_id,
                rec.to_id,
                rec.relationship_type,
                COALESCE(rec.confidence, 0.8)
            );
            synced := synced + 1;
        EXCEPTION
            WHEN OTHERS THEN
                RAISE NOTICE 'Failed syncing % -> %: %', rec.from_id, rec.to_id, SQLERRM;
        END;
    END LOOP;
    RAISE NOTICE 'Synced % relationships to graph', synced;
END;
$$;

-- ============================================================================
-- 3. FIX: Update create_memory_relationship to MERGE nodes if missing
-- ============================================================================

CREATE OR REPLACE FUNCTION create_memory_relationship(
    p_from_id UUID,
    p_to_id UUID,
    p_relationship_type graph_edge_type,
    p_properties JSONB DEFAULT '{}'
) RETURNS VOID AS $$
BEGIN
    -- Use MERGE for nodes to ensure they exist, then CREATE the edge
    -- This prevents silent failures when nodes don't exist
    EXECUTE format(
        'SELECT * FROM cypher(''memory_graph'', $q$
            MERGE (a:MemoryNode {memory_id: %L})
            MERGE (b:MemoryNode {memory_id: %L})
            CREATE (a)-[r:%s %s]->(b)
            RETURN r
        $q$) as (result agtype)',
        p_from_id,
        p_to_id,
        p_relationship_type,
        CASE WHEN p_properties = '{}'::jsonb
             THEN ''
             ELSE format('{%s}',
                  (SELECT string_agg(format('%I: %s', key, value), ', ')
                   FROM jsonb_each(p_properties)))
        END
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION create_memory_relationship IS
'Creates a relationship edge between two memories in the graph. Uses MERGE for nodes to ensure they exist before creating the edge.';
