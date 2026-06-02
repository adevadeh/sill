-- Vital Signs Infrastructure
-- Adds health snapshots, synthesis tracking, and vital signs function

-- Health snapshots for trend tracking
CREATE TABLE IF NOT EXISTS health_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_health_snapshots_captured ON health_snapshots(captured_at DESC);

-- Synthesis attempts for tracking accept/reject rates
CREATE TABLE IF NOT EXISTS synthesis_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    heartbeat_id UUID,  -- Optional reference, not enforced
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    query TEXT,
    result TEXT NOT NULL CHECK (result IN ('accepted', 'rejected_similarity', 'rejected_quality', 'rejected_empty', 'error')),
    similarity_score FLOAT,
    details JSONB
);

CREATE INDEX IF NOT EXISTS idx_synthesis_attempts_time ON synthesis_attempts(attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_synthesis_attempts_result ON synthesis_attempts(result);

-- Get vital signs function
CREATE OR REPLACE FUNCTION get_vital_signs(window_interval INTERVAL DEFAULT '24 hours')
RETURNS JSONB AS $$
DECLARE
    vitals JSONB;
    goal_stats JSONB;
    memory_stats JSONB;
    synthesis_stats JSONB;
    topic_stats JSONB;
    alerts JSONB[];
BEGIN
    -- Goal system health
    SELECT jsonb_build_object(
        'active_count', COUNT(*) FILTER (WHERE priority = 'active'),
        'queued_count', COUNT(*) FILTER (WHERE priority = 'queued'),
        'completed_count', COUNT(*) FILTER (WHERE priority = 'completed'),
        'staleness_days_p50', PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (NOW() - last_touched))/86400) FILTER (WHERE priority = 'active'),
        'staleness_days_p90', PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (NOW() - last_touched))/86400) FILTER (WHERE priority = 'active'),
        'stale_fraction', COALESCE(
            COUNT(*) FILTER (WHERE priority = 'active' AND last_touched < NOW() - INTERVAL '7 days')::FLOAT /
            NULLIF(COUNT(*) FILTER (WHERE priority = 'active'), 0),
            0
        )
    ) INTO goal_stats
    FROM goals;

    -- Memory store health
    SELECT jsonb_build_object(
        'total', COUNT(*),
        'by_type', jsonb_object_agg(type, cnt),
        'created_last_24h', COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours'),
        'created_last_7d', COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days'),
        'importance_p50', PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY importance),
        'importance_p90', PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY importance)
    ) INTO memory_stats
    FROM (
        SELECT type, importance, created_at, COUNT(*) OVER (PARTITION BY type) as cnt
        FROM memories
    ) m;

    -- Synthesis pipeline health (from new tracking table)
    SELECT jsonb_build_object(
        'attempts_in_window', COUNT(*),
        'accept_rate', COALESCE(
            COUNT(*) FILTER (WHERE result = 'accepted')::FLOAT / NULLIF(COUNT(*), 0),
            0
        ),
        'rejection_reasons', jsonb_object_agg(result, cnt) FILTER (WHERE result != 'accepted'),
        'avg_similarity_rejected', AVG(similarity_score) FILTER (WHERE result = 'rejected_similarity'),
        'similarity_p50', PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY similarity_score),
        'similarity_p90', PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY similarity_score)
    ) INTO synthesis_stats
    FROM (
        SELECT result, similarity_score, COUNT(*) OVER (PARTITION BY result) as cnt
        FROM synthesis_attempts
        WHERE attempted_at > NOW() - window_interval
    ) s;

    -- Topic diversity (from memory_concepts join)
    WITH recent_concepts AS (
        SELECT c.name, COUNT(*) as cnt
        FROM memory_concepts mc
        JOIN concepts c ON mc.concept_id = c.id
        JOIN memories m ON mc.memory_id = m.id
        WHERE m.created_at > NOW() - INTERVAL '7 days'
        GROUP BY c.name
    )
    SELECT jsonb_build_object(
        'unique_concepts_7d', COUNT(*),
        'top_concept', (SELECT name FROM recent_concepts ORDER BY cnt DESC LIMIT 1),
        'top_concept_share', COALESCE(
            (SELECT cnt::FLOAT FROM recent_concepts ORDER BY cnt DESC LIMIT 1) / NULLIF(SUM(cnt), 0),
            0
        ),
        'total_concept_links', SUM(cnt)
    ) INTO topic_stats
    FROM recent_concepts;

    -- Build alerts array
    alerts := ARRAY[]::JSONB[];

    -- Goal staleness alert
    IF (goal_stats->>'stale_fraction')::FLOAT > 0.5 THEN
        alerts := array_append(alerts, jsonb_build_object(
            'type', 'goal_staleness',
            'severity', 'warning',
            'message', 'Over 50% of active goals are stale (>7 days untouched)',
            'value', goal_stats->>'stale_fraction'
        ));
    END IF;

    -- Synthesis collapse alert
    IF (synthesis_stats->>'attempts_in_window')::INT > 10
       AND (synthesis_stats->>'accept_rate')::FLOAT < 0.1 THEN
        alerts := array_append(alerts, jsonb_build_object(
            'type', 'synthesis_collapse',
            'severity', 'critical',
            'message', 'Synthesis accept rate below 10%',
            'value', synthesis_stats->>'accept_rate'
        ));
    END IF;

    -- Topic saturation alert
    IF (topic_stats->>'top_concept_share')::FLOAT > 0.6 THEN
        alerts := array_append(alerts, jsonb_build_object(
            'type', 'topic_saturation',
            'severity', 'warning',
            'message', 'Single topic dominates recent memories',
            'value', topic_stats->>'top_concept'
        ));
    END IF;

    -- Assemble vitals
    vitals := jsonb_build_object(
        'captured_at', NOW(),
        'window', window_interval::TEXT,
        'goals', goal_stats,
        'memories', memory_stats,
        'synthesis', synthesis_stats,
        'topics', topic_stats,
        'alerts', to_jsonb(alerts),
        'alert_count', array_length(alerts, 1)
    );

    RETURN vitals;
END;
$$ LANGUAGE plpgsql;

-- Capture health snapshot function (to be called periodically)
CREATE OR REPLACE FUNCTION capture_health_snapshot()
RETURNS UUID AS $$
DECLARE
    snapshot_id UUID;
BEGIN
    INSERT INTO health_snapshots (payload)
    VALUES (get_vital_signs('24 hours'))
    RETURNING id INTO snapshot_id;

    RETURN snapshot_id;
END;
$$ LANGUAGE plpgsql;

-- Cleanup old snapshots (keep 30 days)
CREATE OR REPLACE FUNCTION cleanup_old_snapshots()
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM health_snapshots
    WHERE captured_at < NOW() - INTERVAL '30 days';
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
