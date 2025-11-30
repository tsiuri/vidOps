# vidops/db/connection.py

import time
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool
from psycopg2 import pool
from psycopg2.extras import DictCursor

from vidops.config import load_config

# Global connection pool variable
_connection_pool: Optional[pool.ThreadedConnectionPool] = None

def init_pool():
    """
    Initializes the database connection pool using settings from config.
    """
    global _connection_pool
    if _connection_pool is not None:
        return

    config = load_config()
    db_config = config.database

    try:
        print(f"Initializing connection pool for {db_config.user}@{db_config.host}:{db_config.port}/{db_config.name}")
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            host=db_config.host,
            port=db_config.port,
            dbname=db_config.name,
            user=db_config.user,
            password=db_config.password,
            cursor_factory=DictCursor  # Use dictionary-like cursors
        )
    except psycopg2.OperationalError as e:
        print(f"Error: Could not connect to database: {e}")
        _connection_pool = None
        raise

def get_pool() -> pool.ThreadedConnectionPool:
    """Returns the singleton connection pool, initializing it if necessary."""
    if _connection_pool is None:
        init_pool()
    if _connection_pool is None:
        raise ConnectionError("Database connection pool is not available.")
    return _connection_pool

def close_pool():
    """Closes all connections in the pool."""
    global _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
        _connection_pool = None
        print("Database connection pool closed.")

@contextmanager
def get_connection():
    """
    Provides a connection from the pool as a context manager.
    
    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """
    conn = None
    try:
        pool = get_pool()
        conn = pool.getconn()
        yield conn
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Database transaction failed: {e}")
        raise
    finally:
        if conn:
            pool.putconn(conn)

def check_connection(max_retries=3, delay_sec=2) -> bool:
    """
    Attempts to connect to the database to check its health.
    Retries with exponential backoff.
    """
    for i in range(max_retries):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    result = cur.fetchone()
                    if result and result[0] == 1:
                        print("Database connection successful.")
                        return True
        except Exception as e:
            print(f"Connection attempt {i+1}/{max_retries} failed: {e}")
            if i < max_retries - 1:
                time.sleep(delay_sec * (2 ** i))
    
    print("Error: Could not establish database connection after multiple retries.")
    return False


if __name__ == '__main__':
    # Example usage and testing
    # Note: This requires a running PostgreSQL database configured in config.yaml
    print("--- Testing Database Connection ---")
    try:
        if check_connection():
            print("✅ Connection health check passed.")
            
            with get_connection() as conn:
                print(f"Got connection from pool: {conn.dsn}")
                with conn.cursor() as cur:
                    cur.execute("SELECT version();")
                    version = cur.fetchone()
                    print(f"PostgreSQL Version: {version[0]}")
            
            print("✅ Successfully acquired and released a connection.")
            
        else:
            print("❌ Connection health check failed.")
            print("Please ensure the database is running and config.yaml is correct.")

    finally:
        close_pool()
        print("--- Test Finished ---")
