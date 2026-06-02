-- Migration 017: Fix hybrid_recall returning zero rows for vector-only queries
--
-- Why: hybrid_recall returns 0 rows when the FTS side is empty (e.g. queries
-- with no English lexemes, short queries, or words that don't appear anywhere
-- in the corpus). The vector_ranked CTE alone returns 20 rows for the same
-- input when run inline, but inside the function the HNSW index path under
-- pgvector 0.7.0 + plpgsql + parameterized vector ORDER BY yields zero rows.
--
-- Adding the existing-anywhere `m.embedding <> zero_vec` predicate forces a
-- sequential scan + in-memory sort for the vector_ranked CTE, which sidesteps
-- the HNSW path entirely. fast_recall already uses this pattern; hybrid_recall
-- had drifted from migration 012 and lost the predicate (likely while fixing
-- the numeric/double-precision mismatch in the RRF score).
--
-- Diagnosis: 2026-05-01. Reproduced with:
--   SELECT COUNT(*) FROM hybrid_recall('zzzznotawordzz', 5, 60);  -- returns 0
--   SET LOCAL enable_indexscan = off;
--   SELECT COUNT(*) FROM hybrid_recall('zzzznotawordzz', 5, 60);  -- returns 5
-- Confirmed via pg_get_functiondef that the live function was missing the
-- zero_vec filter that migration 012 specified.

CREATE OR REPLACE FUNCTION hybrid_recall(
    p_query_text TEXT,
    p_limit INT DEFAULT 10,
    p_k INT DEFAULT 60
)
RETURNS TABLE(
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    score DOUBLE PRECISION,
    source TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    query_embedding vector;
    zero_vec vector;
    ts_query tsquery;
BEGIN
    query_embedding := public.get_embedding(p_query_text);
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
    ts_query := plainto_tsquery('english', p_query_text);

    RETURN QUERY
    WITH
    vector_ranked AS (
        SELECT
            m.id,
            m.content,
            m.type,
            ROW_NUMBER() OVER (ORDER BY m.embedding <=> query_embedding) AS rank
        FROM memories m
        WHERE m.status = 'active'
          AND m.embedding IS NOT NULL
          AND m.embedding <> zero_vec
        ORDER BY m.embedding <=> query_embedding
        LIMIT p_limit * 4
    ),
    fts_ranked AS (
        SELECT
            m.id,
            m.content,
            m.type,
            ROW_NUMBER() OVER (ORDER BY ts_rank(m.content_tsv, ts_query) DESC) AS rank
        FROM memories m
        WHERE m.status = 'active'
          AND m.content_tsv @@ ts_query
        ORDER BY ts_rank(m.content_tsv, ts_query) DESC
        LIMIT p_limit * 4
    ),
    combined AS (
        SELECT
            COALESCE(v.id, f.id) AS id,
            COALESCE(v.content, f.content) AS content,
            COALESCE(v.type, f.type) AS type,
            (COALESCE(1.0/(p_k + v.rank), 0) + COALESCE(1.0/(p_k + f.rank), 0))::double precision AS rrf_score,
            CASE
                WHEN v.id IS NOT NULL AND f.id IS NOT NULL THEN 'hybrid'
                WHEN v.id IS NOT NULL THEN 'vector'
                ELSE 'fts'
            END AS match_source
        FROM vector_ranked v
        FULL OUTER JOIN fts_ranked f ON v.id = f.id
    )
    SELECT
        c.id,
        c.content,
        c.type,
        c.rrf_score,
        c.match_source
    FROM combined c
    ORDER BY c.rrf_score DESC
    LIMIT p_limit;
END;
$$;
