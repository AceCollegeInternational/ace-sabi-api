"""
routes/pastoral_logs.py — Pastoral, welfare, discipline and positive logs.

All four endpoints read from and write to the single pastoral_logs table.
This data feeds two KPI indices:
  - pastoral_logs  : total count of logs filed by the teacher
  - incident_rate  : ratio of discipline logs to total logs (inverted score)

Endpoints:
    POST /pastoral-logs                              log a new observation
    GET  /pastoral-logs/teacher/{teacher_id}         all logs by a teacher
    GET  /pastoral-logs/student/{student_id}         all logs for a student
    GET  /pastoral-logs/term/{term_id}               all logs for a term
"""

from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()

LOG_TYPES = ("welfare", "discipline", "pastoral", "positive")


# =============================================================================
# MODELS
# =============================================================================

class PastoralLogCreate(BaseModel):
    teacher_id:            int
    term_id:               int
    # enterprise_student_id links to the student record in the enterprise DB
    enterprise_student_id: str
    log_type:              str
    description:           str
    action_taken:          Optional[str]  = None
    follow_up_date:        Optional[date] = None
    parent_notified:       bool           = False


class PastoralLogUpdate(BaseModel):
    follow_up_done:     Optional[bool] = None
    parent_notified:    Optional[bool] = None
    parent_notified_at: Optional[str]  = None
    action_taken:       Optional[str]  = None


# =============================================================================
# ROUTES
# =============================================================================

