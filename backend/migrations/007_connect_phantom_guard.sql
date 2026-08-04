-- 007_connect_phantom_guard.sql
-- Ported from agi-memory migration 026 (2026-08-02); error text generalized.
--
-- The relationship path could mint phantom graph nodes: discover_relationship
-- swallowed graph errors, and the underlying MERGE created absent endpoints
-- into existence — so linking a fabricated or mistyped memory id silently
-- created a graph node with no memories row behind it. This guard makes
-- discover_relationship refuse ids with no memories row, for EVERY caller,
-- before any graph or log write. The graph-error swallow is kept (a graph
-- hiccup should not kill the discovery log) but can no longer mint phantoms
-- because the guard runs first. Link context stays in
-- relationship_discoveries.discovery_context, deliberately OFF the edge: the
-- edge property map is interpolated into cypher text, and free text there is
-- an injection surface.

BEGIN;

CREATE OR REPLACE FUNCTION public.discover_relationship(
    p_from_id uuid,
    p_to_id uuid,
    p_relationship_type graph_edge_type,
    p_confidence double precision DEFAULT 0.8,
    p_discovered_by text DEFAULT 'reflection'::text,
    p_heartbeat_id uuid DEFAULT NULL::uuid,
    p_discovery_context text DEFAULT NULL::text
)
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    missing uuid[];
BEGIN
    SELECT array_agg(candidate) INTO missing
    FROM unnest(ARRAY[p_from_id, p_to_id]) AS candidate
    WHERE NOT EXISTS (SELECT 1 FROM memories WHERE id = candidate);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            'connect refused: no memories row for % — phantom-node guard '
            '(migration 007). Resolve the full id from the memories table '
            'before linking; receipts can only be copied.',
            missing;
    END IF;

    BEGIN
        PERFORM create_memory_relationship(
            p_from_id,
            p_to_id,
            p_relationship_type,
            jsonb_build_object('confidence', p_confidence, 'by', p_discovered_by)
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;  -- graph hiccup must not kill the discovery log (pre-existing)
    END;

    INSERT INTO relationship_discoveries (
        from_id, to_id, relationship_type, confidence, discovered_by, discovery_context, heartbeat_id
    )
    VALUES (
        p_from_id, p_to_id, p_relationship_type, p_confidence, p_discovered_by, p_discovery_context, p_heartbeat_id
    );
END;
$function$;

COMMIT;
