--
-- PostgreSQL database dump
--

\restrict 5RbqD1IECcbr9rmpF3BPRMZejM1ORNobFReXJqxFzo8IaGanagfLXuFheK7UtfL

-- Dumped from database version 18.1
-- Dumped by pg_dump version 18.1

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: gpu_queuing; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA gpu_queuing;


--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: claim_transcription_job(text, text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.claim_transcription_job(p_worker_id text, p_worker_type text, p_lease_seconds integer DEFAULT 3600) RETURNS TABLE(job_id text, media_path text, model text, language text, output_format text, options jsonb)
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_job_id TEXT;
  v_lease_expires TIMESTAMPTZ;
BEGIN
  v_lease_expires := now() + (p_lease_seconds || ' seconds')::INTERVAL;

  -- Atomically claim highest priority pending job
  UPDATE transcribe_jobs AS j
  SET
    status = 'claimed',
    worker_id = p_worker_id,
    worker_type = p_worker_type,
    claimed_at = now(),
    lease_expires_at = v_lease_expires,
    attempts = attempts + 1
  WHERE j.job_id = (
    SELECT t.job_id
    FROM transcribe_jobs t
    WHERE t.status = 'pending'
      AND t.attempts < t.max_attempts
    ORDER BY t.priority DESC, t.created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
  )
  RETURNING
    j.job_id, j.media_path, j.model, j.language,
    j.output_format, j.options
  INTO job_id, media_path, model, language, output_format, options;

  -- If no job claimed, return empty
  IF NOT FOUND THEN
    RETURN;
  END IF;

  v_job_id := job_id;

  -- Log claim event
  INSERT INTO transcribe_job_log (job_id, event_type, worker_id, message)
  VALUES (v_job_id, 'claimed', p_worker_id,
    format('Job claimed by worker %s (%s)', p_worker_id, p_worker_type));

  -- Update worker status
  UPDATE transcribe_workers
  SET status = 'busy', current_job_id = v_job_id
  WHERE worker_id = p_worker_id;

  RETURN NEXT;
END;
$$;


--
-- Name: enqueue_transcription_job(text, text, text, integer, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_transcription_job(p_media_path text, p_model text DEFAULT 'medium'::text, p_language text DEFAULT 'en'::text, p_priority integer DEFAULT 0, p_options jsonb DEFAULT '{}'::jsonb) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_job_id TEXT;
  v_ytid TEXT;
BEGIN
  -- Generate unique job ID
  v_job_id := 'transcribe_' || gen_random_uuid()::TEXT;

  -- Try to extract YTID from filename (pattern: YTID__*)
  v_ytid := (regexp_match(p_media_path, '([A-Za-z0-9_-]{11})__'))[1];

  -- Insert job
  INSERT INTO transcribe_jobs (
    job_id, media_path, ytid, model, language,
    priority, options, status
  ) VALUES (
    v_job_id, p_media_path, v_ytid, p_model, p_language,
    p_priority, p_options, 'pending'
  );

  -- Log creation
  INSERT INTO transcribe_job_log (job_id, event_type, message)
  VALUES (v_job_id, 'created', format('Job created for: %s', p_media_path));

  RETURN v_job_id;
END;
$$;


--
-- Name: find_person_segments(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.find_person_segments(person_query text) RETURNS TABLE(ytid text, video_title text, video_date date, chunk_id integer, summary text, person_name text)
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN QUERY
  SELECT
    seg.ytid,
    v.title AS video_title,
    v.title_date AS video_date,
    seg.chunk_id,
    seg.summary,
    si.value AS person_name
  FROM analyzed_segitems si
  JOIN analyzed_segments seg ON si.segment_id = seg.id
  JOIN videos v ON seg.ytid = v.ytid
  WHERE si.item_type='person' AND si.normalized ILIKE '%' || LOWER(person_query) || '%'
  ORDER BY v.title_date DESC NULLS LAST, seg.chunk_id;
END;
$$;


--
-- Name: FUNCTION find_person_segments(person_query text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.find_person_segments(person_query text) IS 'Find segments mentioning a specific person';


--
-- Name: find_topic_segments(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.find_topic_segments(topic_query text) RETURNS TABLE(ytid text, video_title text, video_date date, chunk_id integer, summary text, topic text)
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN QUERY
  SELECT
    seg.ytid,
    v.title AS video_title,
    v.title_date AS video_date,
    seg.chunk_id,
    seg.summary,
    si.value AS topic
  FROM analyzed_segitems si
  JOIN analyzed_segments seg ON si.segment_id = seg.id
  JOIN videos v ON seg.ytid = v.ytid
  WHERE si.item_type='topic' AND si.normalized ILIKE '%' || LOWER(topic_query) || '%'
  ORDER BY v.title_date DESC NULLS LAST, seg.chunk_id;
END;
$$;


--
-- Name: FUNCTION find_topic_segments(topic_query text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.find_topic_segments(topic_query text) IS 'Find segments discussing a specific topic';


--
-- Name: recover_stale_jobs(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.recover_stale_jobs(p_lease_buffer_seconds integer DEFAULT 300) RETURNS TABLE(job_id text, worker_id text, stale_duration interval)
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN QUERY
  UPDATE transcribe_jobs AS j
  SET
    status = 'pending',
    worker_id = NULL,
    lease_expires_at = NULL
  WHERE j.status IN ('claimed', 'running')
    AND j.lease_expires_at < (now() - (p_lease_buffer_seconds || ' seconds')::INTERVAL)
  RETURNING
    j.job_id,
    j.worker_id,
    now() - j.lease_expires_at AS stale_duration;

  -- Log recovery events
  INSERT INTO transcribe_job_log (job_id, event_type, message)
  SELECT
    j.job_id,
    'recovered',
    format('Job recovered from stale worker (lease expired)')
  FROM transcribe_jobs j
  WHERE j.status = 'pending'
    AND j.worker_id IS NULL
    AND j.lease_expires_at IS NULL
    AND NOT EXISTS (
      SELECT 1 FROM transcribe_job_log l
      WHERE l.job_id = j.job_id
      AND l.event_type = 'recovered'
      AND l.timestamp > now() - INTERVAL '1 minute'
    );
END;
$$;


--
-- Name: register_worker(text, text, text, integer, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.register_worker(p_worker_id text, p_worker_type text, p_hostname text, p_gpu_index integer DEFAULT NULL::integer, p_gpu_uuid text DEFAULT NULL::text, p_metadata jsonb DEFAULT '{}'::jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
  INSERT INTO transcribe_workers (
    worker_id, worker_type, hostname, gpu_index, gpu_uuid,
    model_loaded, compute_type, version, python_version
  ) VALUES (
    p_worker_id, p_worker_type, p_hostname, p_gpu_index, p_gpu_uuid,
    p_metadata->>'model', p_metadata->>'compute_type',
    p_metadata->>'version', p_metadata->>'python_version'
  )
  ON CONFLICT (worker_id) DO UPDATE SET
    last_heartbeat = now(),
    status = 'idle',
    model_loaded = EXCLUDED.model_loaded,
    compute_type = EXCLUDED.compute_type;
END;
$$;


--
-- Name: search_segments(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_segments(search_query text) RETURNS TABLE(ytid text, video_title text, chunk_id integer, summary text, sentiment text, rank real)
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN QUERY
  SELECT
    seg.ytid,
    v.title AS video_title,
    seg.chunk_id,
    seg.summary,
    seg.sentiment,
    ts_rank(to_tsvector('english', seg.summary), plainto_tsquery('english', search_query)) AS rank
  FROM analyzed_segments seg
  JOIN videos v USING (ytid)
  WHERE to_tsvector('english', seg.summary) @@ plainto_tsquery('english', search_query)
  ORDER BY rank DESC, seg.analyzed_at DESC;
END;
$$;


--
-- Name: FUNCTION search_segments(search_query text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.search_segments(search_query text) IS 'Full-text search across segment summaries';


--
-- Name: update_analysis_task_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_analysis_task_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


--
-- Name: update_drill_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_drill_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


--
-- Name: update_job_status(text, text, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_job_status(p_job_id text, p_status text, p_error text DEFAULT NULL::text, p_metadata jsonb DEFAULT NULL::jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_worker_id TEXT;
  v_event_type TEXT;
BEGIN
  UPDATE transcribe_jobs
  SET
    status = p_status,
    started_at = CASE WHEN p_status = 'running' THEN now() ELSE started_at END,
    completed_at = CASE WHEN p_status IN ('completed', 'failed', 'cancelled')
                        THEN now() ELSE completed_at END,
    last_error = COALESCE(p_error, last_error)
  WHERE job_id = p_job_id
  RETURNING worker_id INTO v_worker_id;

  -- Map status to valid event_type
  v_event_type := CASE p_status
    WHEN 'running' THEN 'started'
    WHEN 'completed' THEN 'completed'
    WHEN 'failed' THEN 'failed'
    WHEN 'cancelled' THEN 'cancelled'
    ELSE 'progress'
  END;

  -- Log event
  INSERT INTO transcribe_job_log (job_id, event_type, worker_id, message, metadata)
  VALUES (p_job_id, v_event_type, v_worker_id, p_error, p_metadata);

  -- Update worker status if job completed
  IF p_status IN ('completed', 'failed', 'cancelled') THEN
    UPDATE transcribe_workers
    SET
      status = 'idle',
      current_job_id = NULL,
      total_jobs_completed = CASE WHEN p_status = 'completed'
        THEN total_jobs_completed + 1 ELSE total_jobs_completed END,
      total_jobs_failed = CASE WHEN p_status = 'failed'
        THEN total_jobs_failed + 1 ELSE total_jobs_failed END
    WHERE worker_id = v_worker_id;
  END IF;
END;
$$;


--
-- Name: update_jobs_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_jobs_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
   NEW.updated_at = NOW();
   RETURN NEW;
END;
$$;


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
   NEW.updated_at = NOW();
   RETURN NEW;
END;
$$;


--
-- Name: worker_heartbeat(text, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.worker_heartbeat(p_worker_id text, p_status text DEFAULT 'idle'::text, p_stats jsonb DEFAULT '{}'::jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
  UPDATE transcribe_workers
  SET
    last_heartbeat = now(),
    status = p_status,
    cpu_percent = (p_stats->>'cpu_percent')::NUMERIC,
    memory_used_gb = (p_stats->>'memory_used_gb')::NUMERIC,
    gpu_memory_used_gb = (p_stats->>'gpu_memory_used_gb')::NUMERIC,
    gpu_utilization = (p_stats->>'gpu_utilization')::NUMERIC
  WHERE worker_id = p_worker_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Worker not found: %', p_worker_id;
  END IF;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: gpu_task_queue; Type: TABLE; Schema: gpu_queuing; Owner: -
--

CREATE TABLE gpu_queuing.gpu_task_queue (
    task_id text NOT NULL,
    payload jsonb NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    owner text,
    attempts integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_expires_at timestamp with time zone
);


--
-- Name: jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jobs (
    job_id text NOT NULL,
    job_type text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    ytid text,
    media_path text,
    config jsonb DEFAULT '{}'::jsonb,
    claimed_by text,
    claimed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    result jsonb DEFAULT '{}'::jsonb,
    error_message text,
    CONSTRAINT valid_status CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'running'::text, 'completed'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: TABLE jobs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.jobs IS 'Generic job queue for Overlord System (v2)';


--
-- Name: COLUMN jobs.config; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.jobs.config IS 'Job-specific configuration as JSONB (model, language, etc.)';


--
-- Name: COLUMN jobs.result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.jobs.result IS 'Job execution results as JSONB (output paths, metrics, etc.)';


--
-- Name: active_jobs_by_type; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.active_jobs_by_type AS
 SELECT job_type,
    status,
    count(*) AS count
   FROM public.jobs
  WHERE (status <> ALL (ARRAY['completed'::text, 'failed'::text, 'cancelled'::text]))
  GROUP BY job_type, status
  ORDER BY job_type, status;


--
-- Name: analysis_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_configs (
    id text NOT NULL,
    name text NOT NULL,
    analysis_type text NOT NULL,
    version integer NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    config_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: analysis_configs_backup_pre_drills_migration; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_configs_backup_pre_drills_migration (
    id text,
    name text,
    analysis_type text,
    version integer,
    is_default boolean,
    config_json jsonb,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


--
-- Name: video_analysis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_analysis (
    id bigint NOT NULL,
    ytid text NOT NULL,
    total_chunks integer NOT NULL,
    dominant_sentiment text,
    tldr_one_sentence text,
    summary_paragraph text,
    social_caption text,
    hashtags text[],
    analyzed_at timestamp with time zone DEFAULT now(),
    model text,
    batch_id bigint,
    analysis_type text DEFAULT 'normal'::text,
    request_text text DEFAULT 'none'::text,
    political_overview text,
    controversies text,
    issues text[],
    noteworthy_statements text[],
    personal_conflicts text[],
    speaker_overview text,
    personal_themes text[],
    diarized boolean DEFAULT false,
    transcription_machine text
);


--
-- Name: TABLE video_analysis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.video_analysis IS 'Aggregated analysis per video';


--
-- Name: video_terms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_terms (
    id bigint NOT NULL,
    ytid text NOT NULL,
    term_type text NOT NULL,
    term text NOT NULL,
    normalized_term text NOT NULL,
    term_count integer DEFAULT 1 NOT NULL,
    CONSTRAINT video_terms_term_type_check CHECK ((term_type = ANY (ARRAY['person'::text, 'topic'::text])))
);


--
-- Name: video_people; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.video_people AS
 SELECT ytid,
    term AS person_name,
    normalized_term AS normalized_name,
    term_count AS mention_count
   FROM public.video_terms
  WHERE (term_type = 'person'::text);


--
-- Name: VIEW video_people; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.video_people IS 'View over video_terms for person mentions per video (ytid, person_name, normalized_name, mention_count).';


--
-- Name: video_topics; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.video_topics AS
 SELECT ytid,
    term AS topic,
    normalized_term AS normalized_topic,
    term_count AS occurrence_count
   FROM public.video_terms
  WHERE (term_type = 'topic'::text);


--
-- Name: VIEW video_topics; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.video_topics IS 'View over video_terms for topics per video (ytid, topic, normalized_topic, occurrence_count).';


--
-- Name: videos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.videos (
    ytid text NOT NULL,
    url text,
    title text,
    title_date date,
    upload_date date,
    duration_sec integer,
    channel text,
    channel_id text,
    extractor_key text,
    upload_type text,
    tags jsonb,
    categories jsonb,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: analysis_overview; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.analysis_overview AS
 SELECT v.ytid,
    v.title,
    v.title_date,
    v.upload_date,
    va.total_chunks,
    va.dominant_sentiment,
    va.tldr_one_sentence,
    va.analyzed_at,
    va.model,
    count(DISTINCT vp.person_name) AS unique_people,
    count(DISTINCT vt.topic) AS unique_topics
   FROM (((public.videos v
     JOIN public.video_analysis va USING (ytid))
     LEFT JOIN public.video_people vp USING (ytid))
     LEFT JOIN public.video_topics vt USING (ytid))
  GROUP BY v.ytid, v.title, v.title_date, v.upload_date, va.total_chunks, va.dominant_sentiment, va.tldr_one_sentence, va.analyzed_at, va.model
  ORDER BY va.analyzed_at DESC;


--
-- Name: analysis_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_results (
    id bigint NOT NULL,
    ytid text NOT NULL,
    config_id text NOT NULL,
    job_id text NOT NULL,
    results_by_pass jsonb NOT NULL,
    total_tasks integer NOT NULL,
    completed_tasks integer NOT NULL,
    failed_tasks integer DEFAULT 0,
    status text DEFAULT 'processing'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT analysis_results_status_check CHECK ((status = ANY (ARRAY['processing'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: analysis_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analysis_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analysis_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analysis_results_id_seq OWNED BY public.analysis_results.id;


--
-- Name: analysis_subchunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_subchunks (
    ytid text NOT NULL,
    subchunk_id integer NOT NULL,
    chunk_id integer,
    start_sec numeric,
    end_sec numeric
);


--
-- Name: analysis_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_tasks (
    task_id bigint NOT NULL,
    job_id text NOT NULL,
    ytid text NOT NULL,
    chunk_id integer NOT NULL,
    pass_id text NOT NULL,
    chunk_text text NOT NULL,
    chunk_metadata jsonb DEFAULT '{}'::jsonb,
    required_capabilities text[] DEFAULT '{}'::text[],
    result_json jsonb,
    status text DEFAULT 'pending'::text NOT NULL,
    claimed_by text,
    claimed_at timestamp with time zone,
    lease_expires_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT analysis_tasks_check CHECK ((((status = 'pending'::text) AND (claimed_by IS NULL) AND (claimed_at IS NULL)) OR ((status = 'claimed'::text) AND (claimed_by IS NOT NULL) AND (claimed_at IS NOT NULL)) OR ((status = 'completed'::text) AND (result_json IS NOT NULL) AND (completed_at IS NOT NULL)) OR ((status = 'failed'::text) AND (error_message IS NOT NULL)))),
    CONSTRAINT analysis_tasks_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: analysis_tasks_task_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analysis_tasks_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analysis_tasks_task_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analysis_tasks_task_id_seq OWNED BY public.analysis_tasks.task_id;


--
-- Name: analyzed_segitems; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analyzed_segitems (
    id bigint CONSTRAINT segment_items_id_not_null NOT NULL,
    segment_id bigint CONSTRAINT segment_items_segment_id_not_null NOT NULL,
    item_type text CONSTRAINT segment_items_item_type_not_null NOT NULL,
    value text CONSTRAINT segment_items_value_not_null NOT NULL,
    normalized text,
    item_order integer,
    CONSTRAINT segment_items_item_type_check CHECK ((item_type = ANY (ARRAY['person'::text, 'topic'::text, 'category'::text, 'key_point'::text, 'quote'::text])))
);


--
-- Name: analyzed_segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analyzed_segments (
    id bigint NOT NULL,
    ytid text NOT NULL,
    chunk_id integer NOT NULL,
    word_count integer NOT NULL,
    start_offset integer,
    end_offset integer,
    sentiment text,
    summary text,
    analyzed_at timestamp with time zone DEFAULT now(),
    model text,
    batch_id bigint,
    analysis_type text DEFAULT 'normal'::text,
    request_text text,
    diarized boolean DEFAULT false,
    transcription_machine text,
    start_sec numeric,
    end_sec numeric
);


--
-- Name: TABLE analyzed_segments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.analyzed_segments IS 'AI-analyzed transcript chunks with sentiment and summary';


--
-- Name: analyzed_segments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analyzed_segments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analyzed_segments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analyzed_segments_id_seq OWNED BY public.analyzed_segments.id;


--
-- Name: assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assets (
    id bigint NOT NULL,
    ytid text NOT NULL,
    kind text NOT NULL,
    path text NOT NULL,
    bytes bigint,
    sha256 text,
    created_at timestamp with time zone DEFAULT now(),
    rel_path text,
    CONSTRAINT assets_kind_check CHECK ((kind = ANY (ARRAY['info_json'::text, 'src_json'::text, 'mp4'::text, 'mkv'::text, 'webm'::text, 'm4a'::text, 'opus'::text, 'vtt'::text, 'words_ytt'::text, 'words_whisper'::text, 'cut'::text, 'transcript_vtt'::text, 'transcript_words'::text, 'clip'::text, 'analysis_json'::text, 'media'::text, 'clips_manifest'::text, 'diarization'::text, 'voice_match'::text, 'stitched'::text, 'analysis'::text, 'dates_manifest'::text, 'reference'::text])))
);


--
-- Name: assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.assets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: assets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.assets_id_seq OWNED BY public.assets.id;


--
-- Name: target_spans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.target_spans (
    id bigint CONSTRAINT conflict_spans_id_not_null NOT NULL,
    ytid text CONSTRAINT conflict_spans_ytid_not_null NOT NULL,
    parties text[],
    description text,
    start_sec numeric,
    end_sec numeric,
    chunk_ids integer[],
    context text,
    sentiment text,
    polarity text,
    source_pass text,
    pass_tier text,
    prompt_version text,
    analysis_type text DEFAULT 'normal'::text,
    batch_id bigint,
    diarized boolean DEFAULT false,
    transcription_machine text
);


--
-- Name: conflict_spans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conflict_spans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conflict_spans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conflict_spans_id_seq OWNED BY public.target_spans.id;


--
-- Name: diarized_timestamps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.diarized_timestamps (
    id bigint NOT NULL,
    ytid text NOT NULL,
    speaker_name text NOT NULL,
    start_sec numeric(10,3) NOT NULL,
    end_sec numeric(10,3) NOT NULL,
    duration_sec numeric(10,3) GENERATED ALWAYS AS ((end_sec - start_sec)) STORED,
    source_path text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT diarized_timestamps_check CHECK ((end_sec >= start_sec))
);


--
-- Name: TABLE diarized_timestamps; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.diarized_timestamps IS 'Speaker diarization spans per video (ingested from VTT-like timestamps).';


--
-- Name: diarized_timestamps_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.diarized_timestamps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diarized_timestamps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.diarized_timestamps_id_seq OWNED BY public.diarized_timestamps.id;


--
-- Name: drill_dependencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drill_dependencies (
    drill_id bigint NOT NULL,
    depends_on_id bigint NOT NULL,
    CONSTRAINT drill_dependencies_check CHECK ((drill_id <> depends_on_id))
);


--
-- Name: drills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drills (
    id bigint NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text,
    prompt text NOT NULL,
    scope text DEFAULT 'chunks'::text NOT NULL,
    output_shape text DEFAULT 'span'::text NOT NULL,
    category text,
    always boolean DEFAULT false NOT NULL,
    min_hits integer DEFAULT 0 NOT NULL,
    keywords text[] DEFAULT '{}'::text[],
    match text[] DEFAULT '{}'::text[],
    cooldown integer DEFAULT 0 NOT NULL,
    detail_pass jsonb DEFAULT '{}'::jsonb,
    config_id text,
    is_local boolean GENERATED ALWAYS AS ((config_id IS NOT NULL)) STORED,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT drills_name_check CHECK ((length(name) > 0)),
    CONSTRAINT drills_prompt_check CHECK ((length(prompt) > 0)),
    CONSTRAINT drills_scope_check CHECK ((scope = ANY (ARRAY['chunks'::text, 'spans'::text, 'subchunks'::text])))
);


--
-- Name: drills_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.drills_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: drills_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.drills_id_seq OWNED BY public.drills.id;


--
-- Name: hits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hits (
    id bigint NOT NULL,
    ytid text NOT NULL,
    start_sec numeric(10,3) NOT NULL,
    end_sec numeric(10,3) NOT NULL,
    duration_sec numeric(10,3) GENERATED ALWAYS AS ((end_sec - start_sec)) STORED,
    label text,
    source_caption text,
    source_type text,
    run_name text,
    CONSTRAINT hits_check CHECK ((end_sec >= start_sec))
);


--
-- Name: hits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hits_id_seq OWNED BY public.hits.id;


--
-- Name: jobs_test; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jobs_test (
    job_id text CONSTRAINT jobs_job_id_not_null NOT NULL,
    job_type text CONSTRAINT jobs_job_type_not_null NOT NULL,
    status text DEFAULT 'pending'::text CONSTRAINT jobs_status_not_null NOT NULL,
    priority integer DEFAULT 0 CONSTRAINT jobs_priority_not_null NOT NULL,
    ytid text,
    media_path text,
    config jsonb DEFAULT '{}'::jsonb,
    claimed_by text,
    claimed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    result jsonb DEFAULT '{}'::jsonb,
    error_message text,
    CONSTRAINT valid_status CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'running'::text, 'completed'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: COLUMN jobs_test.config; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.jobs_test.config IS 'Job-specific configuration as JSONB (model, language, etc.)';


--
-- Name: COLUMN jobs_test.result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.jobs_test.result IS 'Job execution results as JSONB (output paths, metrics, etc.)';


--
-- Name: person_spans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.person_spans (
    id bigint NOT NULL,
    ytid text NOT NULL,
    person_name text NOT NULL,
    normalized_name text NOT NULL,
    start_sec numeric,
    end_sec numeric,
    chunk_ids integer[],
    context text,
    sentiment text,
    polarity text,
    source_pass text,
    pass_tier text,
    prompt_version text,
    analysis_type text DEFAULT 'normal'::text,
    batch_id bigint,
    diarized boolean DEFAULT false,
    transcription_machine text
);


--
-- Name: person_spans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.person_spans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: person_spans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.person_spans_id_seq OWNED BY public.person_spans.id;


--
-- Name: quickclip_clips; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quickclip_clips (
    clip_id text NOT NULL,
    session_id text NOT NULL,
    ytid text NOT NULL,
    start_sec numeric NOT NULL,
    end_sec numeric NOT NULL,
    duration_sec numeric NOT NULL,
    label text,
    clip_index integer,
    asset_path text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: quickclip_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quickclip_sessions (
    session_id text NOT NULL,
    ytid text NOT NULL,
    url text NOT NULL,
    description text,
    tags text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    clips_count integer DEFAULT 0,
    full_video_saved boolean DEFAULT false,
    quality_profile text DEFAULT 'best'::text,
    total_duration_sec numeric,
    session_dir text
);


--
-- Name: segment_categories; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.segment_categories AS
 SELECT segment_id,
    value AS category
   FROM public.analyzed_segitems si
  WHERE (item_type = 'category'::text);


--
-- Name: VIEW segment_categories; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.segment_categories IS 'View over analyzed_segitems for category tags per segment (segment_id, category).';


--
-- Name: segment_items; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.segment_items AS
 SELECT id,
    segment_id,
    item_type,
    value,
    normalized,
    item_order
   FROM public.analyzed_segitems;


--
-- Name: segment_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.segment_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: segment_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.segment_items_id_seq OWNED BY public.analyzed_segitems.id;


--
-- Name: segment_key_points; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.segment_key_points AS
 SELECT segment_id,
    value AS key_point,
    item_order AS point_order
   FROM public.analyzed_segitems si
  WHERE (item_type = 'key_point'::text);


--
-- Name: VIEW segment_key_points; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.segment_key_points IS 'View over analyzed_segitems for ordered key points per segment (segment_id, key_point, point_order).';


--
-- Name: segment_overview; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.segment_overview AS
 SELECT seg.id AS segment_id,
    seg.ytid,
    v.title AS video_title,
    v.title_date AS video_date,
    seg.chunk_id,
    seg.word_count,
    seg.sentiment,
    seg.summary,
    seg.model AS model_used,
    seg.analyzed_at,
    COALESCE(( SELECT array_agg(si.value ORDER BY si.value) AS array_agg
           FROM public.analyzed_segitems si
          WHERE ((si.segment_id = seg.id) AND (si.item_type = 'person'::text))), '{}'::text[]) AS people,
    COALESCE(( SELECT array_agg(si.value ORDER BY si.value) AS array_agg
           FROM public.analyzed_segitems si
          WHERE ((si.segment_id = seg.id) AND (si.item_type = 'topic'::text))), '{}'::text[]) AS topics,
    COALESCE(( SELECT array_agg(si.value ORDER BY si.item_order) AS array_agg
           FROM public.analyzed_segitems si
          WHERE ((si.segment_id = seg.id) AND (si.item_type = 'key_point'::text))), '{}'::text[]) AS key_points,
    COALESCE(( SELECT array_agg(si.value ORDER BY si.item_order) AS array_agg
           FROM public.analyzed_segitems si
          WHERE ((si.segment_id = seg.id) AND (si.item_type = 'quote'::text))), '{}'::text[]) AS quotes,
    COALESCE(( SELECT array_agg(si.value ORDER BY si.value) AS array_agg
           FROM public.analyzed_segitems si
          WHERE ((si.segment_id = seg.id) AND (si.item_type = 'category'::text))), '{}'::text[]) AS categories
   FROM (public.analyzed_segments seg
     JOIN public.videos v USING (ytid));


--
-- Name: VIEW segment_overview; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.segment_overview IS 'Joined overview of segments with video title/date and aggregated people/topics/key_points/quotes/categories arrays.';


--
-- Name: segment_people; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.segment_people AS
 SELECT segment_id,
    value AS person_name,
    normalized AS normalized_name
   FROM public.analyzed_segitems si
  WHERE (item_type = 'person'::text);


--
-- Name: VIEW segment_people; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.segment_people IS 'View over analyzed_segitems for person mentions per segment (segment_id, person_name, normalized_name).';


--
-- Name: segment_quotes; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.segment_quotes AS
 SELECT segment_id,
    value AS quote_text,
    item_order AS quote_order
   FROM public.analyzed_segitems si
  WHERE (item_type = 'quote'::text);


--
-- Name: VIEW segment_quotes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.segment_quotes IS 'View over analyzed_segitems for ordered notable quotes per segment (segment_id, quote_text, quote_order).';


--
-- Name: segment_topics; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.segment_topics AS
 SELECT segment_id,
    value AS topic,
    normalized AS normalized_topic
   FROM public.analyzed_segitems si
  WHERE (item_type = 'topic'::text);


--
-- Name: VIEW segment_topics; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.segment_topics IS 'View over analyzed_segitems for topics per segment (segment_id, topic, normalized_topic).';


--
-- Name: sentiment_distribution; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.sentiment_distribution AS
 SELECT dominant_sentiment,
    count(*) AS video_count,
    round((((count(*))::numeric * 100.0) / sum(count(*)) OVER ()), 2) AS percentage
   FROM public.video_analysis
  WHERE (dominant_sentiment IS NOT NULL)
  GROUP BY dominant_sentiment
  ORDER BY (count(*)) DESC;


--
-- Name: words; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.words (
    id bigint NOT NULL,
    ytid text NOT NULL,
    source text NOT NULL,
    idx integer NOT NULL,
    word text NOT NULL,
    start_sec numeric(10,3) NOT NULL,
    end_sec numeric(10,3) NOT NULL,
    confidence real,
    segment_id integer,
    CONSTRAINT words_check CHECK ((end_sec >= start_sec)),
    CONSTRAINT words_source_check CHECK (((source = 'yt'::text) OR (source = 'whisper'::text) OR (source ~~ 'whisper-%'::text)))
);


--
-- Name: COLUMN words.source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.words.source IS 'Source of transcription: ''yt'' (YouTube auto-captions), ''whisper'' (legacy, model unknown), or ''whisper-{model}'' (e.g., ''whisper-medium'')';


--
-- Name: speaker_words; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.speaker_words AS
 SELECT w.ytid,
    dt.speaker_name,
    w.idx,
    w.word,
    w.start_sec,
    w.end_sec
   FROM (public.words w
     JOIN public.diarized_timestamps dt ON (((dt.ytid = w.ytid) AND (w.start_sec >= dt.start_sec) AND (w.start_sec < dt.end_sec))));


--
-- Name: VIEW speaker_words; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.speaker_words IS 'Join of diarized_timestamps and words: words uttered within diarized spans for each speaker (ytid, speaker_name, idx, word, start_sec, end_sec).';


--
-- Name: top_people_overall; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.top_people_overall AS
 SELECT normalized_term AS person,
    count(DISTINCT ytid) AS videos_mentioned,
    sum(term_count) AS total_mentions
   FROM public.video_terms vt
  WHERE (term_type = 'person'::text)
  GROUP BY normalized_term
  ORDER BY (sum(term_count)) DESC;


--
-- Name: top_topics_overall; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.top_topics_overall AS
 SELECT normalized_term AS topic,
    count(DISTINCT ytid) AS videos_discussed,
    sum(term_count) AS total_occurrences
   FROM public.video_terms vt
  WHERE (term_type = 'topic'::text)
  GROUP BY normalized_term
  ORDER BY (sum(term_count)) DESC;


--
-- Name: topic_spans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.topic_spans (
    id bigint NOT NULL,
    ytid text NOT NULL,
    topic text NOT NULL,
    normalized_topic text NOT NULL,
    start_sec numeric,
    end_sec numeric,
    chunk_ids integer[],
    context text,
    sentiment text,
    source_pass text,
    pass_tier text,
    prompt_version text,
    analysis_type text DEFAULT 'normal'::text,
    batch_id bigint,
    diarized boolean DEFAULT false,
    transcription_machine text
);


--
-- Name: topic_spans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.topic_spans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: topic_spans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.topic_spans_id_seq OWNED BY public.topic_spans.id;


--
-- Name: transcribe_fragments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcribe_fragments (
    id bigint NOT NULL,
    job_id text NOT NULL,
    fragment_index integer NOT NULL,
    start_sec numeric NOT NULL,
    end_sec numeric NOT NULL,
    duration_sec numeric GENERATED ALWAYS AS ((end_sec - start_sec)) STORED,
    status text DEFAULT 'pending'::text NOT NULL,
    worker_id text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    processing_time_sec numeric,
    segment_count integer,
    word_count integer,
    CONSTRAINT valid_fragment_status CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: transcribe_fragments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transcribe_fragments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transcribe_fragments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transcribe_fragments_id_seq OWNED BY public.transcribe_fragments.id;


--
-- Name: transcribe_job_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcribe_job_log (
    id bigint NOT NULL,
    job_id text NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    event_type text NOT NULL,
    worker_id text,
    message text,
    metadata jsonb,
    CONSTRAINT valid_event_type CHECK ((event_type = ANY (ARRAY['created'::text, 'claimed'::text, 'started'::text, 'progress'::text, 'completed'::text, 'failed'::text, 'cancelled'::text, 'recovered'::text, 'retry'::text, 'lease_expired'::text])))
);


--
-- Name: transcribe_job_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transcribe_job_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transcribe_job_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transcribe_job_log_id_seq OWNED BY public.transcribe_job_log.id;


--
-- Name: transcribe_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcribe_jobs (
    id bigint NOT NULL,
    job_id text NOT NULL,
    media_path text NOT NULL,
    ytid text,
    model text DEFAULT 'medium'::text NOT NULL,
    language text DEFAULT 'en'::text,
    output_format text DEFAULT 'vtt'::text NOT NULL,
    compute_type text,
    priority integer DEFAULT 0 NOT NULL,
    options jsonb DEFAULT '{}'::jsonb,
    status text DEFAULT 'pending'::text NOT NULL,
    worker_id text,
    worker_type text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    lease_expires_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    last_error text,
    output_base text,
    output_vtt text,
    output_srt text,
    output_words_tsv text,
    retry_manifest text,
    media_duration_sec numeric,
    fragment_count integer,
    processing_time_sec numeric,
    CONSTRAINT valid_output_format CHECK ((output_format = ANY (ARRAY['vtt'::text, 'srt'::text, 'both'::text]))),
    CONSTRAINT valid_status CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'running'::text, 'completed'::text, 'failed'::text, 'cancelled'::text]))),
    CONSTRAINT valid_worker_type CHECK (((worker_type IS NULL) OR (worker_type = ANY (ARRAY['nvidia'::text, 'cpu'::text, 'amd'::text]))))
);


--
-- Name: transcribe_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transcribe_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transcribe_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transcribe_jobs_id_seq OWNED BY public.transcribe_jobs.id;


--
-- Name: transcribe_queue_stats; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.transcribe_queue_stats AS
 SELECT status,
    count(*) AS job_count,
    avg(EXTRACT(epoch FROM (now() - created_at))) AS avg_wait_seconds,
    min(created_at) AS oldest_job
   FROM public.transcribe_jobs
  GROUP BY status;


--
-- Name: transcribe_recent_jobs; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.transcribe_recent_jobs AS
 SELECT job_id,
    media_path,
    status,
    worker_id,
    worker_type,
    priority,
    attempts,
    round(EXTRACT(epoch FROM (completed_at - started_at)), 2) AS duration_sec,
    created_at,
    completed_at
   FROM public.transcribe_jobs j
  ORDER BY created_at DESC
 LIMIT 100;


--
-- Name: transcribe_retry_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcribe_retry_queue (
    id bigint NOT NULL,
    retry_id text NOT NULL,
    original_job_id text,
    ytid text,
    manifest_path text NOT NULL,
    segment_count integer NOT NULL,
    retry_reason text,
    status text DEFAULT 'pending'::text NOT NULL,
    worker_id text,
    priority integer DEFAULT 50 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    completed_at timestamp with time zone,
    CONSTRAINT valid_retry_status CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'running'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: transcribe_retry_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transcribe_retry_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transcribe_retry_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transcribe_retry_queue_id_seq OWNED BY public.transcribe_retry_queue.id;


--
-- Name: transcribe_workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcribe_workers (
    id bigint NOT NULL,
    worker_id text NOT NULL,
    worker_type text NOT NULL,
    hostname text NOT NULL,
    gpu_index integer,
    gpu_uuid text,
    model_loaded text,
    compute_type text,
    max_concurrent_jobs integer DEFAULT 1 NOT NULL,
    status text DEFAULT 'idle'::text NOT NULL,
    current_job_id text,
    last_heartbeat timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    total_jobs_completed integer DEFAULT 0 NOT NULL,
    total_jobs_failed integer DEFAULT 0 NOT NULL,
    total_processing_sec numeric DEFAULT 0 NOT NULL,
    cpu_percent numeric,
    memory_used_gb numeric,
    gpu_memory_used_gb numeric,
    gpu_utilization numeric,
    version text,
    python_version text,
    CONSTRAINT valid_worker_status CHECK ((status = ANY (ARRAY['idle'::text, 'busy'::text, 'offline'::text, 'error'::text]))),
    CONSTRAINT valid_worker_type CHECK ((worker_type = ANY (ARRAY['nvidia'::text, 'cpu'::text, 'amd'::text])))
);


--
-- Name: transcribe_worker_stats; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.transcribe_worker_stats AS
 SELECT worker_id,
    worker_type,
    status,
    total_jobs_completed,
    total_jobs_failed,
    round((total_processing_sec / (NULLIF(total_jobs_completed, 0))::numeric), 2) AS avg_job_duration_sec,
    EXTRACT(epoch FROM (now() - last_heartbeat)) AS seconds_since_heartbeat,
    gpu_utilization,
    gpu_memory_used_gb
   FROM public.transcribe_workers w
  ORDER BY last_heartbeat DESC;


--
-- Name: transcribe_workers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transcribe_workers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transcribe_workers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transcribe_workers_id_seq OWNED BY public.transcribe_workers.id;


--
-- Name: transcripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcripts (
    id bigint NOT NULL,
    ytid text NOT NULL,
    kind text NOT NULL,
    lang text DEFAULT 'en'::text,
    path text NOT NULL,
    word_count integer,
    segment_count integer,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT transcripts_kind_check CHECK (((kind = ANY (ARRAY['vtt'::text, 'words_ytt'::text, 'words_whisper'::text])) OR (kind ~~ 'words_whisper_%'::text) OR (kind ~~ 'vtt_whisper_%'::text)))
);


--
-- Name: COLUMN transcripts.kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.transcripts.kind IS 'Transcript type (legacy: vtt|words_ytt|words_whisper; new: vtt_whisper_{model}, words_whisper_{model})';


--
-- Name: transcripts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transcripts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transcripts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transcripts_id_seq OWNED BY public.transcripts.id;


--
-- Name: video_analysis_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_analysis_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_analysis_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_analysis_id_seq OWNED BY public.video_analysis.id;


--
-- Name: video_terms_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.video_terms_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: video_terms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.video_terms_id_seq OWNED BY public.video_terms.id;


--
-- Name: words_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.words_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: words_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.words_id_seq OWNED BY public.words.id;


--
-- Name: workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workers (
    worker_id text NOT NULL,
    worker_type text NOT NULL,
    machine_alias text NOT NULL,
    status text DEFAULT 'registering'::text NOT NULL,
    capabilities jsonb DEFAULT '[]'::jsonb,
    current_job_id text,
    registered_at timestamp without time zone DEFAULT now(),
    last_heartbeat timestamp without time zone DEFAULT now(),
    pid integer,
    hostname text,
    CONSTRAINT valid_worker_status CHECK ((status = ANY (ARRAY['registering'::text, 'idle'::text, 'busy'::text, 'stopping'::text, 'errored'::text, 'stale'::text])))
);


--
-- Name: TABLE workers; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.workers IS 'Worker registry and heartbeat tracking';


--
-- Name: worker_summary; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.worker_summary AS
 SELECT worker_type,
    status,
    count(*) AS count,
    max(last_heartbeat) AS latest_heartbeat
   FROM public.workers
  GROUP BY worker_type, status
  ORDER BY worker_type, status;


--
-- Name: workers_test; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workers_test (
    worker_id text CONSTRAINT workers_worker_id_not_null NOT NULL,
    worker_type text CONSTRAINT workers_worker_type_not_null NOT NULL,
    machine_alias text CONSTRAINT workers_machine_alias_not_null NOT NULL,
    status text DEFAULT 'registering'::text CONSTRAINT workers_status_not_null NOT NULL,
    capabilities jsonb DEFAULT '[]'::jsonb,
    current_job_id text,
    registered_at timestamp without time zone DEFAULT now(),
    last_heartbeat timestamp without time zone DEFAULT now(),
    pid integer,
    hostname text,
    CONSTRAINT valid_worker_status CHECK ((status = ANY (ARRAY['registering'::text, 'idle'::text, 'busy'::text, 'stopping'::text, 'errored'::text, 'stale'::text])))
);


--
-- Name: analysis_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results ALTER COLUMN id SET DEFAULT nextval('public.analysis_results_id_seq'::regclass);


--
-- Name: analysis_tasks task_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_tasks ALTER COLUMN task_id SET DEFAULT nextval('public.analysis_tasks_task_id_seq'::regclass);


--
-- Name: analyzed_segitems id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analyzed_segitems ALTER COLUMN id SET DEFAULT nextval('public.segment_items_id_seq'::regclass);


--
-- Name: analyzed_segments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analyzed_segments ALTER COLUMN id SET DEFAULT nextval('public.analyzed_segments_id_seq'::regclass);


--
-- Name: assets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets ALTER COLUMN id SET DEFAULT nextval('public.assets_id_seq'::regclass);


--
-- Name: diarized_timestamps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diarized_timestamps ALTER COLUMN id SET DEFAULT nextval('public.diarized_timestamps_id_seq'::regclass);


--
-- Name: drills id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drills ALTER COLUMN id SET DEFAULT nextval('public.drills_id_seq'::regclass);


--
-- Name: hits id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hits ALTER COLUMN id SET DEFAULT nextval('public.hits_id_seq'::regclass);


--
-- Name: person_spans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_spans ALTER COLUMN id SET DEFAULT nextval('public.person_spans_id_seq'::regclass);


--
-- Name: target_spans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.target_spans ALTER COLUMN id SET DEFAULT nextval('public.conflict_spans_id_seq'::regclass);


--
-- Name: topic_spans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topic_spans ALTER COLUMN id SET DEFAULT nextval('public.topic_spans_id_seq'::regclass);


--
-- Name: transcribe_fragments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_fragments ALTER COLUMN id SET DEFAULT nextval('public.transcribe_fragments_id_seq'::regclass);


--
-- Name: transcribe_job_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_job_log ALTER COLUMN id SET DEFAULT nextval('public.transcribe_job_log_id_seq'::regclass);


--
-- Name: transcribe_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_jobs ALTER COLUMN id SET DEFAULT nextval('public.transcribe_jobs_id_seq'::regclass);


--
-- Name: transcribe_retry_queue id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_retry_queue ALTER COLUMN id SET DEFAULT nextval('public.transcribe_retry_queue_id_seq'::regclass);


--
-- Name: transcribe_workers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_workers ALTER COLUMN id SET DEFAULT nextval('public.transcribe_workers_id_seq'::regclass);


--
-- Name: transcripts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts ALTER COLUMN id SET DEFAULT nextval('public.transcripts_id_seq'::regclass);


--
-- Name: video_analysis id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_analysis ALTER COLUMN id SET DEFAULT nextval('public.video_analysis_id_seq'::regclass);


--
-- Name: video_terms id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_terms ALTER COLUMN id SET DEFAULT nextval('public.video_terms_id_seq'::regclass);


--
-- Name: words id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.words ALTER COLUMN id SET DEFAULT nextval('public.words_id_seq'::regclass);


--
-- Name: gpu_task_queue gpu_task_queue_pkey; Type: CONSTRAINT; Schema: gpu_queuing; Owner: -
--

ALTER TABLE ONLY gpu_queuing.gpu_task_queue
    ADD CONSTRAINT gpu_task_queue_pkey PRIMARY KEY (task_id);


--
-- Name: analysis_configs analysis_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_configs
    ADD CONSTRAINT analysis_configs_pkey PRIMARY KEY (id);


--
-- Name: analysis_configs analysis_configs_unique_type_name_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_configs
    ADD CONSTRAINT analysis_configs_unique_type_name_version UNIQUE (analysis_type, name, version);


--
-- Name: analysis_results analysis_results_job_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_job_id_key UNIQUE (job_id);


--
-- Name: analysis_results analysis_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_pkey PRIMARY KEY (id);


--
-- Name: analysis_results analysis_results_ytid_config_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_ytid_config_id_key UNIQUE (ytid, config_id);


--
-- Name: analysis_subchunks analysis_subchunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_subchunks
    ADD CONSTRAINT analysis_subchunks_pkey PRIMARY KEY (ytid, subchunk_id);


--
-- Name: analysis_tasks analysis_tasks_job_id_ytid_chunk_id_pass_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_tasks
    ADD CONSTRAINT analysis_tasks_job_id_ytid_chunk_id_pass_id_key UNIQUE (job_id, ytid, chunk_id, pass_id);


--
-- Name: analysis_tasks analysis_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_tasks
    ADD CONSTRAINT analysis_tasks_pkey PRIMARY KEY (task_id);


--
-- Name: analyzed_segments analyzed_segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analyzed_segments
    ADD CONSTRAINT analyzed_segments_pkey PRIMARY KEY (id);


--
-- Name: analyzed_segments analyzed_segments_ytid_chunk_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analyzed_segments
    ADD CONSTRAINT analyzed_segments_ytid_chunk_id_key UNIQUE (ytid, chunk_id);


--
-- Name: assets assets_path_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_path_key UNIQUE (path);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: target_spans conflict_spans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.target_spans
    ADD CONSTRAINT conflict_spans_pkey PRIMARY KEY (id);


--
-- Name: diarized_timestamps diarized_timestamps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diarized_timestamps
    ADD CONSTRAINT diarized_timestamps_pkey PRIMARY KEY (id);


--
-- Name: diarized_timestamps diarized_timestamps_ytid_speaker_name_start_sec_end_sec_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diarized_timestamps
    ADD CONSTRAINT diarized_timestamps_ytid_speaker_name_start_sec_end_sec_key UNIQUE (ytid, speaker_name, start_sec, end_sec);


--
-- Name: drill_dependencies drill_dependencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drill_dependencies
    ADD CONSTRAINT drill_dependencies_pkey PRIMARY KEY (drill_id, depends_on_id);


--
-- Name: drills drills_name_config_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drills
    ADD CONSTRAINT drills_name_config_id_key UNIQUE (name, config_id);


--
-- Name: drills drills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drills
    ADD CONSTRAINT drills_pkey PRIMARY KEY (id);


--
-- Name: hits hits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hits
    ADD CONSTRAINT hits_pkey PRIMARY KEY (id);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (job_id);


--
-- Name: jobs_test jobs_test_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs_test
    ADD CONSTRAINT jobs_test_pkey PRIMARY KEY (job_id);


--
-- Name: person_spans person_spans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_spans
    ADD CONSTRAINT person_spans_pkey PRIMARY KEY (id);


--
-- Name: quickclip_clips quickclip_clips_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quickclip_clips
    ADD CONSTRAINT quickclip_clips_pkey PRIMARY KEY (clip_id);


--
-- Name: quickclip_sessions quickclip_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quickclip_sessions
    ADD CONSTRAINT quickclip_sessions_pkey PRIMARY KEY (session_id);


--
-- Name: analyzed_segitems segment_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analyzed_segitems
    ADD CONSTRAINT segment_items_pkey PRIMARY KEY (id);


--
-- Name: topic_spans topic_spans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topic_spans
    ADD CONSTRAINT topic_spans_pkey PRIMARY KEY (id);


--
-- Name: transcribe_fragments transcribe_fragments_job_id_fragment_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_fragments
    ADD CONSTRAINT transcribe_fragments_job_id_fragment_index_key UNIQUE (job_id, fragment_index);


--
-- Name: transcribe_fragments transcribe_fragments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_fragments
    ADD CONSTRAINT transcribe_fragments_pkey PRIMARY KEY (id);


--
-- Name: transcribe_job_log transcribe_job_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_job_log
    ADD CONSTRAINT transcribe_job_log_pkey PRIMARY KEY (id);


--
-- Name: transcribe_jobs transcribe_jobs_job_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_jobs
    ADD CONSTRAINT transcribe_jobs_job_id_key UNIQUE (job_id);


--
-- Name: transcribe_jobs transcribe_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_jobs
    ADD CONSTRAINT transcribe_jobs_pkey PRIMARY KEY (id);


--
-- Name: transcribe_retry_queue transcribe_retry_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_retry_queue
    ADD CONSTRAINT transcribe_retry_queue_pkey PRIMARY KEY (id);


--
-- Name: transcribe_retry_queue transcribe_retry_queue_retry_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_retry_queue
    ADD CONSTRAINT transcribe_retry_queue_retry_id_key UNIQUE (retry_id);


--
-- Name: transcribe_workers transcribe_workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_workers
    ADD CONSTRAINT transcribe_workers_pkey PRIMARY KEY (id);


--
-- Name: transcribe_workers transcribe_workers_worker_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcribe_workers
    ADD CONSTRAINT transcribe_workers_worker_id_key UNIQUE (worker_id);


--
-- Name: transcripts transcripts_path_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_path_key UNIQUE (path);


--
-- Name: transcripts transcripts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_pkey PRIMARY KEY (id);


--
-- Name: transcripts transcripts_ytid_kind_lang_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_ytid_kind_lang_key UNIQUE (ytid, kind, lang);


--
-- Name: video_analysis video_analysis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_analysis
    ADD CONSTRAINT video_analysis_pkey PRIMARY KEY (id);


--
-- Name: video_analysis video_analysis_ytid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_analysis
    ADD CONSTRAINT video_analysis_ytid_key UNIQUE (ytid);


--
-- Name: video_terms video_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_terms
    ADD CONSTRAINT video_terms_pkey PRIMARY KEY (id);


--
-- Name: video_terms video_terms_ytid_term_type_normalized_term_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_terms
    ADD CONSTRAINT video_terms_ytid_term_type_normalized_term_key UNIQUE (ytid, term_type, normalized_term);


--
-- Name: videos videos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.videos
    ADD CONSTRAINT videos_pkey PRIMARY KEY (ytid);


--
-- Name: videos videos_url_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.videos
    ADD CONSTRAINT videos_url_key UNIQUE (url);


--
-- Name: words words_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.words
    ADD CONSTRAINT words_pkey PRIMARY KEY (id);


--
-- Name: words words_ytid_source_idx_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.words
    ADD CONSTRAINT words_ytid_source_idx_key UNIQUE (ytid, source, idx);


--
-- Name: workers workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workers
    ADD CONSTRAINT workers_pkey PRIMARY KEY (worker_id);


--
-- Name: workers_test workers_test_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workers_test
    ADD CONSTRAINT workers_test_pkey PRIMARY KEY (worker_id);


--
-- Name: idx_gpu_task_queue_lease; Type: INDEX; Schema: gpu_queuing; Owner: -
--

CREATE INDEX idx_gpu_task_queue_lease ON gpu_queuing.gpu_task_queue USING btree (lease_expires_at);


--
-- Name: idx_gpu_task_queue_status_priority; Type: INDEX; Schema: gpu_queuing; Owner: -
--

CREATE INDEX idx_gpu_task_queue_status_priority ON gpu_queuing.gpu_task_queue USING btree (status, priority DESC, created_at);


--
-- Name: analysis_subchunks_by_video; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX analysis_subchunks_by_video ON public.analysis_subchunks USING btree (ytid);


--
-- Name: analyzed_segitems_by_segment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX analyzed_segitems_by_segment ON public.analyzed_segitems USING btree (segment_id);


--
-- Name: analyzed_segitems_norm_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX analyzed_segitems_norm_trgm ON public.analyzed_segitems USING gin (normalized public.gin_trgm_ops) WHERE ((item_type = ANY (ARRAY['person'::text, 'topic'::text, 'category'::text])) AND (normalized IS NOT NULL));


--
-- Name: analyzed_segitems_unique_norm; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX analyzed_segitems_unique_norm ON public.analyzed_segitems USING btree (segment_id, item_type, normalized) WHERE ((item_type = ANY (ARRAY['person'::text, 'topic'::text, 'category'::text])) AND (normalized IS NOT NULL));


--
-- Name: analyzed_segitems_unique_order; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX analyzed_segitems_unique_order ON public.analyzed_segitems USING btree (segment_id, item_type, item_order) WHERE ((item_type = ANY (ARRAY['key_point'::text, 'quote'::text])) AND (item_order IS NOT NULL));


--
-- Name: analyzed_segitems_value_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX analyzed_segitems_value_fts ON public.analyzed_segitems USING gin (to_tsvector('english'::regconfig, value)) WHERE (item_type = ANY (ARRAY['quote'::text, 'key_point'::text]));


--
-- Name: analyzed_segments_by_sentiment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX analyzed_segments_by_sentiment ON public.analyzed_segments USING btree (sentiment) WHERE (sentiment IS NOT NULL);


--
-- Name: analyzed_segments_by_video; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX analyzed_segments_by_video ON public.analyzed_segments USING btree (ytid);


--
-- Name: analyzed_segments_summary_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX analyzed_segments_summary_fts ON public.analyzed_segments USING gin (to_tsvector('english'::regconfig, summary));


--
-- Name: diarized_by_speaker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX diarized_by_speaker ON public.diarized_timestamps USING btree (speaker_name);


--
-- Name: diarized_by_video_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX diarized_by_video_start ON public.diarized_timestamps USING btree (ytid, start_sec);


--
-- Name: drill_deps_by_dependency; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX drill_deps_by_dependency ON public.drill_dependencies USING btree (depends_on_id);


--
-- Name: drill_deps_by_drill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX drill_deps_by_drill ON public.drill_dependencies USING btree (drill_id);


--
-- Name: drills_by_config; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX drills_by_config ON public.drills USING btree (config_id) WHERE (config_id IS NOT NULL);


--
-- Name: drills_global; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX drills_global ON public.drills USING btree (name) WHERE (config_id IS NULL);


--
-- Name: hits_by_video_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX hits_by_video_start ON public.hits USING btree (ytid, start_sec);


--
-- Name: hits_unique_span_label; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX hits_unique_span_label ON public.hits USING btree (ytid, start_sec, end_sec, COALESCE(label, ''::text));


--
-- Name: idx_jobs_claimed_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_claimed_by ON public.jobs USING btree (claimed_by) WHERE (claimed_by IS NOT NULL);


--
-- Name: idx_jobs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_created_at ON public.jobs USING btree (created_at DESC);


--
-- Name: idx_jobs_job_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_job_type ON public.jobs USING btree (job_type);


--
-- Name: idx_jobs_queue_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_queue_claim ON public.jobs USING btree (status, priority DESC, created_at) WHERE (status = 'pending'::text);


--
-- Name: idx_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_status ON public.jobs USING btree (status);


--
-- Name: idx_jobs_ytid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_ytid ON public.jobs USING btree (ytid) WHERE (ytid IS NOT NULL);


--
-- Name: idx_quickclip_clips_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quickclip_clips_session ON public.quickclip_clips USING btree (session_id);


--
-- Name: idx_quickclip_clips_ytid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quickclip_clips_ytid ON public.quickclip_clips USING btree (ytid);


--
-- Name: idx_quickclip_sessions_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quickclip_sessions_created_at ON public.quickclip_sessions USING btree (created_at DESC);


--
-- Name: idx_quickclip_sessions_ytid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quickclip_sessions_ytid ON public.quickclip_sessions USING btree (ytid);


--
-- Name: idx_retry_queue_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_retry_queue_status ON public.transcribe_retry_queue USING btree (status, priority DESC, created_at);


--
-- Name: idx_transcribe_fragments_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_fragments_job ON public.transcribe_fragments USING btree (job_id);


--
-- Name: idx_transcribe_fragments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_fragments_status ON public.transcribe_fragments USING btree (job_id, status);


--
-- Name: idx_transcribe_job_log_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_job_log_event ON public.transcribe_job_log USING btree (event_type);


--
-- Name: idx_transcribe_job_log_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_job_log_job ON public.transcribe_job_log USING btree (job_id, "timestamp" DESC);


--
-- Name: idx_transcribe_job_log_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_job_log_timestamp ON public.transcribe_job_log USING btree ("timestamp" DESC);


--
-- Name: idx_transcribe_jobs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_jobs_created_at ON public.transcribe_jobs USING btree (created_at DESC);


--
-- Name: idx_transcribe_jobs_lease_recovery; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_jobs_lease_recovery ON public.transcribe_jobs USING btree (status, lease_expires_at) WHERE (status = ANY (ARRAY['claimed'::text, 'running'::text]));


--
-- Name: idx_transcribe_jobs_queue_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_jobs_queue_order ON public.transcribe_jobs USING btree (priority DESC, created_at) WHERE (status = 'pending'::text);


--
-- Name: idx_transcribe_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_jobs_status ON public.transcribe_jobs USING btree (status);


--
-- Name: idx_transcribe_jobs_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_jobs_worker ON public.transcribe_jobs USING btree (worker_id, status);


--
-- Name: idx_transcribe_jobs_ytid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_jobs_ytid ON public.transcribe_jobs USING btree (ytid) WHERE (ytid IS NOT NULL);


--
-- Name: idx_transcribe_workers_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_workers_heartbeat ON public.transcribe_workers USING btree (last_heartbeat DESC);


--
-- Name: idx_transcribe_workers_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_workers_status ON public.transcribe_workers USING btree (status) WHERE (status = ANY (ARRAY['idle'::text, 'busy'::text]));


--
-- Name: idx_transcribe_workers_type_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcribe_workers_type_status ON public.transcribe_workers USING btree (worker_type, status);


--
-- Name: idx_workers_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workers_active ON public.workers USING btree (status, worker_type) WHERE (status = ANY (ARRAY['idle'::text, 'busy'::text]));


--
-- Name: idx_workers_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workers_heartbeat ON public.workers USING btree (last_heartbeat DESC);


--
-- Name: idx_workers_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workers_status ON public.workers USING btree (status);


--
-- Name: idx_workers_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workers_type ON public.workers USING btree (worker_type);


--
-- Name: jobs_test_claimed_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_test_claimed_by_idx ON public.jobs_test USING btree (claimed_by) WHERE (claimed_by IS NOT NULL);


--
-- Name: jobs_test_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_test_created_at_idx ON public.jobs_test USING btree (created_at DESC);


--
-- Name: jobs_test_job_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_test_job_type_idx ON public.jobs_test USING btree (job_type);


--
-- Name: jobs_test_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_test_status_idx ON public.jobs_test USING btree (status);


--
-- Name: jobs_test_status_priority_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_test_status_priority_created_at_idx ON public.jobs_test USING btree (status, priority DESC, created_at) WHERE (status = 'pending'::text);


--
-- Name: jobs_test_ytid_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_test_ytid_idx ON public.jobs_test USING btree (ytid) WHERE (ytid IS NOT NULL);


--
-- Name: person_spans_by_video; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX person_spans_by_video ON public.person_spans USING btree (ytid);


--
-- Name: person_spans_norm_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX person_spans_norm_trgm ON public.person_spans USING gin (normalized_name public.gin_trgm_ops);


--
-- Name: results_by_config; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX results_by_config ON public.analysis_results USING btree (config_id);


--
-- Name: results_by_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX results_by_status ON public.analysis_results USING btree (status) WHERE (status = ANY (ARRAY['processing'::text, 'completed'::text, 'failed'::text]));


--
-- Name: results_by_ytid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX results_by_ytid ON public.analysis_results USING btree (ytid);


--
-- Name: segment_items_by_segment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX segment_items_by_segment ON public.analyzed_segitems USING btree (segment_id);


--
-- Name: segment_items_norm_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX segment_items_norm_trgm ON public.analyzed_segitems USING gin (normalized public.gin_trgm_ops) WHERE ((item_type = ANY (ARRAY['person'::text, 'topic'::text, 'category'::text])) AND (normalized IS NOT NULL));


--
-- Name: segment_items_unique_norm; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX segment_items_unique_norm ON public.analyzed_segitems USING btree (segment_id, item_type, normalized) WHERE ((item_type = ANY (ARRAY['person'::text, 'topic'::text, 'category'::text])) AND (normalized IS NOT NULL));


--
-- Name: segment_items_unique_order; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX segment_items_unique_order ON public.analyzed_segitems USING btree (segment_id, item_type, item_order) WHERE ((item_type = ANY (ARRAY['key_point'::text, 'quote'::text])) AND (item_order IS NOT NULL));


--
-- Name: segment_items_value_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX segment_items_value_fts ON public.analyzed_segitems USING gin (to_tsvector('english'::regconfig, value)) WHERE (item_type = ANY (ARRAY['quote'::text, 'key_point'::text]));


--
-- Name: target_spans_by_video; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX target_spans_by_video ON public.target_spans USING btree (ytid);


--
-- Name: target_spans_parties_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX target_spans_parties_gin ON public.target_spans USING gin (parties);


--
-- Name: tasks_available_for_claiming; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tasks_available_for_claiming ON public.analysis_tasks USING btree (status, required_capabilities) WHERE (status = ANY (ARRAY['pending'::text, 'failed'::text]));


--
-- Name: tasks_by_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tasks_by_job ON public.analysis_tasks USING btree (job_id);


--
-- Name: tasks_by_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tasks_by_status ON public.analysis_tasks USING btree (status);


--
-- Name: tasks_by_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tasks_by_worker ON public.analysis_tasks USING btree (claimed_by) WHERE (claimed_by IS NOT NULL);


--
-- Name: topic_spans_by_video; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX topic_spans_by_video ON public.topic_spans USING btree (ytid);


--
-- Name: topic_spans_norm_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX topic_spans_norm_trgm ON public.topic_spans USING gin (normalized_topic public.gin_trgm_ops);


--
-- Name: video_analysis_by_sentiment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX video_analysis_by_sentiment ON public.video_analysis USING btree (dominant_sentiment) WHERE (dominant_sentiment IS NOT NULL);


--
-- Name: video_analysis_hashtags; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX video_analysis_hashtags ON public.video_analysis USING gin (hashtags);


--
-- Name: video_analysis_summary_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX video_analysis_summary_fts ON public.video_analysis USING gin (to_tsvector('english'::regconfig, summary_paragraph));


--
-- Name: video_terms_by_count; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX video_terms_by_count ON public.video_terms USING btree (term_count DESC);


--
-- Name: video_terms_by_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX video_terms_by_type ON public.video_terms USING btree (term_type);


--
-- Name: video_terms_by_video; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX video_terms_by_video ON public.video_terms USING btree (ytid);


--
-- Name: video_terms_norm_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX video_terms_norm_trgm ON public.video_terms USING gin (normalized_term public.gin_trgm_ops);


--
-- Name: words_by_video_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX words_by_video_idx ON public.words USING btree (ytid, idx);


--
-- Name: words_by_video_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX words_by_video_start ON public.words USING btree (ytid, start_sec);


--
-- Name: words_source_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX words_source_idx ON public.words USING btree (source);


--
-- Name: words_word_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX words_word_trgm ON public.words USING gin (word public.gin_trgm_ops);


--
-- Name: workers_test_last_heartbeat_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX workers_test_last_heartbeat_idx ON public.workers_test USING btree (last_heartbeat DESC);


--
-- Name: workers_test_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX workers_test_status_idx ON public.workers_test USING btree (status);


--
-- Name: workers_test_status_worker_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX workers_test_status_worker_type_idx ON public.workers_test USING btree (status, worker_type) WHERE (status = ANY (ARRAY['idle'::text, 'busy'::text]));


--
-- Name: workers_test_worker_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX workers_test_worker_type_idx ON public.workers_test USING btree (worker_type);


--
-- Name: analysis_tasks analysis_tasks_update_timestamp; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER analysis_tasks_update_timestamp BEFORE UPDATE ON public.analysis_tasks FOR EACH ROW EXECUTE FUNCTION public.update_analysis_task_timestamp();


--
-- Name: drills drills_update_timestamp; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER drills_update_timestamp BEFORE UPDATE ON public.drills FOR EACH ROW EXECUTE FUNCTION public.update_drill_timestamp();


--
-- Name: jobs trigger_update_jobs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_update_jobs_updated_at BEFORE UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.update_jobs_updated_at();


--
-- Name: videos update_videos_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_videos_updated_at BEFORE UPDATE ON public.videos FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: analysis_results analysis_results_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_config_id_fkey FOREIGN KEY (config_id) REFERENCES public.analysis_configs(id) ON DELETE CASCADE;


--
-- Name: analysis_results analysis_results_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: analysis_subchunks analysis_subchunks_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_subchunks
    ADD CONSTRAINT analysis_subchunks_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: analysis_tasks analysis_tasks_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_tasks
    ADD CONSTRAINT analysis_tasks_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: analyzed_segments analyzed_segments_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analyzed_segments
    ADD CONSTRAINT analyzed_segments_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: assets assets_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: target_spans conflict_spans_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.target_spans
    ADD CONSTRAINT conflict_spans_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: diarized_timestamps diarized_timestamps_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diarized_timestamps
    ADD CONSTRAINT diarized_timestamps_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: drill_dependencies drill_dependencies_depends_on_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drill_dependencies
    ADD CONSTRAINT drill_dependencies_depends_on_id_fkey FOREIGN KEY (depends_on_id) REFERENCES public.drills(id) ON DELETE RESTRICT;


--
-- Name: drill_dependencies drill_dependencies_drill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drill_dependencies
    ADD CONSTRAINT drill_dependencies_drill_id_fkey FOREIGN KEY (drill_id) REFERENCES public.drills(id) ON DELETE CASCADE;


--
-- Name: drills drills_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drills
    ADD CONSTRAINT drills_config_id_fkey FOREIGN KEY (config_id) REFERENCES public.analysis_configs(id) ON DELETE CASCADE;


--
-- Name: workers fk_workers_current_job; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workers
    ADD CONSTRAINT fk_workers_current_job FOREIGN KEY (current_job_id) REFERENCES public.jobs(job_id) ON DELETE SET NULL;


--
-- Name: hits hits_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hits
    ADD CONSTRAINT hits_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: person_spans person_spans_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_spans
    ADD CONSTRAINT person_spans_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: quickclip_clips quickclip_clips_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quickclip_clips
    ADD CONSTRAINT quickclip_clips_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.quickclip_sessions(session_id) ON DELETE CASCADE;


--
-- Name: quickclip_clips quickclip_clips_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quickclip_clips
    ADD CONSTRAINT quickclip_clips_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid);


--
-- Name: quickclip_sessions quickclip_sessions_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quickclip_sessions
    ADD CONSTRAINT quickclip_sessions_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid);


--
-- Name: analyzed_segitems segment_items_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analyzed_segitems
    ADD CONSTRAINT segment_items_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.analyzed_segments(id) ON DELETE CASCADE;


--
-- Name: topic_spans topic_spans_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topic_spans
    ADD CONSTRAINT topic_spans_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: transcripts transcripts_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: video_analysis video_analysis_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_analysis
    ADD CONSTRAINT video_analysis_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: video_terms video_terms_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_terms
    ADD CONSTRAINT video_terms_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- Name: words words_ytid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.words
    ADD CONSTRAINT words_ytid_fkey FOREIGN KEY (ytid) REFERENCES public.videos(ytid) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 5RbqD1IECcbr9rmpF3BPRMZejM1ORNobFReXJqxFzo8IaGanagfLXuFheK7UtfL