@router.post("", status_code=status.HTTP_201_CREATED)
def create_log(body: PastoralLogCreate):
    """
    Log a pastoral, welfare, discipline, or positive observation.

    log_type values:
      welfare    — student welfare concern (health, home situation, etc.)
      discipline — behavioural incident in or outside class
      pastoral   — general pastoral check-in or conversation
      positive   — positive behaviour, achievement, or commendation

    enterprise_student_id is the student's ID from the enterprise DB.
    """
    if body.log_type not in LOG_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"log_type must be one of: {LOG_TYPES}"
        )

    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT id FROM teachers WHERE id = %s AND is_active = TRUE",
            (body.teacher_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute(
            "SELECT id FROM academic_terms WHERE id = %s",
            (body.term_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")

        cur.execute("""
            INSERT INTO pastoral_logs
                (teacher_id, term_id, enterprise_student_id, log_type,
                 description, action_taken, follow_up_date, parent_notified)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            body.teacher_id, body.term_id, body.enterprise_student_id,
            body.log_type, body.description, body.action_taken,
            body.follow_up_date, body.parent_notified
        ))
        new_id = cur.lastrowid

    return {"id": new_id, "message": "Pastoral log recorded."}


@router.patch("/{log_id}")
def update_log(log_id: int, body: PastoralLogUpdate):
    """
    Update follow-up status or parent notification on an existing log.
    Only the fields provided are updated — others remain unchanged.
    """
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM pastoral_logs WHERE id = %s", (log_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Log not found.")

        updates = []
        params  = []

        if body.follow_up_done is not None:
            updates.append("follow_up_done = %s")
            params.append(body.follow_up_done)

        if body.parent_notified is not None:
            updates.append("parent_notified = %s")
            params.append(body.parent_notified)

        if body.parent_notified_at is not None:
            updates.append("parent_notified_at = %s")
            params.append(body.parent_notified_at)

        if body.action_taken is not None:
            updates.append("action_taken = %s")
            params.append(body.action_taken)

        if not updates:
            raise HTTPException(status_code=422, detail="No fields to update.")

        params.append(log_id)
        cur.execute(
            f"UPDATE pastoral_logs SET {', '.join(updates)} WHERE id = %s",
            params
        )

    return {"message": "Log updated."}


@router.get("/teacher/{teacher_id}")
def get_by_teacher(
    teacher_id: int,
    term_id:    Optional[int] = None,
    log_type:   Optional[str] = None
):
    """
    All pastoral logs filed by a specific teacher.
    Optionally filter by term and/or log_type.
    """
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id = %s", (teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found.")

        query  = "SELECT * FROM pastoral_logs WHERE teacher_id = %s"
        params = [teacher_id]

        if term_id:
            query += " AND term_id = %s"
            params.append(term_id)

        if log_type:
            if log_type not in LOG_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=f"log_type must be one of: {LOG_TYPES}"
                )
            query += " AND log_type = %s"
            params.append(log_type)

        query += " ORDER BY created_at DESC"
        cur.execute(query, params)
        return cur.fetchall()


@router.get("/student/{enterprise_student_id}")
def get_by_student(
    enterprise_student_id: str,
    term_id:               Optional[int] = None,
    log_type:              Optional[str] = None
):
    """
    All pastoral logs for a specific student across all teachers.
    enterprise_student_id is the student's ID from the enterprise DB.
    Optionally filter by term and/or log_type.
    """
    with get_sabi() as (_, cur):
        query  = """
            SELECT
                pl.*,
                CONCAT(t.first_name, ' ', t.last_name) AS teacher_name
            FROM   pastoral_logs pl
            JOIN   teachers t ON t.id = pl.teacher_id
            WHERE  pl.enterprise_student_id = %s
        """
        params = [enterprise_student_id]

        if term_id:
            query += " AND pl.term_id = %s"
            params.append(term_id)

        if log_type:
            if log_type not in LOG_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=f"log_type must be one of: {LOG_TYPES}"
                )
            query += " AND pl.log_type = %s"
            params.append(log_type)

        query += " ORDER BY pl.created_at DESC"
        cur.execute(query, params)
        return cur.fetchall()


@router.get("/term/{term_id}")
def get_by_term(
    term_id:  int,
    log_type: Optional[str] = None
):
    """
    All pastoral logs for an entire term with teacher names.
    Optionally filter by log_type.
    Returns a summary count by type alongside the full record list.
    """
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM academic_terms WHERE id = %s", (term_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")

        # Summary counts by type
        cur.execute("""
            SELECT
                log_type,
                COUNT(*) AS count
            FROM   pastoral_logs
            WHERE  term_id = %s
            GROUP  BY log_type
        """, (term_id,))
        summary = cur.fetchall()

        # Full records
        query  = """
            SELECT
                pl.*,
                CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                t.subject_primary
            FROM   pastoral_logs pl
            JOIN   teachers t ON t.id = pl.teacher_id
            WHERE  pl.term_id = %s
        """
        params = [term_id]

        if log_type:
            if log_type not in LOG_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=f"log_type must be one of: {LOG_TYPES}"
                )
            query += " AND pl.log_type = %s"
            params.append(log_type)

        query += " ORDER BY pl.created_at DESC"
        cur.execute(query, params)
        records = cur.fetchall()

    return {
        "term_id": term_id,
        "summary": summary,
        "records": records,
    }


@router.get("/pending-contact")
def get_pending_contact(
    term_id:     Optional[int] = None,
    teacher_id:  Optional[int] = None,
    days_old:    int           = 1,
):
    """
    Return pastoral logs that require parent contact but have not yet
    been followed up.

    Filters:
      - log_type is 'welfare' or 'discipline' — these are the types
        that typically require parent notification
      - parent_notified = FALSE
      - Log was created at least days_old days ago (default 1)
        This avoids flagging logs filed today that the teacher has not
        yet had time to act on.

    Optional filters:
      - term_id: defaults to current term
      - teacher_id: filter to one teacher's logs only

    Results ordered by oldest first so the most overdue appear at the top.
    Used by:
      - Principal's morning brief — school-wide outstanding contacts
      - Teacher's daily reminder — their own pending contacts
    """
    with get_sabi() as (_, cur):
        if not term_id:
            cur.execute(
                "SELECT id FROM academic_terms WHERE is_current = TRUE LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No current term set.")
            term_id = row["id"]

        query = """
            SELECT
                pl.id,
                pl.teacher_id,
                CONCAT(t.first_name, ' ', t.last_name)  AS teacher_name,
                pl.term_id,
                pl.enterprise_student_id,
                pl.log_type,
                pl.description,
                pl.action_taken,
                pl.follow_up_date,
                pl.parent_notified,
                pl.created_at,
                DATEDIFF(NOW(), pl.created_at)           AS days_since_logged
            FROM   pastoral_logs pl
            JOIN   teachers t ON t.id = pl.teacher_id
            WHERE  pl.term_id        = %s
            AND    pl.parent_notified = FALSE
            AND    pl.log_type        IN ('welfare', 'discipline')
            AND    DATEDIFF(NOW(), pl.created_at) >= %s
        """
        params = [term_id, days_old]

        if teacher_id:
            query += " AND pl.teacher_id = %s"
            params.append(teacher_id)

        query += " ORDER BY pl.created_at ASC"

        cur.execute(query, params)
        records = cur.fetchall()

    return {
        "term_id":        term_id,
        "pending_count":  len(records),
        "days_threshold": days_old,
        "records":        records,
    }
