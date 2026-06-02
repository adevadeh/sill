-- Track which concepts are involved in each synthesis attempt
-- Enables: "for concept X, what's the acceptance rate over time?"

CREATE TABLE IF NOT EXISTS synthesis_attempt_concepts (
  synthesis_attempt_id UUID REFERENCES synthesis_attempts(id) ON DELETE CASCADE,
  concept_id UUID REFERENCES concepts(id) ON DELETE CASCADE,
  source TEXT NOT NULL DEFAULT 'retrieval' CHECK (source IN ('retrieval', 'query')),
  memory_count INT NOT NULL DEFAULT 0,  -- how many retrieved memories had this concept
  weight FLOAT NOT NULL DEFAULT 1.0,    -- share of retrieved memories with this concept
  is_primary BOOLEAN NOT NULL DEFAULT FALSE,  -- top concept for this attempt
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (synthesis_attempt_id, concept_id, source)
);

CREATE INDEX IF NOT EXISTS idx_sac_concept ON synthesis_attempt_concepts(concept_id);
CREATE INDEX IF NOT EXISTS idx_sac_attempt ON synthesis_attempt_concepts(synthesis_attempt_id);
CREATE INDEX IF NOT EXISTS idx_sac_primary ON synthesis_attempt_concepts(concept_id) WHERE is_primary;

-- Helper function: Get acceptance rate by concept over time
CREATE OR REPLACE FUNCTION concept_acceptance_rate(
  concept_name TEXT,
  lookback INTERVAL DEFAULT '30 days'
)
RETURNS TABLE (
  day DATE,
  attempts BIGINT,
  accepted BIGINT,
  accept_rate FLOAT
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    date_trunc('day', sa.attempted_at)::DATE AS day,
    COUNT(*) AS attempts,
    COUNT(*) FILTER (WHERE sa.result = 'accepted') AS accepted,
    AVG((sa.result = 'accepted')::int::float) AS accept_rate
  FROM synthesis_attempt_concepts sac
  JOIN synthesis_attempts sa ON sa.id = sac.synthesis_attempt_id
  JOIN concepts c ON c.id = sac.concept_id
  WHERE c.name ILIKE concept_name
    AND sac.source = 'retrieval'
    AND sa.attempted_at > CURRENT_TIMESTAMP - lookback
  GROUP BY 1
  ORDER BY 1;
END;
$$ LANGUAGE plpgsql;

-- Helper function: Find saturated concepts (high attempt count, low acceptance)
CREATE OR REPLACE FUNCTION find_saturated_concepts(
  min_attempts INT DEFAULT 10,
  max_accept_rate FLOAT DEFAULT 0.3,
  lookback INTERVAL DEFAULT '7 days'
)
RETURNS TABLE (
  concept TEXT,
  attempts BIGINT,
  accepted BIGINT,
  accept_rate FLOAT
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    c.name AS concept,
    COUNT(*) AS attempts,
    COUNT(*) FILTER (WHERE sa.result = 'accepted') AS accepted,
    AVG((sa.result = 'accepted')::int::float) AS accept_rate
  FROM synthesis_attempt_concepts sac
  JOIN synthesis_attempts sa ON sa.id = sac.synthesis_attempt_id
  JOIN concepts c ON c.id = sac.concept_id
  WHERE sac.source = 'retrieval'
    AND sa.attempted_at > CURRENT_TIMESTAMP - lookback
  GROUP BY c.name
  HAVING COUNT(*) >= min_attempts
     AND AVG((sa.result = 'accepted')::int::float) <= max_accept_rate
  ORDER BY COUNT(*) DESC;
END;
$$ LANGUAGE plpgsql;
