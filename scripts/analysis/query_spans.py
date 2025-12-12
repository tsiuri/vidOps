#!/usr/bin/env python3
"""
Query and display spans from the database.

Supports querying conflict_spans, topic_spans, and person_spans with filters:
- ytid: Filter by video ID
- target_name: Filter conflict spans by source_pass (hot target name)
- parties: Filter conflict spans by party involvement
- time_range: Filter spans by start_sec/end_sec
- span_type: Type of spans to query (conflict, topic, person, all)
"""

import argparse
import sys
from typing import Dict, List, Any, Optional
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Install with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


class SpanQuery:
    """Query spans from the database with filters."""

    def __init__(self, db_name: str = "transcripts", db_host: str = "localhost",
                 db_port: int = 5432, db_user: str = "billie", db_password: str = "z"):
        self.conn = psycopg2.connect(
            dbname=db_name,
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password
        )
        self.cursor = self.conn.cursor(cursor_factory=RealDictCursor)

    def close(self):
        """Close database connection."""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()

    def query_target_spans(self, ytid: Optional[str] = None,
                          target_name: Optional[str] = None,
                          party: Optional[str] = None,
                          start_after: Optional[float] = None,
                          end_before: Optional[float] = None,
                          limit: int = 100) -> List[Dict[str, Any]]:
        """
        Query target spans with optional filters.

        Args:
            ytid: Filter by video ID
            target_name: Filter by source_pass (target/drill name)
            party: Filter by party involvement (checks if party in parties array)
            start_after: Filter spans starting after this time (seconds)
            end_before: Filter spans ending before this time (seconds)
            limit: Maximum results to return
        """
        conditions = []
        params = []

        if ytid:
            conditions.append("ytid = %s")
            params.append(ytid)

        if target_name:
            conditions.append("source_pass ILIKE %s")
            params.append(f"%{target_name}%")

        if party:
            conditions.append("%s = ANY(parties)")
            params.append(party)

        if start_after is not None:
            conditions.append("start_sec >= %s")
            params.append(start_after)

        if end_before is not None:
            conditions.append("end_sec <= %s")
            params.append(end_before)

        where_clause = " AND ".join(conditions) if conditions else "TRUE"

        query = f"""
            SELECT
                id, ytid, parties, description, start_sec, end_sec,
                chunk_ids, context, sentiment, polarity, source_pass,
                pass_tier, analysis_type, batch_id, diarized, transcription_machine
            FROM target_spans
            WHERE {where_clause}
            ORDER BY ytid, coalesce(chunk_ids[1], 0)
            LIMIT %s
        """
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def query_topic_spans(self, ytid: Optional[str] = None,
                         topic: Optional[str] = None,
                         start_after: Optional[float] = None,
                         end_before: Optional[float] = None,
                         limit: int = 100) -> List[Dict[str, Any]]:
        """
        Query topic spans with optional filters.

        Args:
            ytid: Filter by video ID
            topic: Filter by topic name (normalized, case-insensitive)
            start_after: Filter spans starting after this time (seconds)
            end_before: Filter spans ending before this time (seconds)
            limit: Maximum results to return
        """
        conditions = []
        params = []

        if ytid:
            conditions.append("ytid = %s")
            params.append(ytid)

        if topic:
            conditions.append("normalized_topic ILIKE %s")
            params.append(f"%{topic.lower()}%")

        if start_after is not None:
            conditions.append("start_sec >= %s")
            params.append(start_after)

        if end_before is not None:
            conditions.append("end_sec <= %s")
            params.append(end_before)

        where_clause = " AND ".join(conditions) if conditions else "TRUE"

        query = f"""
            SELECT
                id, ytid, topic, normalized_topic, start_sec, end_sec,
                chunk_ids, context, sentiment, source_pass, pass_tier,
                analysis_type, batch_id, diarized, transcription_machine
            FROM topic_spans
            WHERE {where_clause}
            ORDER BY ytid, chunk_ids[1]
            LIMIT %s
        """
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def query_person_spans(self, ytid: Optional[str] = None,
                          person: Optional[str] = None,
                          start_after: Optional[float] = None,
                          end_before: Optional[float] = None,
                          limit: int = 100) -> List[Dict[str, Any]]:
        """
        Query person spans with optional filters.

        Args:
            ytid: Filter by video ID
            person: Filter by person name (normalized, case-insensitive)
            start_after: Filter spans starting after this time (seconds)
            end_before: Filter spans ending before this time (seconds)
            limit: Maximum results to return
        """
        conditions = []
        params = []

        if ytid:
            conditions.append("ytid = %s")
            params.append(ytid)

        if person:
            conditions.append("normalized_name ILIKE %s")
            params.append(f"%{person.lower()}%")

        if start_after is not None:
            conditions.append("start_sec >= %s")
            params.append(start_after)

        if end_before is not None:
            conditions.append("end_sec <= %s")
            params.append(end_before)

        where_clause = " AND ".join(conditions) if conditions else "TRUE"

        query = f"""
            SELECT
                id, ytid, person_name, normalized_name, start_sec, end_sec,
                chunk_ids, context, sentiment, polarity, source_pass, pass_tier,
                analysis_type, batch_id, diarized, transcription_machine
            FROM person_spans
            WHERE {where_clause}
            ORDER BY ytid, chunk_ids[1]
            LIMIT %s
        """
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_video_info(self, ytid: str) -> Optional[Dict[str, Any]]:
        """Get video metadata."""
        self.cursor.execute("""
            SELECT ytid, title, title_date, channel_name, upload_date
            FROM videos
            WHERE ytid = %s
        """, (ytid,))
        row = self.cursor.fetchone()
        return dict(row) if row else None


