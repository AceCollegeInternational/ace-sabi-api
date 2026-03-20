"""
routes/terms.py — Academic term management.

Endpoints:
    GET  /terms              list all terms
    GET  /terms/current      get the active term
    GET  /terms/{id}         get single term
    POST /terms              create a new term
    POST /terms/{id}/set-current   mark a term as current
"""

from datetime import date
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, model_validator

from database.connections import get_sabi

router = APIRouter()


# =============================================================================
# MODELS
# =============================================================================

class TermCreate(BaseModel):
    term_name:     str
    academic_year: str   # e.g. "2025/2026"
    term_number:   int   # 1, 2, or 3
    start_date:    date
    end_date:      date

    @model_validator(mode="after")
    def validate(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date.")
        if self.term_number not in (1, 2, 3):
            raise ValueError("term_number must be 1, 2, or 3.")
        return self


# =============================================================================
# ROUTES
# =============================================================================

@router.get("")
def list_terms():
    """Return all academic terms, most recent first."""
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT id, term_name, academic_year, term_number,
                   start_date, end_date, is_current, created_at
            FROM   academic_terms
            ORDER  BY academic_year DESC, term_number DESC
        """)
        return cur.fetchall()


@router.get("/current")
def get_current_term():
    """Return the term currently marked as active."""
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT id, term_name, academic_year, term_number,
                   start_date, end_date, is_current, created_at
            FROM   academic_terms
            WHERE  is_current = TRUE
            LIMIT  1
        """)
        row = cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No current term set. Create a term and call /set-current."
        )
    return row


@router.get("/{term_id}")
def get_term(term_id: int):
    with get_sabi() as (_, cur):
        cur.execute("SELECT * FROM academic_terms WHERE id = %s", (term_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Term not found.")
    return row


@router.post("", status_code=status.HTTP_201_CREATED)
def create_term(body: TermCreate):
    """
    Create a new academic term.
    Does NOT automatically become current — call /set-current explicitly.
    """
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT id FROM academic_terms
            WHERE  academic_year = %s AND term_number = %s
        """, (body.academic_year, body.term_number))
        if cur.fetchone():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Term {body.term_number} for {body.academic_year} already exists."
            )
        cur.execute("""
            INSERT INTO academic_terms
                (term_name, academic_year, term_number, start_date, end_date)
            VALUES (%s, %s, %s, %s, %s)
        """, (body.term_name, body.academic_year, body.term_number,
              body.start_date, body.end_date))
        new_id = cur.lastrowid
    return {"id": new_id, "message": "Term created."}


@router.post("/{term_id}/set-current")
def set_current_term(term_id: int):
    """
    Mark a term as active. Clears is_current on all other terms first.
    Only one term can be current at any time.
    """
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM academic_terms WHERE id = %s", (term_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")
        cur.execute("UPDATE academic_terms SET is_current = FALSE")
        cur.execute("UPDATE academic_terms SET is_current = TRUE WHERE id = %s", (term_id,))
    return {"message": f"Term {term_id} is now current."}
