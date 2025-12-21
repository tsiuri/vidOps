# tests/db/test_connection.py

import pytest
import psycopg2
import time
from unittest.mock import patch, MagicMock
from typing import Optional

# ADDED import for pool
from psycopg2 import pool

from db.connection import init_pool, get_pool, close_pool, get_connection, check_connection
from configuration import load_config, Config, DatabaseConfig

# --- Module-level setup for database availability check ---
# This needs to be outside a fixture if pytestmark consumes it directly.
_is_db_available = False
_config_for_check: Optional[Config] = None
try:
    _config_for_check = load_config()
    # Ensure no existing pool interferes with this initial check
    close_pool() 
    # Patch load_config to return our _config_for_check during init_pool for this check
            with patch('db.connection.load_config', return_value=_config_for_check):        init_pool() # Try to init with loaded config
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone()[0] == 1
    _is_db_available = True
except (psycopg2.OperationalError, ConnectionError) as e:
    print(f"\nWARN: Database not available for integration tests ({e}). Skipping.")
    _is_db_available = False
except Exception as e:
    print(f"\nWARN: Unexpected error during initial DB check ({e}). Skipping.")
    _is_db_available = False
finally:
    # Close the pool used for the check to not interfere with test-specific pool inits
    close_pool()

# Mark all tests in this file as 'integration' and skip if DB is not available
pytestmark = pytest.mark.skipif(not _is_db_available, reason="database not available for integration tests")

# --- Fixtures for tests ---

@pytest.fixture(scope="module", autouse=True)
def db_pool_lifecycle():
    """Manages the database connection pool lifecycle for the module."""
    # Ensure no pool is active before tests
    close_pool()
    
    # Init the pool using the actual config for the module's tests
    # Ensure vidops.config.load_config is not mocked globally if it's used here
    try:
        init_pool()
    except (psycopg2.OperationalError, ConnectionError) as e:
        pytest.fail(f"Could not initialize DB pool for tests: {e}")
    
    yield
    # Ensure pool is closed after all tests in the module
    close_pool()

@pytest.fixture
def clean_test_table():
    """Creates and drops a temporary table for testing transactions."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS test_table (id SERIAL PRIMARY KEY, value TEXT)")
            conn.commit() # Commit CREATE TABLE
    yield
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS test_table")
            conn.commit() # Commit DROP TABLE

# --- Tests ---

@pytest.mark.integration
def test_init_pool_and_get_pool():
    """Test that the connection pool can be initialized and retrieved."""
    # The pool is initialized by the db_pool_lifecycle fixture if db_available is True
    pool_instance = get_pool()
    assert pool_instance is not None
    assert isinstance(pool_instance, pool.ThreadedConnectionPool)
    # Ensure subsequent calls return the same instance (singleton behavior within the module)
    assert pool_instance is get_pool()

@pytest.mark.integration
def test_init_pool_failure(mocker):
    """Test that init_pool raises OperationalError on connection failure."""
    close_pool() # Ensure no active pool from previous tests
    
    # Mock psycopg2.connect to raise an operational error
    mocker.patch('psycopg2.connect', side_effect=psycopg2.OperationalError("Mocked connection error"))
    
    # Patch load_config to return a dummy config, so init_pool doesn't try to load real one
    mocker.patch('db.connection.load_config', return_value=Config(database=DatabaseConfig(host="invalid", port=1234)))

    with pytest.raises(psycopg2.OperationalError):
        init_pool()
    
    # After a failed init, get_pool() should also try to init and thus fail if mocks are still active
    # The best check here is simply that init_pool() fails as expected.
    # We do NOT assert get_pool() is None, as get_pool will attempt to re-init.
    pass # No further assertions needed after pytest.raises

@pytest.mark.integration
def test_get_connection_success(clean_test_table):
    """Test get_connection context manager commits on success."""
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO test_table (value) VALUES (%s)", ("test_value_success",))
    
    # Verify the commit happened
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM test_table WHERE value = %s", ("test_value_success",))
            result = cur.fetchone()
            assert result is not None
            assert result[0] == "test_value_success"

@pytest.mark.integration
def test_get_connection_rollback_on_exception(clean_test_table):
    """Test get_connection context manager rolls back on exception."""
    
    with pytest.raises(ValueError):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO test_table (value) VALUES (%s)", ("test_value_fail",))
            raise ValueError("Simulated error")
    
    # Verify the rollback happened
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM test_table WHERE value = %s", ("test_value_fail",))
            result = cur.fetchone()
            assert result is None # Should not be in DB

@pytest.mark.integration
def test_check_connection_success():
    """Test that check_connection returns True for a healthy database."""
    # Ensure no pool is active initially for this check, init_pool() will be called internally
    close_pool()
    assert check_connection(max_retries=1) is True

@pytest.mark.integration
def test_check_connection_failure(mocker):
    """Test that check_connection returns False after retries on connection failure."""
    close_pool() # Ensure no active pool
    # Mock psycopg2.connect to always raise an operational error
    mocker.patch('psycopg2.connect', side_effect=psycopg2.OperationalError("Mocked connection error"))
    
    # Patch load_config to return a dummy config for this test, as check_connection calls load_config
    mocker.patch('db.connection.load_config', return_value=Config(database=DatabaseConfig(host="invalid", port=1234)))
    
    # check_connection should try multiple times and eventually fail
    assert check_connection(max_retries=2, delay_sec=0.01) is False

@pytest.mark.integration
def test_close_pool_functionality(mocker): # Added mocker to clear global mock from test_init_pool_failure
    """Test that close_pool correctly shuts down connections and clears the pool."""
    # Ensure init_pool is called and a pool is active
    close_pool() # Ensure clean state
    
    # Patch load_config to return a valid config for this test
    mocker.patch('db.connection.load_config', return_value=load_config())
    
    init_pool()
    pool_instance = get_pool()
    assert pool_instance is not None
    
    close_pool()
    
    # The internal _connection_pool should be None after close_pool()
    from db import connection as db_connection_module
    assert db_connection_module._connection_pool is None
    
    # Now, if we call get_pool(), it should re-initialize a new pool without error
    # because load_config is not mocked for failure and psycopg2.connect is not mocked.
    re_init_pool_instance = get_pool()
    assert re_init_pool_instance is not None
    assert isinstance(re_init_pool_instance, pool.ThreadedConnectionPool)
    assert re_init_pool_instance is not pool_instance # Should be a new instance