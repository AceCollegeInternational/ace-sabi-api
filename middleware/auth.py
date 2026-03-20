"""
middleware/auth.py — API key authentication for all Sabi API endpoints.

How it works:
    1. Client sends the raw API key in the X-API-Key header.
    2. The middleware hashes the incoming key with SHA-256.
    3. It looks up the hash in sabi_db.api_keys where is_active = TRUE.
    4. If found, the request proceeds. If not, 401 is returned.
    5. last_used_at is updated on every successful authentication.

Generating a new API key (run this locally, then store the raw key securely):

    import secrets, hashlib
    raw_key  = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    print("Raw key (give to client):", raw_key)
    print("Hash  (insert to DB)    :", key_hash)

    INSERT INTO api_keys (key_hash, label) VALUES ('<hash>', 'openclaw-staff-bot');
"""

import hashlib
import logging
from typing import Optional

from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

from database.connections import get_sabi

logger = logging.getLogger(__name__)

# FastAPI reads the key from this header on every request
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _lookup_and_refresh(key_hash: str) -> Optional[dict]:
    """
    Check if the key hash exists and is active.
    Update last_used_at on success.
    Returns the api_keys row dict, or None if not found / inactive.
    """
    with get_sabi() as (conn, cur):
        cur.execute(
            """
            SELECT id, label, is_active
            FROM   api_keys
            WHERE  key_hash = %s
            LIMIT  1
            """,
            (key_hash,),
        )
        row = cur.fetchone()

        if row and row["is_active"]:
            cur.execute(
                "UPDATE api_keys SET last_used_at = NOW() WHERE id = %s",
                (row["id"],),
            )
            return row

    return None


async def require_api_key(raw_key: str = Security(_api_key_header)) -> dict:
    """
    FastAPI dependency. Inject into any route that requires authentication:

        @router.get("/teachers")
        def list_teachers(auth: dict = Depends(require_api_key)):
            ...

    Raises HTTP 401 if the key is missing or invalid.
    Returns the api_keys row dict (contains 'label') on success.
    """
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    key_hash = _hash_key(raw_key)

    try:
        key_record = _lookup_and_refresh(key_hash)
    except Exception as e:
        logger.error("Auth DB lookup failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable.",
        )

    if not key_record:
        logger.warning("Rejected request with invalid or inactive API key.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return key_record
