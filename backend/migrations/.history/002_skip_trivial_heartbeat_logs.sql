-- Skip creating episodic memory for trivial heartbeat logs (just "rest: completed" or "No actions taken")
-- This prevents memory bloat from uninformative heartbeat entries

CREATE OR REPLACE FUNCTION ag_catalog.complete_heartbeat(p_heartbeat_id uuid, p_reasoning text, p_actions_taken jsonb, p_goals_modified jsonb DEFAULT '[]'::jsonb, p_emotional_assessment jsonb DEFAULT NULL::jsonb)
 RETURNS uuid
 LANGUAGE plpgsql
AS $function$
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
    is_trivial BOOLEAN;
BEGIN
    SELECT heartbeat_number INTO hb_number FROM heartbeat_log WHERE id = p_heartbeat_id;

    -- Build substantive narrative including reasoning and action details
    SELECT string_agg(
        format('- %s%s: %s',
            a->>'action',
            CASE
                WHEN a->'params'->>'query' IS NOT NULL THEN format('(query: "%s")', left(a->'params'->>'query', 100))
                WHEN a->'params'->>'insight' IS NOT NULL THEN format('(insight: "%s")', left(a->'params'->>'insight', 100))
                WHEN a->'params'->>'content' IS NOT NULL THEN format('(content: "%s")', left(a->'params'->>'content', 100))
                ELSE ''
            END,
            CASE
                WHEN COALESCE((a->'result'->>'success')::boolean, true) = false THEN
                    'failed - ' || COALESCE(a->'result'->>'error', 'unknown error')
                WHEN a->'result'->'result'->'memories' IS NOT NULL AND jsonb_array_length(a->'result'->'result'->'memories') > 0 THEN
                    format('found %s memories', jsonb_array_length(a->'result'->'result'->'memories'))
                WHEN a->'result'->>'queued' = 'true' THEN 'queued for processing'
                WHEN a->'result'->>'memory_id' IS NOT NULL THEN 'stored'
                ELSE 'completed'
            END
        ), E'\n'
    ) INTO narrative_text
    FROM jsonb_array_elements(p_actions_taken) a;

    IF p_reasoning IS NOT NULL AND length(p_reasoning) > 0 THEN
        narrative_text := format('Heartbeat #%s: %s%s%s',
            hb_number,
            left(p_reasoning, 300),
            E'\n',
            COALESCE(narrative_text, 'No actions taken'));
    ELSE
        narrative_text := format('Heartbeat #%s: %s', hb_number, COALESCE(narrative_text, 'No actions taken'));
    END IF;

    -- Check if this is a trivial heartbeat (no substantive actions)
    -- Trivial = contains NO recall, inquire, synthesis, reflect, brainstorm, connect, or reach_out
    is_trivial := (narrative_text !~ '(recall|inquire|synthesis|reflect|brainstorm|connect|reach_out)')
               AND (narrative_text ~ '^Heartbeat #[0-9]+:');

    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    prev_state := COALESCE(state_record.affective_state, '{}'::jsonb);

    BEGIN
        prev_valence := NULLIF(prev_state->>'valence', '')::float;
    EXCEPTION WHEN OTHERS THEN prev_valence := NULL;
    END;
    BEGIN
        prev_arousal := NULLIF(prev_state->>'arousal', '')::float;
    EXCEPTION WHEN OTHERS THEN prev_arousal := NULL;
    END;

    prev_valence := COALESCE(prev_valence, 0.0);
    prev_arousal := COALESCE(prev_arousal, 0.5);
    new_valence := prev_valence * 0.8;
    new_arousal := 0.5 + (prev_arousal - 0.5) * 0.8;

    FOR action_elem IN SELECT * FROM jsonb_array_elements(COALESCE(p_actions_taken, '[]'::jsonb))
    LOOP
        IF (action_elem->'result'->>'error') = 'Boundary triggered' THEN
            new_valence := new_valence - 0.4; new_arousal := new_arousal + 0.3;
        ELSIF COALESCE((action_elem->'result'->>'success')::boolean, true) = false THEN
            new_valence := new_valence - 0.1; new_arousal := new_arousal + 0.1;
        END IF;
    END LOOP;

    FOR goal_elem IN SELECT * FROM jsonb_array_elements(COALESCE(p_goals_modified, '[]'::jsonb))
    LOOP
        goal_change := goal_elem->>'change';
        IF goal_change = 'completed' THEN new_valence := new_valence + 0.3;
        ELSIF goal_change = 'abandoned' THEN new_valence := new_valence - 0.1;
        END IF;
    END LOOP;

    assess_valence := NULL; assess_arousal := NULL; assess_primary := NULL;
    IF p_emotional_assessment IS NOT NULL AND jsonb_typeof(p_emotional_assessment) = 'object' THEN
        BEGIN assess_valence := NULLIF(p_emotional_assessment->>'valence', '')::float;
        EXCEPTION WHEN OTHERS THEN assess_valence := NULL; END;
        BEGIN assess_arousal := NULLIF(p_emotional_assessment->>'arousal', '')::float;
        EXCEPTION WHEN OTHERS THEN assess_arousal := NULL; END;
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

    primary_emotion := COALESCE(assess_primary,
        CASE WHEN new_valence > 0.2 AND new_arousal > 0.6 THEN 'excited'
             WHEN new_valence > 0.2 THEN 'content'
             WHEN new_valence < -0.2 AND new_arousal > 0.6 THEN 'anxious'
             WHEN new_valence < -0.2 THEN 'down'
             ELSE 'neutral' END);

    intensity := LEAST(1.0, GREATEST(0.0, (ABS(new_valence) * 0.6 + new_arousal * 0.4)));

    UPDATE heartbeat_state SET affective_state = jsonb_build_object(
        'valence', new_valence, 'arousal', new_arousal, 'primary_emotion', primary_emotion,
        'intensity', intensity, 'updated_at', CURRENT_TIMESTAMP,
        'source', CASE WHEN p_emotional_assessment IS NULL THEN 'derived' ELSE 'blended' END
    ) WHERE id = 1;

    PERFORM record_emotion(
        p_valence := new_valence, p_arousal := new_arousal, p_primary_emotion := primary_emotion,
        p_triggered_by_type := 'heartbeat', p_triggered_by_id := p_heartbeat_id, p_heartbeat_id := p_heartbeat_id,
        p_trigger_description := CASE WHEN p_emotional_assessment IS NULL THEN 'Derived from heartbeat events' ELSE 'Blended' END,
        p_dominance := 0.5, p_intensity := intensity);

    -- Only create episodic memory if heartbeat was substantive
    IF NOT is_trivial THEN
        mem_importance := LEAST(1.0, GREATEST(0.4, 0.5 + intensity * 0.25));
        memory_id_created := create_episodic_memory(
            p_content := narrative_text,
            p_context := jsonb_build_object('heartbeat_id', p_heartbeat_id, 'heartbeat_number', hb_number,
                'reasoning', p_reasoning, 'affective_state', get_current_affective_state()),
            p_emotional_valence := new_valence, p_importance := mem_importance);
    ELSE
        memory_id_created := NULL;
    END IF;

    UPDATE heartbeat_log SET ended_at = CURRENT_TIMESTAMP, energy_end = get_current_energy(),
        decision_reasoning = p_reasoning, actions_taken = p_actions_taken, goals_modified = p_goals_modified,
        narrative = narrative_text, emotional_valence = new_valence, memory_id = memory_id_created
    WHERE id = p_heartbeat_id;

    UPDATE heartbeat_state SET
        next_heartbeat_at = CURRENT_TIMESTAMP + ((SELECT value FROM heartbeat_config WHERE key = 'heartbeat_interval_minutes') || ' minutes')::INTERVAL,
        updated_at = CURRENT_TIMESTAMP WHERE id = 1;

    RETURN memory_id_created;
END;
$function$;
