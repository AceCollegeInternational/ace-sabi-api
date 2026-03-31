"""
routes/attendance_confirmations.py — class register confirmation tracking.
"""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()
VALID_SESSIONS = ("morning", "noon")


class AttendanceConfirmationCreate(BaseModel):
    teacher_id: int
    term_id: int
    enterprise_class_id: str
    confirm_date: date
    session: str


@router.post("/confirm", status_code=status.HTTP_201_CREATED)
def confirm_register(body: AttendanceConfirmationCreate):
    if body.session not in VALID_SESSIONS:
        raise HTTPException(status_code=422, detail=f"session must be one of: {VALID_SESSIONS}")

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE", (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")
        cur.execute("SELECT id FROM academic_terms WHERE id=%s", (body.term_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")
        cur.execute(
            """
            INSERT INTO attendance_confirmations
                (teacher_id, term_id, enterprise_class_id, confirm_date, session, confirmed_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE confirmed_at = NOW()
            """,
            (body.teacher_id, body.term_id, body.enterprise_class_id, body.confirm_date, body.session),
        )
    return {"message": "Attendance confirmation recorded."}


@router.get("/pending")
def pending_confirmations(term_id: Optional[int] = None, confirm_date: Optional[date] = None, session: str = "morning"):
    if session not in VALID_SESSIONS:
        raise HTTPException(status_code=422, detail=f"session must be one of: {VALID_SESSIONS}")
    with get_sabi() as (_, cur):
        if not term_id:
            cur.execute("SELECT id FROM academic_terms WHERE is_current=TRUE LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No current term set.")
            term_id = row["id"]
        confirm_date = confirm_date or date.today()
        cur.execute(
            """
            SELECT ta.teacher_id,
                   CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                   ta.enterprise_class_id,
                   ta.class_name
            FROM teacher_assignments ta
            JOIN teachers t ON t.id = ta.teacher_id
            LEFT JOIN attendance_confirmations ac
              ON ac.teacher_id = ta.teacher_id
             AND ac.enterprise_class_id = ta.enterprise_class_id
             AND ac.confirm_date = %s
             AND ac.session = %s
            WHERE ta.term_id = %s
              AND ac.id IS NULL
            ORDER BY teacher_name, ta.class_name
            """,
            (confirm_date, session, term_id),
        )
        return {
            "term_id": term_id,
            "confirm_date": confirm_date,
            "session": session,
            "pending": cur.fetchall(),
        }


@router.get("/teacher/{teacher_id}")
def teacher_confirmation_history(teacher_id: int, term_id: Optional[int] = None):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s", (teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found.")
        query = "SELECT * FROM attendance_confirmations WHERE teacher_id = %s"
        params = [teacher_id]
        if term_id:
            query += " AND term_id = %s"
            params.append(term_id)
        query += " ORDER BY confirm_date DESC, session"
        cur.execute(query, params)
        return {"teacher_id": teacher_id, "records": cur.fetchall()}
