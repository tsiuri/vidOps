#!/usr/bin/env python3
"""
Database storage module for analysis results.
Stores analyzed transcript segments and aggregated results in PostgreSQL.
"""

import sys
from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import execute_values, Json
    from psycopg2 import sql as _sql
except ImportError:
    print("Error: 'psycopg2' library not found. Install with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


class AnalysisDatabase:
    """Handles database storage of analysis results"""

    def __init__(self, dbname: str = "transcripts", host: str = "localhost",
                 port: int = 5432, user: str = None, password: str = None):
        """Initialize database connection"""
        self.conn_params = {
            'dbname': dbname,
            'host': host,
            'port': port
        }
        if user:
            self.conn_params['user'] = user
        if password:
            self.conn_params['password'] = password

        self.conn = None
        self.cursor = None

    def connect(self):
        """Establish database connection"""
        try:
            self.conn = psycopg2.connect(**self.conn_params)
            self.cursor = self.conn.cursor()
        except psycopg2.Error as e:
            print(f"Database connection error: {e}", file=sys.stderr)
            raise

    def disconnect(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()

    def has_diarization(self, ytid: str) -> bool:
        """Check if a video has diarization data."""
        try:
            self.cursor.execute(
                "SELECT 1 FROM diarized_timestamps WHERE ytid=%s LIMIT 1",
                (ytid,),
            )
            return self.cursor.fetchone() is not None
        except Exception:
            return False

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.disconnect()

    def ensure_video_exists(self, ytid: str, metadata: Dict[str, Any]) -> None:
        """Ensure video record exists in videos table"""
        self.cursor.execute("""
            INSERT INTO videos (ytid, title, title_date, url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (ytid) DO UPDATE
            SET title = EXCLUDED.title,
                title_date = EXCLUDED.title_date,
                url = COALESCE(EXCLUDED.url, videos.url)
        """, (
            ytid,
            metadata.get('title'),
            metadata.get('date') if metadata.get('date') != 'unknown' else None,
            f"https://youtube.com/watch?v={ytid}"
        ))

    def store_subchunks(self, ytid: str, subchunks: List[Dict[str, Any]]) -> None:
        """Persist VTT-derived subchunks for timing drills."""
        if not subchunks:
            return
        rows = []
        for sc in subchunks:
            sub_id = sc.get("cue_id")
            if sub_id is None:
                continue
            try:
                sub_id = int(sub_id)
            except Exception:
                continue
            rows.append((
                ytid,
                sub_id,
                sc.get("chunk_id"),
                sc.get("start_sec"),
                sc.get("end_sec"),
            ))
        if not rows:
            return
        execute_values(
            self.cursor,
            """
            INSERT INTO analysis_subchunks (ytid, subchunk_id, chunk_id, start_sec, end_sec)
            VALUES %s
            ON CONFLICT (ytid, subchunk_id) DO UPDATE
            SET chunk_id = EXCLUDED.chunk_id,
                start_sec = EXCLUDED.start_sec,
                end_sec = EXCLUDED.end_sec
            """,
            rows,
        )

    def store_segment_analysis(self, ytid: str, chunk_id: int, analysis: Dict[str, Any],
                               model: str = "llama3.2", batch_id: int | None = None,
                               analysis_type: str = "normal", request_text: str | None = None,
                               diarized: bool | None = None, transcription_machine: str | None = None) -> int:
        """
        Store a single segment analysis and return segment_id.
        Returns the database ID of the inserted segment.
        """
        # Insert segment
        self.cursor.execute("""
            INSERT INTO analyzed_segments
                (ytid, chunk_id, word_count, sentiment, summary, model, batch_id, analysis_type, request_text, diarized, transcription_machine, start_sec, end_sec)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ytid, chunk_id) DO UPDATE
            SET word_count = EXCLUDED.word_count,
                sentiment = EXCLUDED.sentiment,
                summary = EXCLUDED.summary,
                model = EXCLUDED.model,
                batch_id = EXCLUDED.batch_id,
                analysis_type = EXCLUDED.analysis_type,
                request_text = EXCLUDED.request_text,
                diarized = EXCLUDED.diarized,
                transcription_machine = EXCLUDED.transcription_machine,
                start_sec = EXCLUDED.start_sec,
                end_sec = EXCLUDED.end_sec,
                analyzed_at = now()
            RETURNING id
        """, (
            ytid,
            chunk_id,
            analysis.get('word_count', 0),
            analysis.get('sentiment', 'unknown'),
            analysis.get('summary', ''),
            model,
            batch_id,
            analysis_type,
            request_text,
            bool(diarized) if diarized is not None else False,
            transcription_machine,
            analysis.get('start_sec'),
            analysis.get('end_sec')
        ))
        segment_id = self.cursor.fetchone()[0]

        # Delete existing related records for this segment (for idempotent updates)
        try:
            self.cursor.execute("DELETE FROM analyzed_segitems WHERE segment_id = %s", (segment_id,))
        except Exception:
            # fallback for legacy schemas
            for table in ['segment_items','segment_people', 'segment_topics', 'segment_key_points',
                          'segment_quotes', 'segment_categories']:
                try:
                    self.cursor.execute(f"DELETE FROM {table} WHERE segment_id = %s", (segment_id,))
                except Exception:
                    pass

        # Consolidated storage into segment_items
        # People
        people = analysis.get('people', [])
        if people:
            vals = [(segment_id, 'person', name, name.lower().strip(), None)
                    for name in people if name]
            if vals:
                try:
                    execute_values(
                        self.cursor,
                        "INSERT INTO analyzed_segitems (segment_id, item_type, value, normalized, item_order) VALUES %s",
                        vals
                    )
                except Exception:
                    execute_values(
                        self.cursor,
                        "INSERT INTO segment_items (segment_id, item_type, value, normalized, item_order) VALUES %s",
                        vals
                    )
        # Topics
        topics = analysis.get('topics', [])
        if topics:
            vals = [(segment_id, 'topic', topic, topic.lower().strip(), None)
                    for topic in topics if topic]
            if vals:
                try:
                    execute_values(
                        self.cursor,
                        "INSERT INTO analyzed_segitems (segment_id, item_type, value, normalized, item_order) VALUES %s",
                        vals
                    )
                except Exception:
                    execute_values(
                        self.cursor,
                        "INSERT INTO segment_items (segment_id, item_type, value, normalized, item_order) VALUES %s",
                        vals
                    )
        # Categories
        categories = analysis.get('categories', [])
        if categories:
            vals = [(segment_id, 'category', cat, cat.lower().strip(), None)
                    for cat in categories if cat]
            if vals:
                try:
                    execute_values(
                        self.cursor,
                        "INSERT INTO analyzed_segitems (segment_id, item_type, value, normalized, item_order) VALUES %s",
                        vals
                    )
                except Exception:
                    execute_values(
                        self.cursor,
                        "INSERT INTO segment_items (segment_id, item_type, value, normalized, item_order) VALUES %s",
                        vals
                    )
        # Key points
        key_points = analysis.get('key_points', [])
        if key_points:
            vals = [(segment_id, 'key_point', point, None, idx)
                    for idx, point in enumerate(key_points) if point]
            if vals:
                try:
                    execute_values(
                        self.cursor,
                        "INSERT INTO analyzed_segitems (segment_id, item_type, value, normalized, item_order) VALUES %s",
                        vals
                    )
                except Exception:
                    execute_values(
                        self.cursor,
                        "INSERT INTO segment_items (segment_id, item_type, value, normalized, item_order) VALUES %s",
                        vals
                    )
        # Quotes
        quotes = analysis.get('notable_quotes', [])
        if quotes:
            vals = [(segment_id, 'quote', quote, None, idx)
                    for idx, quote in enumerate(quotes) if quote]
            if vals:
                execute_values(
                    self.cursor,
                    "INSERT INTO segment_items (segment_id, item_type, value, normalized, item_order) VALUES %s",
                    vals
                )

        return segment_id

    def store_video_analysis(self, ytid: str, analysis_data: Dict[str, Any],
                            model: str = "llama3.2", batch_id: int | None = None,
                            analysis_type: str = "normal", request_text: str | None = None,
                            diarized: bool | None = None, transcription_machine: str | None = None) -> None:
        """Store aggregated video-level analysis"""
        analysis = analysis_data.get('analysis', {})
        stats = analysis_data.get('stats', {})
        summaries = analysis_data.get('summaries', {})

        # Store video analysis summary
        self.cursor.execute("""
            INSERT INTO video_analysis
                (ytid, total_chunks, dominant_sentiment, tldr_one_sentence,
                 summary_paragraph, model, batch_id, analysis_type, request_text,
                 political_overview, controversies, issues, noteworthy_statements, personal_conflicts,
                 speaker_overview, personal_themes, diarized, transcription_machine)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ytid) DO UPDATE
            SET total_chunks = EXCLUDED.total_chunks,
                dominant_sentiment = EXCLUDED.dominant_sentiment,
                tldr_one_sentence = EXCLUDED.tldr_one_sentence,
                summary_paragraph = EXCLUDED.summary_paragraph,
                model = EXCLUDED.model,
                batch_id = EXCLUDED.batch_id,
                analysis_type = EXCLUDED.analysis_type,
                request_text = EXCLUDED.request_text,
                political_overview = EXCLUDED.political_overview,
                controversies = EXCLUDED.controversies,
                issues = EXCLUDED.issues,
                noteworthy_statements = EXCLUDED.noteworthy_statements,
                personal_conflicts = EXCLUDED.personal_conflicts,
                speaker_overview = EXCLUDED.speaker_overview,
                personal_themes = EXCLUDED.personal_themes,
                diarized = EXCLUDED.diarized,
                transcription_machine = EXCLUDED.transcription_machine,
                analyzed_at = now()
        """, (
            ytid,
            stats.get('total_chunks', 0),
            analysis.get('dominant_sentiment', 'unknown'),
            summaries.get('tldr_one_sentence', ''),
            summaries.get('summary_paragraph', ''),
            model,
            batch_id,
            analysis_type,
            request_text or 'none',
            summaries.get('political_overview', ''),
            summaries.get('controversies', ''),
            summaries.get('issues', []),
            summaries.get('noteworthy_statements', []),
            summaries.get('personal_conflicts', []),
            summaries.get('speaker_overview', ''),
            summaries.get('personal_themes', []),
            bool(diarized) if diarized is not None else False,
            transcription_machine
        ))

        # Wipe vestigial fields if they exist (backward compatibility)
        try:
            self.cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='video_analysis'
                  AND column_name IN ('social_caption','hashtags')
                """
            )
            cols = {r[0] for r in self.cursor.fetchall()}
            sets = []
            if 'social_caption' in cols:
                sets.append('social_caption = NULL')
            if 'hashtags' in cols:
                sets.append('hashtags = NULL')
            if sets:
                q = f"UPDATE video_analysis SET {', '.join(sets)} WHERE ytid = %s"
                self.cursor.execute(q, (ytid,))
        except Exception:
            # Ignore if table/columns differ
            pass

        # Delete existing video-level rollups
        try:
            self.cursor.execute("DELETE FROM video_terms WHERE ytid = %s AND term_type IN ('person','topic')", (ytid,))
        except Exception:
            try:
                self.cursor.execute("DELETE FROM video_people WHERE ytid = %s", (ytid,))
            except Exception:
                pass
            try:
                self.cursor.execute("DELETE FROM video_topics WHERE ytid = %s", (ytid,))
            except Exception:
                pass

        # Store video-level people mentions (deduplicate by normalized name)
        people = analysis.get('people_mentioned', [])
        if people:
            agg = {}
            for p in people:
                name = p.get('name')
                if not name:
                    continue
                norm = name.lower().strip()
                mentions = int(p.get('mentions') or 1)
                if norm in agg:
                    agg[norm]['mention_count'] += mentions
                else:
                    agg[norm] = {
                        'person_name': name,
                        'normalized_name': norm,
                        'mention_count': mentions
                    }
            people_values = [
                (ytid, v['person_name'], v['normalized_name'], v['mention_count'])
                for v in agg.values()
            ]
            if people_values:
                try:
                    vals = [(ytid, 'person', name, norm, cnt) for (_, name, norm, cnt) in people_values]
                    execute_values(
                        self.cursor,
                        "INSERT INTO video_terms (ytid, term_type, term, normalized_term, term_count) VALUES %s",
                        vals
                    )
                except Exception:
                    execute_values(
                        self.cursor,
                        """INSERT INTO video_people
                           (ytid, person_name, normalized_name, mention_count) VALUES %s
                           ON CONFLICT (ytid, normalized_name) DO UPDATE
                           SET mention_count = EXCLUDED.mention_count""",
                        people_values
                    )

        # Store video-level topics (deduplicate by normalized topic)
        topics = analysis.get('topics', [])
        if topics:
            agg_t = {}
            for t in topics:
                topic = t.get('topic')
                if not topic:
                    continue
                norm = topic.lower().strip()
                occ = int(t.get('occurrences') or 1)
                if norm in agg_t:
                    agg_t[norm]['occurrence_count'] += occ
                else:
                    agg_t[norm] = {
                        'topic': topic,
                        'normalized_topic': norm,
                        'occurrence_count': occ
                    }
            topics_values = [
                (ytid, v['topic'], v['normalized_topic'], v['occurrence_count'])
                for v in agg_t.values()
            ]
            if topics_values:
                try:
                    vals = [(ytid, 'topic', topic, norm, occ) for (_, topic, norm, occ) in topics_values]
                    execute_values(
                        self.cursor,
                        "INSERT INTO video_terms (ytid, term_type, term, normalized_term, term_count) VALUES %s",
                        vals
                    )
                except Exception:
                    execute_values(
                        self.cursor,
                        """INSERT INTO video_topics
                           (ytid, topic, normalized_topic, occurrence_count) VALUES %s
                           ON CONFLICT (ytid, normalized_topic) DO UPDATE
                           SET occurrence_count = EXCLUDED.occurrence_count""",
                        topics_values
                    )

    def store_full_analysis(self, analysis_result: Dict[str, Any], model: str = "llama3.2",
                            batch_id: int | None = None, analysis_type: str = "normal",
                            request_text: str | None = None, diarized: bool | None = None,
                            transcription_machine: str | None = None) -> None:
        """
        Store complete analysis result (both segments and aggregated video analysis).
        This is the main entry point for storing analysis results.
        """
        metadata = analysis_result.get('metadata', {})
        ytid = metadata.get('video_id')

        if not ytid:
            raise ValueError("Missing video_id in analysis result")

        # Ensure video exists
        self.ensure_video_exists(ytid, metadata)

        # Store chunk summaries (segment-level data)
        chunk_summaries = analysis_result.get('chunk_summaries', [])
        for chunk_data in chunk_summaries:
            chunk_id = chunk_data.get('chunk_id', 0)
            # Note: chunk_data from aggregate doesn't have full analysis
            # If you want segment details, pass chunk_analyses separately
            # For now, store minimal data from chunk_summaries
            self.cursor.execute("""
                INSERT INTO analyzed_segments
                    (ytid, chunk_id, word_count, sentiment, summary, model, batch_id, analysis_type, request_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ytid, chunk_id) DO UPDATE
                SET word_count = EXCLUDED.word_count,
                    sentiment = EXCLUDED.sentiment,
                    summary = EXCLUDED.summary,
                    model = EXCLUDED.model,
                    batch_id = EXCLUDED.batch_id,
                    analysis_type = EXCLUDED.analysis_type,
                    request_text = EXCLUDED.request_text,
                    analyzed_at = now()
            """, (
                ytid,
                chunk_id,
                chunk_data.get('word_count', 0),
                chunk_data.get('sentiment', 'unknown'),
                chunk_data.get('summary', ''),
                model,
                batch_id,
                analysis_type,
                request_text
            ))

        # Store aggregated video analysis
        self.store_video_analysis(ytid, analysis_result, model,
                                  batch_id=batch_id,
                                  analysis_type=analysis_type,
                                  request_text=request_text,
                                  diarized=diarized,
                                  transcription_machine=transcription_machine)
        # Commit to persist changes when not using context manager
        if self.conn:
            self.conn.commit()

    def store_spans(self, ytid: str, spans: Dict[str, List[Dict[str, Any]]],
                    source_pass: str = "chunk_analysis", pass_tier: str = "initial",
                    prompt_version: str | None = None, analysis_type: str = "normal",
                    batch_id: int | None = None, diarized: bool | None = None,
                    transcription_machine: str | None = None) -> None:
        """
        Store topic and person spans extracted from chunk analyses.
        Spans group consecutive chunks mentioning the same entity.
        """
        # Delete existing spans for this video (idempotent updates)
        try:
            self.cursor.execute("DELETE FROM topic_spans WHERE ytid = %s", (ytid,))
            self.cursor.execute("DELETE FROM person_spans WHERE ytid = %s", (ytid,))
        except Exception:
            # Ignore if tables don't exist in older schemas
            pass

        # Store topic spans
        topic_spans = spans.get("topic_spans", [])
        if topic_spans:
            for span in topic_spans:
                try:
                    self.cursor.execute("""
                        INSERT INTO topic_spans
                            (ytid, topic, normalized_topic, start_sec, end_sec, chunk_ids,
                             context, sentiment, source_pass, pass_tier, prompt_version,
                             analysis_type, batch_id, diarized, transcription_machine)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        ytid,
                        span.get("topic"),
                        span.get("normalized_topic"),
                        span.get("start_sec"),  # NULL for now (no timing data yet)
                        span.get("end_sec"),    # NULL for now
                        span.get("chunk_ids", []),
                        span.get("context"),
                        span.get("sentiment"),
                        source_pass,
                        pass_tier,
                        prompt_version,
                        analysis_type,
                        batch_id,
                        bool(diarized) if diarized is not None else False,
                        transcription_machine
                    ))
                except Exception as e:
                    print(f"Warning: failed to insert topic span for '{span.get('topic')}': {e}", file=sys.stderr)

        # Store person spans
        person_spans = spans.get("person_spans", [])
        if person_spans:
            for span in person_spans:
                try:
                    self.cursor.execute("""
                        INSERT INTO person_spans
                            (ytid, person_name, normalized_name, start_sec, end_sec, chunk_ids,
                             context, sentiment, polarity, source_pass, pass_tier, prompt_version,
                             analysis_type, batch_id, diarized, transcription_machine)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        ytid,
                        span.get("person_name"),
                        span.get("normalized_name"),
                        span.get("start_sec"),  # NULL for now (no timing data yet)
                        span.get("end_sec"),    # NULL for now
                        span.get("chunk_ids", []),
                        span.get("context"),
                        span.get("sentiment"),
                        span.get("polarity"),   # NULL for now (not extracted yet)
                        source_pass,
                        pass_tier,
                        prompt_version,
                        analysis_type,
                        batch_id,
                        bool(diarized) if diarized is not None else False,
                        transcription_machine
                    ))
                except Exception as e:
                    print(f"Warning: failed to insert person span for '{span.get('person_name')}': {e}", file=sys.stderr)

    def store_target_spans(self, ytid: str, spans: List[Dict[str, Any]],
                           source_pass: str = "hot_target", pass_tier: str = "detail",
                           prompt_version: str | None = None, analysis_type: str = "normal",
                           batch_id: int | None = None, diarized: bool | None = None,
                           transcription_machine: str | None = None) -> None:
        """
        Store generic target spans (formerly conflict spans).
        """
        # Delete existing spans for this video (idempotent updates)
        try:
            self.cursor.execute("DELETE FROM target_spans WHERE ytid = %s", (ytid,))
        except Exception:
            # Ignore if table doesn't exist in older schemas
            pass

        # Store spans
        if not spans:
            return

        for span in spans:
            try:
                # Get parties array, ensure it's not empty before storing
                parties = span.get("parties")
                if parties and not isinstance(parties, list):
                    parties = [str(parties)]
                if not parties or (isinstance(parties, list) and not parties):
                    parties = None  # Store NULL instead of empty array

                self.cursor.execute("""
                    INSERT INTO target_spans
                        (ytid, parties, description, start_sec, end_sec, chunk_ids,
                         context, sentiment, polarity, source_pass, pass_tier, prompt_version,
                         analysis_type, batch_id, diarized, transcription_machine)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ytid,
                    parties,
                    span.get("description"),
                    span.get("start_sec"),
                    span.get("end_sec"),
                    span.get("chunk_ids", []),
                    span.get("context"),
                    span.get("sentiment"),
                    span.get("polarity"),
                    span.get("target_name") or source_pass,  # Use target_name if available
                    pass_tier,
                    prompt_version,
                    analysis_type,
                    batch_id,
                    bool(diarized) if diarized is not None else False,
                    transcription_machine
                ))
            except Exception as e:
                desc = span.get("description", "unknown")
                print(f"Warning: failed to insert target span '{desc}': {e}", file=sys.stderr)

    def store_full_analysis_with_chunks(self, analysis_result: Dict[str, Any],
                                       chunk_analyses: List[Dict[str, Any]],
                                       spans: Dict[str, List[Dict[str, Any]]] | None = None,
                                       model: str = "llama3.2",
                                       batch_id: int | None = None,
                                       analysis_type: str = "normal",
                                       request_text: str | None = None,
                                       diarized: bool | None = None,
                                       transcription_machine: str | None = None,
                                       source_pass: str = "chunk_analysis",
                                       pass_tier: str = "initial",
                                       prompt_version: str | None = None) -> None:
        """
        Store complete analysis with full chunk detail and optional spans.
        Use this when you have the original chunk_analyses list with all details.
        """
        metadata = analysis_result.get('metadata', {})
        ytid = metadata.get('video_id')

        if not ytid:
            raise ValueError("Missing video_id in analysis result")

        # Ensure video exists
        self.ensure_video_exists(ytid, metadata)

        # Store each chunk analysis with full details
        for chunk in chunk_analyses:
            per_chunk_model = chunk.get('model_used', model)
            self.store_segment_analysis(ytid, chunk.get('chunk_id', 0), chunk, per_chunk_model,
                                        batch_id=batch_id,
                                        analysis_type=analysis_type,
                                        request_text=request_text,
                                        diarized=diarized,
                                        transcription_machine=transcription_machine)

        # Store aggregated video analysis
        self.store_video_analysis(ytid, analysis_result, model,
                                  batch_id=batch_id,
                                  analysis_type=analysis_type,
                                  request_text=request_text,
                                  diarized=diarized,
                                  transcription_machine=transcription_machine)

        # Store spans if provided
        if spans:
            self.store_spans(ytid, spans,
                           source_pass=source_pass,
                           pass_tier=pass_tier,
                           prompt_version=prompt_version,
                           analysis_type=analysis_type,
                           batch_id=batch_id,
                           diarized=diarized,
                           transcription_machine=transcription_machine)

        # Commit to persist changes when not using context manager
        if self.conn:
            self.conn.commit()

    def get_analysis_status(self, ytid: str) -> Optional[Dict[str, Any]]:
        """Check if a video has been analyzed and return status"""
        self.cursor.execute("""
            SELECT
                va.total_chunks,
                va.dominant_sentiment,
                va.analyzed_at,
                va.model,
                COUNT(DISTINCT seg.id) as segments_stored
            FROM video_analysis va
            LEFT JOIN analyzed_segments seg USING (ytid)
            WHERE va.ytid = %s
            GROUP BY va.ytid, va.total_chunks, va.dominant_sentiment, va.analyzed_at, va.model
        """, (ytid,))

        row = self.cursor.fetchone()
        if row:
            return {
                'analyzed': True,
                'total_chunks': row[0],
                'dominant_sentiment': row[1],
                'analyzed_at': row[2],
                'model': row[3],
                'segments_stored': row[4]
            }
        return None

    def search_by_topic(self, topic: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Search segments by topic"""
        self.cursor.execute("""
            SELECT * FROM find_topic_segments(%s) LIMIT %s
        """, (topic, limit))

        results = []
        for row in self.cursor.fetchall():
            results.append({
                'ytid': row[0],
                'video_title': row[1],
                'video_date': row[2],
                'chunk_id': row[3],
                'summary': row[4],
                'topic': row[5]
            })
        return results

    def search_by_person(self, person: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Search segments by person mentioned"""
        self.cursor.execute("""
            SELECT * FROM find_person_segments(%s) LIMIT %s
        """, (person, limit))

        results = []
        for row in self.cursor.fetchall():
            results.append({
                'ytid': row[0],
                'video_title': row[1],
                'video_date': row[2],
                'chunk_id': row[3],
                'summary': row[4],
                'person_name': row[5]
            })
        return results

    def search_segments_fulltext(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Full-text search across segment summaries"""
        self.cursor.execute("""
            SELECT * FROM search_segments(%s) LIMIT %s
        """, (query, limit))

        results = []
        for row in self.cursor.fetchall():
            results.append({
                'ytid': row[0],
                'video_title': row[1],
                'chunk_id': row[2],
                'summary': row[3],
                'sentiment': row[4],
                'rank': row[5]
            })
        return results

    # -------------------------
    # Core videos (pre-existing)
    # -------------------------
    def list_videos(self, limit: int = 50, offset: int = 0,
                    order_by: str = "title_date DESC NULLS LAST, ytid") -> List[Dict[str, Any]]:
        """List videos from the core videos table"""
        # Basic allowed order_by guard
        allowed = {
            'title_date DESC NULLS LAST, ytid',
            'upload_date DESC NULLS LAST, ytid',
            'title ASC, ytid',
            'ytid ASC'
        }
        if order_by not in allowed:
            order_by = 'title_date DESC NULLS LAST, ytid'

        self.cursor.execute(f"""
            SELECT ytid, title, title_date, upload_date, duration_sec, channel
            FROM videos
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
        """, (limit, offset))

        rows = self.cursor.fetchall()
        return [
            {
                'ytid': r[0],
                'title': r[1],
                'title_date': str(r[2]) if r[2] else None,
                'upload_date': str(r[3]) if r[3] else None,
                'duration_sec': r[4],
                'channel': r[5]
            }
            for r in rows
        ]

    def search_videos(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search videos by ytid or title (ILIKE)"""
        like = f"%{query}%"
        self.cursor.execute(
            """
            SELECT ytid, title, title_date, upload_date, duration_sec, channel
            FROM videos
            WHERE ytid ILIKE %s OR title ILIKE %s
            ORDER BY title_date DESC NULLS LAST, ytid
            LIMIT %s
            """,
            (like, like, limit)
        )
        rows = self.cursor.fetchall()
        return [
            {
                'ytid': r[0],
                'title': r[1],
                'title_date': str(r[2]) if r[2] else None,
                'upload_date': str(r[3]) if r[3] else None,
                'duration_sec': r[4],
                'channel': r[5]
            }
            for r in rows
        ]

    def count_videos(self) -> int:
        """Return total count from videos table"""
        self.cursor.execute("SELECT COUNT(*) FROM videos")
        return int(self.cursor.fetchone()[0] or 0)

    # -------------------------------------
    # Generic DB schema + simple querying
    # -------------------------------------
    def list_tables(self, include_views: bool = False):
        """List tables (and optionally views) in the public schema"""
        if include_views:
            kinds = ('BASE TABLE','VIEW')
            self.cursor.execute(
                """
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = ANY(%s)
                ORDER BY table_name
                """,
                (list(kinds),)
            )
        else:
            self.cursor.execute(
                """
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
        return [ {'name': r[0], 'type': r[1]} for r in self.cursor.fetchall() ]

    def get_table_columns(self, table: str):
        """Return list of columns with data_type for a given public table/view"""
        self.cursor.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,)
        )
        return [ {'name': r[0], 'data_type': r[1]} for r in self.cursor.fetchall() ]

    def query_table(self, table: str, columns: list[str] | None, search: str | None,
                    limit: int = 50, order_by: str | None = None):
        """Run a simple SELECT on a whitelisted table/columns with optional text search."""
        # Introspect to build an allowlist
        cols_info = self.get_table_columns(table)
        if not cols_info:
            raise ValueError("Unknown or empty table")
        allowed_cols = [c['name'] for c in cols_info]
        text_cols = [c['name'] for c in cols_info if c['data_type'] in ('text','character varying','character')]

        sel_cols = columns or allowed_cols
        sel_cols = [c for c in sel_cols if c in allowed_cols]
        if not sel_cols:
            sel_cols = allowed_cols[:10]

        # Compose SQL safely for identifiers
        identifiers = [_sql.Identifier(c) for c in sel_cols]
        table_ident = _sql.Identifier('public', table)

        query = _sql.SQL("SELECT {fields} FROM {table}").format(
            fields=_sql.SQL(', ').join(identifiers),
            table=table_ident
        )

        params = []
        where_clauses = []
        if search:
            # ILIKE across text columns
            like = f"%{search}%"
            for c in text_cols:
                where_clauses.append(_sql.SQL("{} ILIKE %s").format(_sql.Identifier(c)))
                params.append(like)
        if where_clauses:
            query = query + _sql.SQL(" WHERE ") + _sql.SQL(" OR ").join(where_clauses)

        # Order
        if order_by and order_by in allowed_cols:
            query = query + _sql.SQL(" ORDER BY {} DESC NULLS LAST").format(_sql.Identifier(order_by))

        # Limit
        if limit is None or limit <= 0 or limit > 500:
            limit = 50
        query = query + _sql.SQL(" LIMIT %s")
        params.append(limit)

        self.cursor.execute(query, params)
        rows = self.cursor.fetchall()
        # Map to dicts
        return [ { sel_cols[i]: row[i] for i in range(len(sel_cols)) } for row in rows ]

    # ------------------------------------------------------------------
    # Analysis configuration helpers
    # ------------------------------------------------------------------

    def list_analysis_model_profiles(self) -> list[dict[str, Any]]:
        """Return all registered analysis model profiles."""
        try:
            self.cursor.execute(
                """SELECT id, model_name, options, required_vram_gb, notes, created_at, updated_at
                   FROM analysis_model_profiles
                   ORDER BY model_name, id"""
            )
        except Exception:
            return []
        rows = self.cursor.fetchall()
        cols = [desc[0] for desc in self.cursor.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_analysis_model_profile(self, profile_id: int) -> dict[str, Any] | None:
        """Fetch a single model profile row by id."""
        try:
            self.cursor.execute(
                """SELECT id, model_name, options, required_vram_gb, notes, created_at, updated_at
                   FROM analysis_model_profiles
                   WHERE id = %s""",
                (profile_id,),
            )
        except Exception:
            return None
        row = self.cursor.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in self.cursor.description]
        return dict(zip(cols, row))

    def get_analysis_model_profile_by_name_options(
        self,
        model_name: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Fetch a model profile matching model name + options."""
        opts = options or {}
        try:
            self.cursor.execute(
                """SELECT id, model_name, options, required_vram_gb, notes, created_at, updated_at
                   FROM analysis_model_profiles
                   WHERE model_name = %s AND options = %s::jsonb
                   ORDER BY id
                   LIMIT 1""",
                (model_name, Json(opts)),
            )
        except Exception:
            return None
        row = self.cursor.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in self.cursor.description]
        return dict(zip(cols, row))

    def upsert_analysis_model_profile(
        self,
        model_name: str,
        options: dict[str, Any] | None,
        required_vram_gb: float,
        notes: str | None = None,
        profile_id: int | None = None,
    ) -> int:
        """Insert or update an analysis model profile; returns profile id."""
        opts = options or {}
        if profile_id is not None:
            self.cursor.execute(
                """UPDATE analysis_model_profiles
                   SET model_name = %s,
                       options = %s::jsonb,
                       required_vram_gb = %s,
                       notes = %s,
                       updated_at = NOW()
                   WHERE id = %s
                   RETURNING id""",
                (model_name, Json(opts), required_vram_gb, notes, profile_id),
            )
            row = self.cursor.fetchone()
            return int(row[0]) if row else int(profile_id)

        self.cursor.execute(
            """INSERT INTO analysis_model_profiles (model_name, options, required_vram_gb, notes, created_at, updated_at)
               VALUES (%s, %s::jsonb, %s, %s, NOW(), NOW())
               ON CONFLICT (model_name, options) DO UPDATE
                  SET required_vram_gb = EXCLUDED.required_vram_gb,
                      notes = EXCLUDED.notes,
                      updated_at = NOW()
               RETURNING id""",
            (model_name, Json(opts), required_vram_gb, notes),
        )
        row = self.cursor.fetchone()
        return int(row[0]) if row else 0

    def list_analysis_configs(self) -> list[dict[str, Any]]:
        """Return a list of analysis configs."""
        try:
            self.cursor.execute(
                """SELECT id, name, analysis_type, version, is_default,
                          config_json, created_at, updated_at
                   FROM analysis_configs
                   ORDER BY analysis_type, name, version DESC"""
            )
        except Exception:
            # Table might not exist yet; return empty list gracefully.
            return []
        rows = self.cursor.fetchall()
        cols = [desc[0] for desc in self.cursor.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_analysis_config(self, config_id: str) -> dict[str, Any] | None:
        """Fetch a single analysis config row by id."""
        try:
            self.cursor.execute(
                """SELECT id, name, analysis_type, version, is_default, config_json,
                          created_at, updated_at
                   FROM analysis_configs
                   WHERE id = %s""", (config_id,),
            )
        except Exception:
            return None
        row = self.cursor.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in self.cursor.description]
        return dict(zip(cols, row))

    def get_default_analysis_config(self, analysis_type: str, version: int | None = None) -> dict[str, Any] | None:
        """Fetch the default (or specific-version) config for an analysis_type."""
        try:
            if version is not None:
                self.cursor.execute(
                    """SELECT id, name, analysis_type, version, is_default, config_json,
                              created_at, updated_at
                       FROM analysis_configs
                       WHERE analysis_type = %s AND version = %s
                       ORDER BY is_default DESC, updated_at DESC
                       LIMIT 1""", (analysis_type, version),
                )
            else:
                self.cursor.execute(
                    """SELECT id, name, analysis_type, version, is_default, config_json,
                              created_at, updated_at
                       FROM analysis_configs
                       WHERE analysis_type = %s
                       ORDER BY is_default DESC, updated_at DESC
                       LIMIT 1""", (analysis_type,),
                )
        except Exception:
            return None
        row = self.cursor.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in self.cursor.description]
        return dict(zip(cols, row))

    def upsert_analysis_config(
        self,
        config_id: str,
        name: str,
        analysis_type: str,
        version: int,
        config_json: dict[str, Any],
        is_default: bool = False,
    ) -> None:
        """Insert or update an AnalysisConfig row."""
        import json as _json
        payload = _json.dumps(config_json)
        self.cursor.execute(
            """INSERT INTO analysis_configs
                    (id, name, analysis_type, version, is_default, config_json, created_at, updated_at)
                 VALUES (%s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW())
                 ON CONFLICT (id) DO UPDATE
                    SET name = EXCLUDED.name,
                        analysis_type = EXCLUDED.analysis_type,
                        version = EXCLUDED.version,
                        is_default = EXCLUDED.is_default,
                        config_json = EXCLUDED.config_json,
                        updated_at = NOW()""",
            (config_id, name, analysis_type, version, is_default, payload),
        )

    # ========== Drill Management ==========

    def list_drills_for_config(self, config_id: str) -> list[dict[str, Any]]:
        """Get all drills visible to a config (global + local).

        Returns drills in order: local drills first, then global drills, all alphabetical.
        """
        self.cursor.execute(
            """
            SELECT d.id, d.name, d.description, d.prompt, d.scope, d.output_shape,
                   d.category, d.always, d.min_hits, d.keywords, d.match,
                   d.cooldown, d.detail_pass, d.config_id, d.is_local,
                   d.created_at, d.updated_at,
                   array_agg(dep_drill.name ORDER BY dep_drill.name)
                     FILTER (WHERE dep_drill.name IS NOT NULL) as depends_on
            FROM drills d
            LEFT JOIN drill_dependencies dd ON d.id = dd.drill_id
            LEFT JOIN drills dep_drill ON dd.depends_on_id = dep_drill.id
            WHERE d.config_id IS NULL OR d.config_id = %s
            GROUP BY d.id
            ORDER BY d.is_local DESC, d.name ASC
            """,
            (config_id,)
        )
        rows = self.cursor.fetchall()
        cols = [desc[0] for desc in self.cursor.description]

        drills = []
        for row in rows:
            drill = dict(zip(cols, row))
            # Parse JSONB detail_pass if it's a string
            if isinstance(drill['detail_pass'], str):
                import json as _json
                drill['detail_pass'] = _json.loads(drill['detail_pass'])
            drills.append(drill)
        return drills

    def get_drill(self, name: str, config_id: str) -> dict[str, Any] | None:
        """Get a specific drill by name (local takes precedence over global)."""
        self.cursor.execute(
            """
            SELECT d.id, d.name, d.description, d.prompt, d.scope, d.output_shape,
                   d.category, d.always, d.min_hits, d.keywords, d.match,
                   d.cooldown, d.detail_pass, d.config_id, d.is_local,
                   d.created_at, d.updated_at,
                   array_agg(dep_drill.name ORDER BY dep_drill.name)
                     FILTER (WHERE dep_drill.name IS NOT NULL) as depends_on
            FROM drills d
            LEFT JOIN drill_dependencies dd ON d.id = dd.drill_id
            LEFT JOIN drills dep_drill ON dd.depends_on_id = dep_drill.id
            WHERE d.name = %s AND (d.config_id = %s OR d.config_id IS NULL)
            GROUP BY d.id
            ORDER BY d.config_id NULLS LAST
            LIMIT 1
            """,
            (name, config_id)
        )
        row = self.cursor.fetchone()
        if not row:
            return None

        cols = [desc[0] for desc in self.cursor.description]
        drill = dict(zip(cols, row))

        if isinstance(drill['detail_pass'], str):
            import json as _json
            drill['detail_pass'] = _json.loads(drill['detail_pass'])

        return drill

    def create_drill(
        self,
        name: str,
        prompt: str,
        config_id: str | None = None,
        description: str = "",
        scope: str = "chunks",
        output_shape: str = "span",
        category: str | None = None,
        always: bool = False,
        min_hits: int = 0,
        keywords: list[str] | None = None,
        match: list[str] | None = None,
        cooldown: int = 0,
        detail_pass: dict[str, Any] | None = None,
        depends_on: list[str] | None = None
    ) -> int:
        """Create a new drill. Returns drill ID."""
        import json as _json

        # Insert drill
        self.cursor.execute(
            """
            INSERT INTO drills (
                name, description, prompt, scope, output_shape, category,
                always, min_hits, keywords, match, cooldown, detail_pass, config_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
            ) RETURNING id
            """,
            (
                name,
                description,
                prompt,
                scope,
                output_shape,
                category,
                always,
                min_hits,
                keywords or [],
                match or [],
                cooldown,
                _json.dumps(detail_pass or {}),
                config_id
            )
        )
        drill_id = self.cursor.fetchone()[0]

        # Insert dependencies
        if depends_on:
            for dep_name in depends_on:
                # Find dependency drill (local or global)
                dep_drill = self.get_drill(dep_name, config_id or '')
                if dep_drill:
                    self.cursor.execute(
                        """
                        INSERT INTO drill_dependencies (drill_id, depends_on_id)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (drill_id, dep_drill['id'])
                    )

        return drill_id

    def update_drill(
        self,
        drill_id: int,
        name: str | None = None,
        prompt: str | None = None,
        description: str | None = None,
        scope: str | None = None,
        output_shape: str | None = None,
        category: str | None = None,
        always: bool | None = None,
        min_hits: int | None = None,
        keywords: list[str] | None = None,
        match: list[str] | None = None,
        cooldown: int | None = None,
        detail_pass: dict[str, Any] | None = None,
        depends_on: list[str] | None = None,
        config_id_for_deps: str | None = None
    ) -> None:
        """Update an existing drill."""
        import json as _json

        updates = []
        params = []

        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if description is not None:
            updates.append("description = %s")
            params.append(description)
        if prompt is not None:
            updates.append("prompt = %s")
            params.append(prompt)
        if scope is not None:
            updates.append("scope = %s")
            params.append(scope)
        if output_shape is not None:
            updates.append("output_shape = %s")
            params.append(output_shape)
        if category is not None:
            updates.append("category = %s")
            params.append(category)
        if always is not None:
            updates.append("always = %s")
            params.append(always)
        if min_hits is not None:
            updates.append("min_hits = %s")
            params.append(min_hits)
        if keywords is not None:
            updates.append("keywords = %s")
            params.append(keywords)
        if match is not None:
            updates.append("match = %s")
            params.append(match)
        if cooldown is not None:
            updates.append("cooldown = %s")
            params.append(cooldown)
        if detail_pass is not None:
            updates.append("detail_pass = %s::jsonb")
            params.append(_json.dumps(detail_pass))

        if updates:
            params.append(drill_id)
            self.cursor.execute(
                f"UPDATE drills SET {', '.join(updates)} WHERE id = %s",
                params
            )

        # Update dependencies if provided
        if depends_on is not None:
            # Clear existing
            self.cursor.execute(
                "DELETE FROM drill_dependencies WHERE drill_id = %s",
                (drill_id,)
            )

            # Insert new
            for dep_name in depends_on:
                dep_drill = self.get_drill(dep_name, config_id_for_deps or '')
                if dep_drill:
                    self.cursor.execute(
                        """
                        INSERT INTO drill_dependencies (drill_id, depends_on_id)
                        VALUES (%s, %s)
                        """,
                        (drill_id, dep_drill['id'])
                    )

    def delete_drill(self, drill_id: int) -> None:
        """Delete a drill (will fail if other drills depend on it due to FK RESTRICT)."""
        self.cursor.execute(
            "DELETE FROM drills WHERE id = %s",
            (drill_id,)
        )

    def get_drill_dependents(self, drill_id: int) -> list[str]:
        """Get names of drills that depend on this drill."""
        self.cursor.execute(
            """
            SELECT d.name FROM drills d
            JOIN drill_dependencies dd ON d.id = dd.drill_id
            WHERE dd.depends_on_id = %s
            ORDER BY d.name
            """,
            (drill_id,)
        )
        return [row[0] for row in self.cursor.fetchall()]
