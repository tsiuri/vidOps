# vidops/dal/transcripts.py

from typing import Optional, List
from psycopg2.extras import execute_values

from vidops.db import get_connection
from vidops.models import Transcript, Word
from vidops.config import TRANSCRIPT_QUALITY_HIERARCHY

class TranscriptRepository:
    """
    Data Access Layer for the 'transcripts' table.
    """

    def get(self, ytid: str, kind: str) -> Optional[Transcript]:
        """
        Retrieves a single transcript's metadata.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM transcripts WHERE ytid = %s AND kind = %s",
                    (ytid, kind)
                )
                row = cur.fetchone()
                return Transcript.from_row(row) if row else None

    def list_for_ytid(self, ytid: str) -> List[Transcript]:
        """
        Retrieves all transcripts for a given ytid.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM transcripts WHERE ytid = %s ORDER BY kind",
                    (ytid,)
                )
                rows = cur.fetchall()
                return [Transcript.from_row(row) for row in rows]

    def get_best_available(self, ytid: str) -> Optional[Transcript]:
        """
        Retrieves the highest quality transcript available for a given ytid,
        based on TRANSCRIPT_QUALITY_HIERARCHY.

        Returns None if no transcripts exist for this ytid.
        """
        available = self.list_for_ytid(ytid)
        if not available:
            return None

        # Create a map of kind -> transcript for quick lookup
        transcripts_by_kind = {t.kind: t for t in available}

        # Find the first match in the quality hierarchy
        for kind in TRANSCRIPT_QUALITY_HIERARCHY:
            if kind in transcripts_by_kind:
                return transcripts_by_kind[kind]

        # If no match in hierarchy, return the first available
        # (fallback for unexpected transcript kinds)
        return available[0]

    def upsert(self, transcript: Transcript) -> Transcript:
        """
        Inserts or updates a transcript's metadata.
        """
        transcript_dict = transcript.to_dict()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO transcripts (ytid, kind, lang, path, word_count, segment_count)
                    VALUES (%(ytid)s, %(kind)s, %(lang)s, %(path)s, %(word_count)s, %(segment_count)s)
                    ON CONFLICT (ytid, kind, lang) DO UPDATE SET
                        lang = EXCLUDED.lang,
                        path = EXCLUDED.path,
                        word_count = EXCLUDED.word_count,
                        segment_count = EXCLUDED.segment_count,
                        created_at = NOW() -- Or keep original? Let's update.
                    RETURNING *;
                    """,
                    transcript_dict
                )
                row = cur.fetchone()
                return Transcript.from_row(row)


class WordRepository:
    """
    Data Access Layer for the 'words' table.
    """

    def bulk_insert(self, words: List[Word], page_size: int = 500) -> int:
        """
        Efficiently inserts a large number of words into the database.
        Uses psycopg2's execute_values for high performance.
        
        Args:
            words: A list of Word objects to insert.
            page_size: The number of rows to insert per batch.
            
        Returns:
            The total number of rows inserted.
        """
        if not words:
            return 0
            
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Assuming the first word's ytid and source are the same for all
                # It's safer to delete only for the specific scope of the insert
                ytid = words[0].ytid
                source = words[0].source
                
                # First, delete existing words for this specific ytid and source to ensure idempotency
                cur.execute(
                    "DELETE FROM words WHERE ytid = %s AND source = %s",
                    (ytid, source)
                )
                
                # Now, insert the new words
                sql = """
                    INSERT INTO words (
                        ytid, source, idx, word, start_sec, end_sec, confidence, segment_id
                    ) VALUES %s;
                """
                
                # Convert list of objects to list of tuples
                data_tuples = [w.to_tuple() for w in words]
                
                execute_values(
                    cur,
                    sql,
                    data_tuples,
                    template=None,
                    page_size=page_size
                )
                return cur.rowcount
    
    def search(self, query: str, limit: int = 100) -> List[Word]:
        """
        Performs a simple search on the 'words' table.
        This is a placeholder for future Full-Text Search implementation.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Using LIKE for a basic search. This is not performant for large tables.
                # A real implementation would use tsvector and tsquery.
                cur.execute(
                    "SELECT * FROM words WHERE word ILIKE %s ORDER BY start_sec LIMIT %s",
                    (f"%{query}%", limit)
                )
                rows = cur.fetchall()
                return [Word.from_row(row) for row in rows]

    def find_phrase_hits(
        self,
        tokens: List[str],
        source: Optional[str] = None,
        limit: int = 100,
        exact: bool = False,
        ytids: Optional[List[str]] = None,
    ) -> List[dict]:
        """
        Find occurrences of sequential tokens within the words table.
        """
        tokens = [tok.strip() for tok in tokens if tok.strip()]
        if not tokens:
            return []

        first_token = tokens[0]
        results: List[dict] = []

        with get_connection() as conn:
            with conn.cursor() as cur:
                comparator = "LOWER(word) = LOWER(%s)" if exact else "word ILIKE %s"
                sql = f"""
                    SELECT ytid, source, idx, start_sec, end_sec
                    FROM words
                    WHERE {comparator}
                """
                params: List = [first_token if exact else f"%{first_token}%"]
                if source:
                    sql += " AND source = %s"
                    params.append(source)
                if ytids:
                    sql += " AND ytid = ANY(%s)"
                    params.append(ytids)
                sql += " ORDER BY start_sec LIMIT %s"
                params.append(limit)
                cur.execute(sql, tuple(params))
                candidates = cur.fetchall()

                for candidate in candidates:
                    start_idx = candidate["idx"]
                    end_idx = start_idx + len(tokens) - 1
                    cur.execute(
                        """
                        SELECT idx, word, start_sec, end_sec
                        FROM words
                        WHERE ytid = %s AND source = %s AND idx BETWEEN %s AND %s
                        ORDER BY idx
                        """,
                        (candidate["ytid"], candidate["source"], start_idx, end_idx)
                    )
                    snippet = cur.fetchall()
                    if len(snippet) != len(tokens):
                        continue

                    matched = True
                    for token, row in zip(tokens, snippet):
                        candidate_word = row["word"].strip().lower()
                        target = token.strip().lower()
                        if exact:
                            if candidate_word != target:
                                matched = False
                                break
                        else:
                            if target not in candidate_word:
                                matched = False
                                break
                    if not matched:
                        continue

                    results.append(
                        {
                            "ytid": candidate["ytid"],
                            "source": candidate["source"],
                            "start_sec": snippet[0]["start_sec"],
                            "end_sec": snippet[-1]["end_sec"],
                            "start_idx": start_idx,
                            "end_idx": snippet[-1]["idx"],
                            "phrase": " ".join(row["word"] for row in snippet),
                        }
                    )

        return results

    def auto_resolve_source(self, ytids: Optional[List[str]] = None) -> Optional[str]:
        """
        Pick a single available source for the given ytids (or globally) if only one exists.
        If multiple sources exist, return None to force the caller to choose.
        """
        sql = "SELECT DISTINCT source FROM words"
        params: List = []
        if ytids:
            sql += " WHERE ytid = ANY(%s)"
            params.append(ytids)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params) if params else None)
                sources = [row[0] for row in cur.fetchall()]
                if len(sources) == 1:
                    return sources[0]
                return None

    def fetch_for_source(
        self,
        ytid: str,
        source: str,
        limit: Optional[int] = None
    ) -> List[Word]:
        """
        Fetch ordered word rows for a given ytid + source.

        Args:
            ytid: Video identifier
            source: Words source (e.g., 'whisper-medium')
            limit: Optional cap on number of rows to return
        """
        sql = "SELECT * FROM words WHERE ytid = %s AND source = %s ORDER BY idx"
        params: List = [ytid, source]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                return [Word.from_row(row) for row in rows]
