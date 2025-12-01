# vidops/dal/hits.py

from typing import List
from psycopg2.extras import execute_values

from vidops.db import get_connection


class HitsRepository:
    """
    Data Access Layer for the 'hits' table.
    Used to log phrase search results for traceability.
    """

    def bulk_insert(self, rows: List[dict]) -> int:
        """
        Insert multiple hit rows.

        Expected keys per row: ytid, start_sec, end_sec,
        label, phrase/source_caption, source/source_type, run_name.
        """
        if not rows:
            return 0

        values = []
        for r in rows:
            values.append(
                (
                    r.get("ytid"),
                    float(r.get("start_sec", 0.0)),
                    float(r.get("end_sec", 0.0)),
                    r.get("label"),
                    r.get("phrase") or r.get("source_caption"),
                    r.get("source") or r.get("source_type"),
                    r.get("run_name"),
                )
            )

        sql = """
            INSERT INTO hits (
                ytid, start_sec, end_sec,
                label, source_caption, source_type, run_name
            ) VALUES %s
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                execute_values(cur, sql, values, page_size=1000)
                return len(values)
