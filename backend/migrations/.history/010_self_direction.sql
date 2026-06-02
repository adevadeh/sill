-- Self-direction tables: enable Sili to participate in memory curation
-- Closes the loop: self-model can now affect what persists

-- 1. Archive/forget memories (soft delete with reason tracking)
ALTER TABLE memories ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS archive_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived_at) WHERE archived_at IS NOT NULL;

-- Function to archive a memory with reason
CREATE OR REPLACE FUNCTION archive_memory(
  p_memory_id UUID,
  p_reason TEXT DEFAULT ''
) RETURNS BOOLEAN AS $$
DECLARE
  v_rows_affected INT;
BEGIN
  UPDATE memories
  SET status = 'archived',
      archived_at = CURRENT_TIMESTAMP,
      archive_reason = p_reason
  WHERE id = p_memory_id
    AND status = 'active';

  GET DIAGNOSTICS v_rows_affected = ROW_COUNT;
  RETURN v_rows_affected > 0;
END;
$$ LANGUAGE plpgsql;

-- 2. Quality pattern suggestions (for human review)
CREATE TABLE IF NOT EXISTS quality_pattern_suggestions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pattern TEXT NOT NULL,
  reason TEXT NOT NULL,
  examples JSONB DEFAULT '[]'::jsonb,  -- array of example strings that should match
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  suggested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TIMESTAMPTZ,
  reviewer_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_qps_status ON quality_pattern_suggestions(status);
CREATE INDEX IF NOT EXISTS idx_qps_suggested ON quality_pattern_suggestions(suggested_at);

-- 3. Exploration requests (influence heartbeat queries)
CREATE TABLE IF NOT EXISTS exploration_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  query TEXT NOT NULL,
  reason TEXT NOT NULL,
  priority FLOAT NOT NULL DEFAULT 0.5 CHECK (priority >= 0 AND priority <= 1),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'explored', 'rejected')),
  requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  explored_at TIMESTAMPTZ,
  heartbeat_id UUID REFERENCES heartbeat_log(id)
);

CREATE INDEX IF NOT EXISTS idx_er_status ON exploration_requests(status);
CREATE INDEX IF NOT EXISTS idx_er_priority ON exploration_requests(priority DESC) WHERE status = 'pending';

-- Function to get pending exploration requests ordered by priority
CREATE OR REPLACE FUNCTION get_pending_explorations(
  p_limit INT DEFAULT 5
) RETURNS TABLE (
  id UUID,
  query TEXT,
  reason TEXT,
  priority FLOAT,
  requested_at TIMESTAMPTZ
) AS $$
BEGIN
  RETURN QUERY
  SELECT er.id, er.query, er.reason, er.priority, er.requested_at
  FROM exploration_requests er
  WHERE er.status = 'pending'
  ORDER BY er.priority DESC, er.requested_at ASC
  LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- 4. Review queue (flag things for human review)
CREATE TABLE IF NOT EXISTS review_queue (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
  question TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved')),
  flagged_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TIMESTAMPTZ,
  resolution TEXT
);

CREATE INDEX IF NOT EXISTS idx_rq_status ON review_queue(status);
CREATE INDEX IF NOT EXISTS idx_rq_memory ON review_queue(memory_id);

-- 5. Importance updates (track changes to importance)
CREATE TABLE IF NOT EXISTS importance_updates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  old_importance FLOAT NOT NULL,
  new_importance FLOAT NOT NULL,
  reason TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_iu_memory ON importance_updates(memory_id);

-- Function to update importance with tracking
CREATE OR REPLACE FUNCTION update_memory_importance(
  p_memory_id UUID,
  p_new_importance FLOAT,
  p_reason TEXT DEFAULT ''
) RETURNS BOOLEAN AS $$
DECLARE
  v_old_importance FLOAT;
  v_updated BOOLEAN := FALSE;
BEGIN
  -- Get current importance
  SELECT importance INTO v_old_importance
  FROM memories WHERE id = p_memory_id;

  IF v_old_importance IS NULL THEN
    RETURN FALSE;
  END IF;

  -- Update memory
  UPDATE memories SET importance = p_new_importance WHERE id = p_memory_id;

  -- Log the change
  INSERT INTO importance_updates (memory_id, old_importance, new_importance, reason)
  VALUES (p_memory_id, v_old_importance, p_new_importance, p_reason);

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- Update search functions to exclude archived memories
-- (Modify existing search_similar_memories to filter out archived)
CREATE OR REPLACE FUNCTION search_similar_memories(
  query_text TEXT,
  result_limit INT DEFAULT 10,
  type_filter memory_type[] DEFAULT NULL
)
RETURNS TABLE (
  id UUID,
  type memory_type,
  content TEXT,
  importance FLOAT,
  created_at TIMESTAMPTZ,
  similarity FLOAT
) AS $$
DECLARE
  query_embedding vector(1536);
BEGIN
  -- Get embedding for query
  query_embedding := get_embedding(query_text);

  RETURN QUERY
  SELECT
    m.id,
    m.type,
    m.content,
    m.importance,
    m.created_at,
    1 - (m.embedding <=> query_embedding) AS similarity
  FROM memories m
  WHERE m.embedding IS NOT NULL
    AND m.archived_at IS NULL  -- Exclude archived
    AND (type_filter IS NULL OR m.type = ANY(type_filter))
  ORDER BY m.embedding <=> query_embedding
  LIMIT result_limit;
END;
$$ LANGUAGE plpgsql;
