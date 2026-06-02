-- Migration 012: Add hybrid recall with FTS (Full-Text Search)
--
-- Stream A: Retrieval improvements
-- Adds keyword search alongside vector similarity using RRF (Reciprocal Rank Fusion)
--
-- Why: Short queries get bad embedding signal. "Hofstadter" as keyword works
-- better than semantic similarity for exact name matches.

-- Step 1: Add tsvector column (generated from content)
ALTER TABLE memories
ADD COLUMN IF NOT EXISTS content_tsv tsvector
GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

-- Step 2: Create GIN index on the tsvector column
CREATE INDEX IF NOT EXISTS idx_memories_content_fts
ON memories USING gin(content_tsv);

-- Step 3: Add reuse tracking columns (retrieved AND referenced in response)
ALTER TABLE memories ADD COLUMN IF NOT EXISTS reuse_count INT DEFAULT 0;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_reused TIMESTAMPTZ;

-- Step 4: Create function to touch memory on access
CREATE OR REPLACE FUNCTION touch_memory_access(p_memory_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE memories SET
        access_count = access_count + 1,
        last_accessed = NOW()
    WHERE id = p_memory_id;
END;
$$ LANGUAGE plpgsql;

-- Step 5: Create function to mark memory as reused (appeared in response)
CREATE OR REPLACE FUNCTION touch_memory_reuse(p_memory_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE memories SET
        reuse_count = reuse_count + 1,
        last_reused = NOW()
    WHERE id = p_memory_id;
END;
$$ LANGUAGE plpgsql;

-- Step 6: Create hybrid recall function using RRF
-- Combines vector similarity and FTS keyword matching
CREATE OR REPLACE FUNCTION hybrid_recall(
    p_query_text TEXT,
    p_limit INT DEFAULT 10,
    p_k INT DEFAULT 60  -- RRF constant, standard value
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
    -- Get embedding for vector search
    query_embedding := get_embedding(p_query_text);
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;

    -- Create tsquery for FTS (handles multi-word queries)
    ts_query := plainto_tsquery('english', p_query_text);

    RETURN QUERY
    WITH
    -- Vector search: ranked by cosine similarity
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
    -- FTS search: ranked by ts_rank
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
    -- Combine using RRF (Reciprocal Rank Fusion)
    combined AS (
        SELECT
            COALESCE(v.id, f.id) AS id,
            COALESCE(v.content, f.content) AS content,
            COALESCE(v.type, f.type) AS type,
            -- RRF score: 1/(k+rank_vector) + 1/(k+rank_fts)
            COALESCE(1.0/(p_k + v.rank), 0) + COALESCE(1.0/(p_k + f.rank), 0) AS rrf_score,
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

-- Step 7: Create enhanced fast_recall that includes FTS
-- This extends the existing fast_recall with an FTS signal
CREATE OR REPLACE FUNCTION fast_recall_hybrid(
    p_query_text TEXT,
    p_limit INT DEFAULT 10
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
    current_valence FLOAT;
    ts_query tsquery;
BEGIN
    query_embedding := get_embedding(p_query_text);
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
    ts_query := plainto_tsquery('english', p_query_text);

    BEGIN
        current_valence := NULLIF(get_current_affective_state()->>'valence', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            current_valence := NULL;
    END;
    current_valence := COALESCE(current_valence, 0.0);

    RETURN QUERY
    WITH
    -- Vector seeds (semantic similarity)
    seeds AS (
        SELECT
            m.id,
            m.content,
            m.type,
            m.importance,
            m.decay_rate,
            m.created_at,
            m.last_accessed,
            1 - (m.embedding <=> query_embedding) as sim
        FROM memories m
        WHERE m.status = 'active'
          AND m.embedding IS NOT NULL
          AND m.embedding <> zero_vec
        ORDER BY m.embedding <=> query_embedding
        LIMIT GREATEST(p_limit, 5)
    ),
    -- FTS matches (keyword search) - NEW!
    fts_matches AS (
        SELECT
            m.id,
            ts_rank(m.content_tsv, ts_query) AS fts_score
        FROM memories m
        WHERE m.status = 'active'
          AND m.content_tsv @@ ts_query
        ORDER BY ts_rank(m.content_tsv, ts_query) DESC
        LIMIT p_limit * 2
    ),
    -- Expand via precomputed neighborhoods
    associations AS (
        SELECT
            (key)::UUID as mem_id,
            MAX((value::float) * s.sim) as assoc_score
        FROM seeds s
        JOIN memory_neighborhoods mn ON s.id = mn.memory_id,
        jsonb_each_text(mn.neighbors)
        WHERE NOT mn.is_stale
        GROUP BY key
    ),
    -- Temporal context from episodes
    temporal AS (
        SELECT DISTINCT
            em.memory_id as mem_id,
            0.15 as temp_score
        FROM seeds s
        JOIN episode_memories em_seed ON s.id = em_seed.memory_id
        JOIN episode_memories em ON em_seed.episode_id = em.episode_id
        WHERE em.memory_id != s.id
        LIMIT 20
    ),
    -- Combine all candidates
    candidates AS (
        SELECT id as mem_id, sim as vector_score, NULL::float as fts_score, NULL::float as assoc_score, NULL::float as temp_score
        FROM seeds
        UNION
        SELECT id as mem_id, NULL, fts_score, NULL, NULL FROM fts_matches
        UNION
        SELECT mem_id, NULL, NULL, assoc_score, NULL FROM associations
        UNION
        SELECT mem_id, NULL, NULL, NULL, temp_score FROM temporal
    ),
    -- Aggregate scores per memory
    scored AS (
        SELECT
            c.mem_id,
            MAX(c.vector_score) as vector_score,
            MAX(c.fts_score) as fts_score,
            MAX(c.assoc_score) as assoc_score,
            MAX(c.temp_score) as temp_score
        FROM candidates c
        GROUP BY c.mem_id
    )
    SELECT
        m.id,
        m.content,
        m.type,
        GREATEST(
            -- Vector similarity: 40% (reduced from 50%)
            COALESCE(sc.vector_score, 0) * 0.40 +
            -- FTS keyword match: 15% (NEW!)
            COALESCE(sc.fts_score, 0) * 0.15 +
            -- Association score: 25% (reduced from 30%)
            COALESCE(sc.assoc_score, 0) * 0.25 +
            -- Temporal score: 10% (reduced from 15%)
            COALESCE(sc.temp_score, 0) * 0.10 +
            -- Relevance decay: 5%
            calculate_relevance(m.importance, m.decay_rate, m.created_at, m.last_accessed) * 0.05 +
            -- Mood-congruent recall: 5%
            (CASE
                WHEN em.emotional_valence IS NULL THEN 0.5
                ELSE 1.0 - (ABS(em.emotional_valence - current_valence) / 2.0)
            END) * 0.05,
            0.001
        ) as final_score,
        CASE
            WHEN sc.fts_score IS NOT NULL AND sc.vector_score IS NOT NULL THEN 'hybrid'
            WHEN sc.fts_score IS NOT NULL THEN 'fts'
            WHEN sc.vector_score IS NOT NULL THEN 'vector'
            WHEN sc.assoc_score IS NOT NULL THEN 'association'
            WHEN sc.temp_score IS NOT NULL THEN 'temporal'
            ELSE 'fallback'
        END as source
    FROM scored sc
    JOIN memories m ON sc.mem_id = m.id
    LEFT JOIN episodic_memories em ON em.memory_id = m.id
    WHERE m.status = 'active'
    ORDER BY final_score DESC
    LIMIT p_limit;
END;
$$;

-- Step 8: Add comment explaining the scoring weights
COMMENT ON FUNCTION fast_recall_hybrid IS
'Hybrid recall combining vector similarity (40%), FTS keywords (15%),
associations (25%), temporal (10%), relevance (5%), mood (5%).
FTS helps catch exact name matches that vector similarity might miss.
Use this instead of fast_recall for better recall on short/specific queries.';

-- Verify the migration
DO $$
BEGIN
    -- Check if tsvector column exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memories' AND column_name = 'content_tsv'
    ) THEN
        RAISE EXCEPTION 'content_tsv column not created';
    END IF;

    RAISE NOTICE 'Migration 012 complete: Hybrid recall with FTS enabled';
END $$;