class SpanFormatter:
    """Format spans for CLI display."""

    @staticmethod
    def format_time(seconds: Optional[float]) -> str:
        """Format seconds as mm:ss or return 'N/A'."""
        if seconds is None:
            return "N/A"
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    @staticmethod
    def format_duration(start: Optional[float], end: Optional[float]) -> str:
        """Format duration."""
        if start is None or end is None:
            return "N/A"
        duration = end - start
        return f"{duration:.1f}s"

    @staticmethod
    def format_target_span(span: Dict[str, Any], verbose: bool = False) -> str:
        """Format a generic target span for display."""
        lines = []

        lines.append(f"\n{'='*80}")
        lines.append(f"TARGET SPAN #{span['id']}")
        lines.append(f"{'='*80}")

        lines.append(f"Video ID:     {span['ytid']}")
        lines.append(f"Target:       {span.get('source_pass') or 'N/A'}")
        lines.append(f"Description:  {span.get('description') or 'N/A'}")

        parties = span.get('parties') or []
        lines.append(f"Parties:      {', '.join(parties) if parties else 'None'}")

        start = SpanFormatter.format_time(span.get('start_sec'))
        end = SpanFormatter.format_time(span.get('end_sec'))
        duration = SpanFormatter.format_duration(span.get('start_sec'), span.get('end_sec'))
        lines.append(f"Time:         {start} - {end} (duration: {duration})")

        lines.append(f"Sentiment:    {span.get('sentiment') or 'N/A'}")
        lines.append(f"Polarity:     {span.get('polarity') or 'N/A'}")

        chunk_ids = span.get('chunk_ids') or []
        lines.append(f"Chunks:       {chunk_ids if chunk_ids else 'N/A'}")

        if verbose:
            context = span.get('context') or ''
            if context:
                lines.append(f"\nContext:")
                lines.append(f"  {context[:200]}{'...' if len(context) > 200 else ''}")

            lines.append(f"\nMetadata:")
            lines.append(f"  Pass Tier:    {span.get('pass_tier') or 'N/A'}")
            lines.append(f"  Analysis:     {span.get('analysis_type') or 'N/A'}")
            lines.append(f"  Batch ID:     {span.get('batch_id') or 'N/A'}")
            lines.append(f"  Diarized:     {span.get('diarized') or False}")
            lines.append(f"  Machine:      {span.get('transcription_machine') or 'N/A'}")

        return "\n".join(lines)

    @staticmethod
    def format_topic_span(span: Dict[str, Any], verbose: bool = False) -> str:
        """Format a topic span for display."""
        lines = []

        lines.append(f"\n{'='*80}")
        lines.append(f"TOPIC SPAN #{span['id']}")
        lines.append(f"{'='*80}")

        lines.append(f"Video ID:     {span['ytid']}")
        lines.append(f"Topic:        {span['topic']}")

        start = SpanFormatter.format_time(span.get('start_sec'))
        end = SpanFormatter.format_time(span.get('end_sec'))
        duration = SpanFormatter.format_duration(span.get('start_sec'), span.get('end_sec'))
        lines.append(f"Time:         {start} - {end} (duration: {duration})")

        lines.append(f"Sentiment:    {span.get('sentiment') or 'N/A'}")

        chunk_ids = span.get('chunk_ids') or []
        lines.append(f"Chunks:       {chunk_ids if chunk_ids else 'N/A'}")

        if verbose:
            context = span.get('context') or ''
            if context:
                lines.append(f"\nContext:")
                lines.append(f"  {context[:200]}{'...' if len(context) > 200 else ''}")

            lines.append(f"\nMetadata:")
            lines.append(f"  Source Pass:  {span.get('source_pass') or 'N/A'}")
            lines.append(f"  Pass Tier:    {span.get('pass_tier') or 'N/A'}")
            lines.append(f"  Analysis:     {span.get('analysis_type') or 'N/A'}")
            lines.append(f"  Batch ID:     {span.get('batch_id') or 'N/A'}")

        return "\n".join(lines)

    @staticmethod
    def format_person_span(span: Dict[str, Any], verbose: bool = False) -> str:
        """Format a person span for display."""
        lines = []

        lines.append(f"\n{'='*80}")
        lines.append(f"PERSON SPAN #{span['id']}")
        lines.append(f"{'='*80}")

        lines.append(f"Video ID:     {span['ytid']}")
        lines.append(f"Person:       {span['person_name']}")

        start = SpanFormatter.format_time(span.get('start_sec'))
        end = SpanFormatter.format_time(span.get('end_sec'))
        duration = SpanFormatter.format_duration(span.get('start_sec'), span.get('end_sec'))
        lines.append(f"Time:         {start} - {end} (duration: {duration})")

        lines.append(f"Sentiment:    {span.get('sentiment') or 'N/A'}")
        lines.append(f"Polarity:     {span.get('polarity') or 'N/A'}")

        chunk_ids = span.get('chunk_ids') or []
        lines.append(f"Chunks:       {chunk_ids if chunk_ids else 'N/A'}")

        if verbose:
            context = span.get('context') or ''
            if context:
                lines.append(f"\nContext:")
                lines.append(f"  {context[:200]}{'...' if len(context) > 200 else ''}")

            lines.append(f"\nMetadata:")
            lines.append(f"  Source Pass:  {span.get('source_pass') or 'N/A'}")
            lines.append(f"  Pass Tier:    {span.get('pass_tier') or 'N/A'}")
            lines.append(f"  Analysis:     {span.get('analysis_type') or 'N/A'}")
            lines.append(f"  Batch ID:     {span.get('batch_id') or 'N/A'}")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Query and display spans from the database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Query all conflict spans for a video
  %(prog)s --ytid VIDEO_ID --type conflict

  # Query conflict spans by hot target
  %(prog)s --target conflict_detection

  # Query spans involving specific party
  %(prog)s --party Biden --type conflict

  # Query spans in time range
  %(prog)s --ytid VIDEO_ID --start-after 60 --end-before 120

  # Query all span types for a video
  %(prog)s --ytid VIDEO_ID --type all -v

  # Query topic spans
  %(prog)s --ytid VIDEO_ID --type topic --topic gaza

  # Query person spans
  %(prog)s --ytid VIDEO_ID --type person --person biden
        """
    )

    # Filters
    parser.add_argument("--ytid", help="Filter by video ID")
    parser.add_argument("--target", dest="target_name", help="Filter target spans by source_pass/name")
    parser.add_argument("--party", help="Filter target spans by party involvement")
    parser.add_argument("--topic", help="Filter topic spans by topic name")
    parser.add_argument("--person", help="Filter person spans by person name")
    parser.add_argument("--start-after", type=float, help="Filter spans starting after this time (seconds)")
    parser.add_argument("--end-before", type=float, help="Filter spans ending before this time (seconds)")

    # Query options
    parser.add_argument("--type", choices=["target", "topic", "person", "all"], default="target",
                       help="Type of spans to query (default: target)")
    parser.add_argument("--limit", type=int, default=100, help="Maximum results per span type (default: 100)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed information")

    # Database connection
    parser.add_argument("--db-name", default="transcripts", help="Database name (default: transcripts)")
    parser.add_argument("--db-host", default="localhost", help="Database host (default: localhost)")
    parser.add_argument("--db-port", type=int, default=5432, help="Database port (default: 5432)")
    parser.add_argument("--db-user", default="billie", help="Database user (default: billie)")
    parser.add_argument("--db-password", default="z", help="Database password (default: z)")

    args = parser.parse_args()

    # Connect to database
    try:
        query = SpanQuery(
            db_name=args.db_name,
            db_host=args.db_host,
            db_port=args.db_port,
            db_user=args.db_user,
            db_password=args.db_password
        )
    except Exception as e:
        print(f"Error connecting to database: {e}", file=sys.stderr)
        return 1

    try:
        # Show video info if ytid provided
        if args.ytid:
            video_info = query.get_video_info(args.ytid)
            if video_info:
                print(f"\n{'='*80}")
                print(f"VIDEO: {video_info['title']}")
                print(f"{'='*80}")
                print(f"ID:      {video_info['ytid']}")
                print(f"Date:    {video_info.get('title_date') or 'N/A'}")
                print(f"Channel: {video_info.get('channel_name') or 'N/A'}")
            else:
                print(f"Warning: Video {args.ytid} not found in database", file=sys.stderr)

        formatter = SpanFormatter()
        total_results = 0

        # Query conflict spans
        if args.type in ["target", "all"]:
            target_spans = query.query_target_spans(
                ytid=args.ytid,
                target_name=args.target_name,
                party=args.party,
                start_after=args.start_after,
                end_before=args.end_before,
                limit=args.limit
            )

            if target_spans:
                print(f"\n{'#'*80}")
                print(f"TARGET SPANS ({len(target_spans)} result(s))")
                print(f"{'#'*80}")

                for span in target_spans:
                    print(formatter.format_target_span(span, args.verbose))

                total_results += len(target_spans)

        # Query topic spans
        if args.type in ["topic", "all"]:
            topic_spans = query.query_topic_spans(
                ytid=args.ytid,
                topic=args.topic,
                start_after=args.start_after,
                end_before=args.end_before,
                limit=args.limit
            )

            if topic_spans:
                print(f"\n{'#'*80}")
                print(f"TOPIC SPANS ({len(topic_spans)} result(s))")
                print(f"{'#'*80}")

                for span in topic_spans:
                    print(formatter.format_topic_span(span, args.verbose))

                total_results += len(topic_spans)

        # Query person spans
        if args.type in ["person", "all"]:
            person_spans = query.query_person_spans(
                ytid=args.ytid,
                person=args.person,
                start_after=args.start_after,
                end_before=args.end_before,
                limit=args.limit
            )

            if person_spans:
                print(f"\n{'#'*80}")
                print(f"PERSON SPANS ({len(person_spans)} result(s))")
                print(f"{'#'*80}")

                for span in person_spans:
                    print(formatter.format_person_span(span, args.verbose))

                total_results += len(person_spans)

        # Summary
        print(f"\n{'='*80}")
        print(f"TOTAL: {total_results} span(s) found")
        print(f"{'='*80}\n")

        if total_results == 0:
            print("No spans found matching the filters.", file=sys.stderr)
            return 1

    finally:
        query.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
