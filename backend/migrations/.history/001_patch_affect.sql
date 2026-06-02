-- Patch migration: affect system fixes (momentum + goal-change handling + recall coupling).
-- Safe to re-run (idempotent ALTER / CREATE OR REPLACE).

ALTER TABLE heartbeat_state
    ADD COLUMN IF NOT EXISTS affective_state JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE OR REPLACE FUNCTION get_current_affective_state()
RETURNS JSONB AS $$
DECLARE
    st RECORD;
    fallback JSONB;
BEGIN
    SELECT * INTO st FROM heartbeat_state WHERE id = 1;

    IF st.affective_state IS NOT NULL AND st.affective_state <> '{}'::jsonb THEN
        RETURN st.affective_state;
    END IF;

    SELECT jsonb_build_object(
        'valence', valence,
        'arousal', arousal,
        'dominance', dominance,
        'primary_emotion', primary_emotion,
        'intensity', intensity,
        'recorded_at', recorded_at,
        'source', 'history_fallback'
    )
    INTO fallback
    FROM current_emotional_state;

    RETURN COALESCE(fallback, '{}'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '{}'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION set_current_affective_state(p_state JSONB)
RETURNS VOID AS $$
BEGIN
    UPDATE heartbeat_state
    SET affective_state = COALESCE(p_state, '{}'::jsonb),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION gather_turn_context()
RETURNS JSONB AS $$
DECLARE
    state_record RECORD;
    action_costs JSONB;
BEGIN
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;

    SELECT jsonb_object_agg(
        regexp_replace(key, '^cost_', ''),
        value
    ) INTO action_costs
    FROM heartbeat_config
    WHERE key LIKE 'cost_%';

    RETURN jsonb_build_object(
        'agent', get_agent_profile_context(),
        'environment', get_environment_snapshot(),
        'goals', get_goals_snapshot(),
        'recent_memories', get_recent_context(5),
        'identity', get_identity_context(),
        'worldview', get_worldview_context(),
        'self_model', get_self_model_context(25),
        'narrative', get_narrative_context(),
        'energy', jsonb_build_object(
            'current', state_record.current_energy,
            'max', (SELECT value FROM heartbeat_config WHERE key = 'max_energy')
        ),
        'action_costs', action_costs,
        'heartbeat_number', state_record.heartbeat_count,
        'urgent_drives', (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'name', name,
                        'level', current_level,
                        'urgency_ratio', current_level / NULLIF(urgency_threshold, 0)
                    )
                    ORDER BY current_level DESC
                ),
                '[]'::jsonb
            )
            FROM drives
            WHERE current_level >= urgency_threshold * 0.8
        ),
        'emotional_state', get_current_affective_state()
    );
END;
$$ LANGUAGE plpgsql;

-- Remove legacy 4-arg version if present so calls use the momentum-aware version.
DROP FUNCTION IF EXISTS complete_heartbeat(UUID, TEXT, JSONB, JSONB);

