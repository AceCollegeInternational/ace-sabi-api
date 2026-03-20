"""
database/connections.py — MySQL connection pools for all three databases.

Usage:
    from database.connections import get_sabi, get_enterprise, get_moodle

    # In a route or service:
    with get_sabi() as (conn, cursor):
        cursor.execute("SELECT * FROM teachers WHERE id = %s", (teacher_id,))
        row = cursor.fetchone()

All connections are managed via context managers.
Connections are returned to the pool automatically on exit.
Enterprise and Moodle connections are read-only by convention —
the user credentials in .env should only have SELECT privileges on those DBs.
"""

import logging
from contextlib import contextmanager
from typing import Generator, Tuple

import mysql.connector
from mysql.connector import pooling, Error as MySQLError

from config import SABI_DB, ENTERPRISE_DB, MOODLE_DB

logger = logging.getLogger(__name__)


# =============================================================================
# POOL INITIALISATION
# Pools are created once at module load time.
# =============================================================================

def _create_pool(config: dict) -> pooling.MySQLConnectionPool:
    """Create a named MySQL connection pool from a config dict."""
    try:
        pool = pooling.MySQLConnectionPool(
            pool_name=config["pool_name"],
            pool_size=config["pool_size"],
            host=config["host"],
            port=config["port"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            charset=config["charset"],
            use_unicode=True,
            # Reconnect automatically if the server drops the connection
            connection_timeout=30,
            autocommit=False,
        )
        logger.info(
            "DB pool '%s' created (%d connections) → %s:%s/%s",
            config["pool_name"],
            config["pool_size"],
            config["host"],
            config["port"],
            config["database"],
        )
        return pool
    except MySQLError as e:
        logger.error(
            "Failed to create pool '%s': %s", config["pool_name"], e
        )
        raise


_sabi_pool:       pooling.MySQLConnectionPool | None = None
_enterprise_pool: pooling.MySQLConnectionPool | None = None
_moodle_pool:     pooling.MySQLConnectionPool | None = None


def init_pools() -> None:
    """
    Initialise all three connection pools.
    Call once at application startup (in main.py lifespan).
    """
    global _sabi_pool, _enterprise_pool, _moodle_pool
    _sabi_pool       = _create_pool(SABI_DB)
    _enterprise_pool = _create_pool(ENTERPRISE_DB)
    _moodle_pool     = _create_pool(MOODLE_DB)


def close_pools() -> None:
    """
    Close all pool connections.
    Call on application shutdown (in main.py lifespan).
    MySQL connector pools do not expose an explicit close method,
    so we log and let the GC handle cleanup.
    """
    logger.info("Closing database connection pools.")


# =============================================================================
# CONTEXT MANAGERS
# =============================================================================

@contextmanager
def get_sabi() -> Generator[Tuple, None, None]:
    """
    Yields (connection, cursor) for the Sabi DB.
    Commits on clean exit. Rolls back and re-raises on exception.

    Example:
        with get_sabi() as (conn, cur):
            cur.execute("INSERT INTO teachers ...")
            # auto-committed on exit
    """
    if _sabi_pool is None:
        raise RuntimeError("Sabi DB pool is not initialised. Call init_pools() first.")

    conn = _sabi_pool.get_connection()
    cursor = conn.cursor(dictionary=True)  # rows as dicts, not tuples
    try:
        yield conn, cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()  # returns connection to pool


@contextmanager
def get_enterprise() -> Generator[Tuple, None, None]:
    """
    Yields (connection, cursor) for the Enterprise DB (read-only).
    No commit is issued — this DB should only be read.

    Example:
        with get_enterprise() as (_, cur):
            cur.execute("SELECT * FROM students WHERE ...")
            rows = cur.fetchall()
    """
    if _enterprise_pool is None:
        raise RuntimeError("Enterprise DB pool is not initialised. Call init_pools() first.")

    conn = _enterprise_pool.get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        yield conn, cursor
    finally:
        cursor.close()
        conn.close()


@contextmanager
def get_moodle() -> Generator[Tuple, None, None]:
    """
    Yields (connection, cursor) for the Moodle DB (read-only).
    No commit is issued — this DB should only be read.
    """
    if _moodle_pool is None:
        raise RuntimeError("Moodle DB pool is not initialised. Call init_pools() first.")

    conn = _moodle_pool.get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        yield conn, cursor
    finally:
        cursor.close()
        conn.close()


# =============================================================================
# HEALTH CHECKS
# =============================================================================

def check_all_connections() -> dict:
    """
    Ping all three databases. Returns a status dict.
    Used by the /health endpoint.
    """
    results = {}
    for name, pool in [
        ("sabi", _sabi_pool),
        ("enterprise", _enterprise_pool),
        ("moodle", _moodle_pool),
    ]:
        if pool is None:
            results[name] = {"status": "not_initialised"}
            continue
        try:
            conn = pool.get_connection()
            conn.ping(reconnect=True)
            conn.close()
            results[name] = {"status": "ok"}
        except MySQLError as e:
            results[name] = {"status": "error", "detail": str(e)}

    return results
