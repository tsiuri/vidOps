import pytest
from psycopg2 import sql
from db import get_connection


@pytest.fixture(scope="function", autouse=True)
def isolate_queue_tables():
    """
    Ensures dedicated test copies of the jobs/workers tables exist and are clean
    before each test. Uses tables named jobs_test/workers_test to avoid touching
    production data.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Ensure base tables exist (raises if schema is missing)
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'jobs'")
            if cur.fetchone() is None:
                raise RuntimeError("Base 'jobs' table not found; apply migration 002 first.")
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'workers'")
            if cur.fetchone() is None:
                raise RuntimeError("Base 'workers' table not found; apply migration 002 first.")

            # Create dedicated test tables (structure copied from base tables)
            cur.execute("CREATE TABLE IF NOT EXISTS jobs_test (LIKE jobs INCLUDING ALL);")
            cur.execute("CREATE TABLE IF NOT EXISTS workers_test (LIKE workers INCLUDING ALL);")

            # Clear any leftover rows
            cur.execute("TRUNCATE TABLE jobs_test;")
            cur.execute("TRUNCATE TABLE workers_test;")

        conn.commit()

    yield

    # Cleanup after tests (truncate only; keep tables for reuse)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE jobs_test;")
            cur.execute("TRUNCATE TABLE workers_test;")
        conn.commit()