CREATE OR REPLACE FUNCTION complete_heartbeat(
    p_heartbeat_id UUID,
    p_reasoning TEXT,
    p_actions_taken JSONB,
    p_goals_modified JSONB DEFAULT '[]',
    p_emotional_assessment JSONB DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    narrative_text TEXT;
    memory_id_created UUID;
    hb_number INT;
    state_record RECORD;
    prev_state JSONB;
    prev_valence FLOAT;
    prev_arousal FLOAT;
    new_valence FLOAT;
    new_arousal FLOAT;
    primary_emotion TEXT;
    intensity FLOAT;
    action_elem JSONB;
    goal_elem JSONB;
    goal_change TEXT;
    assess_valence FLOAT;
    assess_arousal FLOAT;
    assess_primary TEXT;
    mem_importance FLOAT;
BEGIN
    SELECT heartbeat_number INTO hb_number FROM heartbeat_log WHERE id = p_heartbeat_id;

    SELECT string_agg(
        format('- %s: %s',
            a->>'action',
            CASE
                WHEN COALESCE((a->'result'->>'success')::boolean, true) = false THEN 'failed'
                ELSE 'completed'
            END
        ), E'\n'
    ) INTO narrative_text
    FROM jsonb_array_elements(p_actions_taken) a;

    narrative_text := format('Heartbeat #%s: %s', hb_number, COALESCE(narrative_text, 'No actions taken'));

    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    prev_state := COALESCE(state_record.affective_state, '{}'::jsonb);

    BEGIN
        prev_valence := NULLIF(prev_state->>'valence', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            prev_valence := NULL;
    END;
    BEGIN
        prev_arousal := NULLIF(prev_state->>'arousal', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            prev_arousal := NULL;
    END;

    prev_valence := COALESCE(prev_valence, 0.0);
    prev_arousal := COALESCE(prev_arousal, 0.5);

    new_valence := prev_valence * 0.8;
    new_arousal := 0.5 + (prev_arousal - 0.5) * 0.8;

    FOR action_elem IN SELECT * FROM jsonb_array_elements(COALESCE(p_actions_taken, '[]'::jsonb))
    LOOP
        IF (action_elem->'result'->>'error') = 'Boundary triggered' THEN
            new_valence := new_valence - 0.4;
            new_arousal := new_arousal + 0.3;
        ELSIF COALESCE((action_elem->'result'->>'success')::boolean, true) = false THEN
            new_valence := new_valence - 0.1;
            new_arousal := new_arousal + 0.1;
        END IF;

        IF (action_elem->>'action') IN ('reach_out_user', 'reach_out_public') THEN
            IF COALESCE((action_elem->'result'->>'success')::boolean, true) = true THEN
                new_valence := new_valence + 0.2;
                new_arousal := new_arousal + 0.1;
            END IF;
        END IF;

        IF (action_elem->>'action') = 'rest' THEN
            new_valence := new_valence + 0.1;
            new_arousal := new_arousal - 0.2;
        END IF;
    END LOOP;

    FOR goal_elem IN SELECT * FROM jsonb_array_elements(COALESCE(p_goals_modified, '[]'::jsonb))
    LOOP
        goal_change := COALESCE(goal_elem->>'new_priority', goal_elem->>'change', goal_elem->>'priority', '');
        IF goal_change = 'completed' THEN
            new_valence := new_valence + 0.3;
            new_arousal := new_arousal + 0.1;
        ELSIF goal_change = 'abandoned' THEN
            new_valence := new_valence - 0.2;
            new_arousal := new_arousal - 0.1;
        END IF;
    END LOOP;

    assess_valence := NULL;
    assess_arousal := NULL;
    assess_primary := NULL;
    IF p_emotional_assessment IS NOT NULL AND jsonb_typeof(p_emotional_assessment) = 'object' THEN
        BEGIN
            assess_valence := NULLIF(p_emotional_assessment->>'valence', '')::float;
        EXCEPTION
            WHEN OTHERS THEN
                assess_valence := NULL;
        END;
        BEGIN
            assess_arousal := NULLIF(p_emotional_assessment->>'arousal', '')::float;
        EXCEPTION
            WHEN OTHERS THEN
                assess_arousal := NULL;
        END;
        assess_primary := NULLIF(p_emotional_assessment->>'primary_emotion', '');
    END IF;

    IF assess_valence IS NOT NULL THEN
        new_valence := new_valence * 0.6 + LEAST(1.0, GREATEST(-1.0, assess_valence)) * 0.4;
    END IF;
    IF assess_arousal IS NOT NULL THEN
        new_arousal := new_arousal * 0.6 + LEAST(1.0, GREATEST(0.0, assess_arousal)) * 0.4;
    END IF;

    new_valence := LEAST(1.0, GREATEST(-1.0, new_valence));
    new_arousal := LEAST(1.0, GREATEST(0.0, new_arousal));

    primary_emotion := COALESCE(
        assess_primary,
        CASE
            WHEN new_valence > 0.2 AND new_arousal > 0.6 THEN 'excited'
            WHEN new_valence > 0.2 THEN 'content'
            WHEN new_valence < -0.2 AND new_arousal > 0.6 THEN 'anxious'
            WHEN new_valence < -0.2 THEN 'down'
            ELSE 'neutral'
        END
    );

    intensity := LEAST(1.0, GREATEST(0.0, (ABS(new_valence) * 0.6 + new_arousal * 0.4)));

    UPDATE heartbeat_state SET
        affective_state = jsonb_build_object(
            'valence', new_valence,
            'arousal', new_arousal,
            'primary_emotion', primary_emotion,
            'intensity', intensity,
            'updated_at', CURRENT_TIMESTAMP,
            'source', CASE WHEN p_emotional_assessment IS NULL THEN 'derived' ELSE 'blended' END
        )
    WHERE id = 1;

    PERFORM record_emotion(
        p_valence := new_valence,
        p_arousal := new_arousal,
        p_primary_emotion := primary_emotion,
        p_triggered_by_type := 'heartbeat',
        p_triggered_by_id := p_heartbeat_id,
        p_heartbeat_id := p_heartbeat_id,
        p_trigger_description := CASE WHEN p_emotional_assessment IS NULL THEN 'Derived from heartbeat events' ELSE 'Blended from prior state + events + self-report' END,
        p_dominance := 0.5,
        p_intensity := intensity
    );

    mem_importance := LEAST(1.0, GREATEST(0.4, 0.5 + intensity * 0.25));
    memory_id_created := create_episodic_memory(
        p_content := narrative_text,
        p_context := jsonb_build_object(
            'heartbeat_id', p_heartbeat_id,
            'heartbeat_number', hb_number,
            'reasoning', p_reasoning,
            'affective_state', get_current_affective_state()
        ),
        p_emotional_valence := new_valence,
        p_importance := mem_importance
    );

    UPDATE heartbeat_log SET
        ended_at = CURRENT_TIMESTAMP,
        energy_end = get_current_energy(),
        decision_reasoning = p_reasoning,
        actions_taken = p_actions_taken,
        goals_modified = p_goals_modified,
        narrative = narrative_text,
        emotional_valence = new_valence,
        memory_id = memory_id_created
    WHERE id = p_heartbeat_id;

    UPDATE heartbeat_state SET
        next_heartbeat_at = CURRENT_TIMESTAMP +
            ((SELECT value FROM heartbeat_config WHERE key = 'heartbeat_interval_minutes') || ' minutes')::INTERVAL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    RETURN memory_id_created;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fast_recall(
    p_query_text TEXT,
    p_limit INT DEFAULT 10
)
RETURNS TABLE (
    memory_id UUID,
    content TEXT,
    memory_type memory_type,
    score FLOAT,
    source TEXT
) AS $$
DECLARE
    query_embedding vector;
    zero_vec vector;
    current_valence FLOAT;
BEGIN
    query_embedding := get_embedding(p_query_text);
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;
    BEGIN
        current_valence := NULLIF(get_current_affective_state()->>'valence', '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            current_valence := NULL;
    END;
    current_valence := COALESCE(current_valence, 0.0);

    RETURN QUERY
    WITH
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
        LIMIT 5
    ),
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
    candidates AS (
        SELECT id as mem_id, sim as vector_score, NULL::float as assoc_score, NULL::float as temp_score
        FROM seeds
        UNION
        SELECT mem_id, NULL, assoc_score, NULL FROM associations
        UNION
        SELECT mem_id, NULL, NULL, temp_score FROM temporal
    ),
    scored AS (
        SELECT
            c.mem_id,
            MAX(c.vector_score) as vector_score,
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
            COALESCE(sc.vector_score, 0) * 0.5 +
            COALESCE(sc.assoc_score, 0) * 0.3 +
            COALESCE(sc.temp_score, 0) * 0.15 +
            calculate_relevance(m.importance, m.decay_rate, m.created_at, m.last_accessed) * 0.05 +
            (CASE
                WHEN em.emotional_valence IS NULL THEN 0.5
                ELSE 1.0 - (ABS(em.emotional_valence - current_valence) / 2.0)
            END) * 0.05,
            0.001
        ) as final_score,
        CASE
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
$$ LANGUAGE plpgsql;

