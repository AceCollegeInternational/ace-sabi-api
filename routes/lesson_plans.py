"""
routes/lesson_plans.py — Weekly lesson plan submission tracking.

Endpoints:
    POST  /lesson-plans                   log or update a submission
    PATCH /lesson-plans/{id}/review       HOD marks plan as on-topic or not
    GET   /lesson-plans/compliance        compliance report for current term
    GET   /lesson-plans/teacher/{id}      all submissions for one teacher
"""

from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()


# =============================================================================
# MODELS
# =============================================================================

class PlanSubmission(BaseModel):
    teacher_id:     int
    term_id:        int
    week_number:    int           # 1–14
    due_date:       date
    file_reference: Optional[str] = None   # Google Drive link
    notes:          Optional[str] = None

class PlanReview(BaseModel):
    is_on_topic: bool
    notes:       Optional[str] = None


# =============================================================================
# ROUTES
# =============================================================================

@router.post("", status_code=status.HTTP_201_CREATED)
def submit_plan(body: PlanSubmission):
    """
    Record a lesson plan submission.
    If a record for this teacher/term/week already exists it is updated
    (teacher re-submitted a corrected plan).
    is_on_time is computed automatically from the submission timestamp vs due_date.
    """
    if not (1 <= body.week_number <= 20):
        raise HTTPException(status_code=422, detail="week_number must be between 1 and 20.")

    with get_sabi() as (_, cur):
        # Validate teacher and term exist
        cur.execute("SELECT id FROM teachers WHERE id = %s AND is_active = TRUE",
                    (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute("SELECT id FROM academic_terms WHERE id = %s", (body.term_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")

        now      = date.today()
        on_time  = (now <= body.due_date)

        cur.execute("""
            INSERT INTO lesson_plan_submissions
                (teacher_id, term_id, week_number, due_date,
                 submitted_at, is_on_time, file_reference, notes)
            VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                submitted_at   = NOW(),
                is_on_time     = VALUES(is_on_time),
                file_reference = VALUES(file_reference),
                notes          = VALUES(notes)
        """, (body.teacher_id, body.term_id, body.week_number,
              body.due_date, on_time, body.file_reference, body.notes))

        record_id = cur.lastrowid

    return {
        "id":        record_id,
        "is_on_time": on_time,
        "message":   "Plan submission recorded."
    }


@router.patch("/{plan_id}/review")
def review_plan(plan_id: int, body: PlanReview):
    """
    HOD or admin marks whether a submitted plan is on-topic
    (i.e. matches the expected scheme of work for that week).
    """
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM lesson_plan_submissions WHERE id = %s", (plan_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Submission not found.")

        cur.execute("""
            UPDATE lesson_plan_submissions
            SET    is_on_topic = %s, notes = COALESCE(%s, notes)
            WHERE  id = %s
        """, (body.is_on_topic, body.notes, plan_id))

    return {"message": "Review recorded."}


@router.get("/compliance")
def compliance_report(term_id: Optional[int] = None):
    """
    Per-teacher compliance report for a term (defaults to current term).
    Returns: weeks submitted, on-time count, on-topic count, compliance %.
    """
    with get_sabi() as (_, cur):
        if not term_id:
            cur.execute("SELECT id FROM academic_terms WHERE is_current = TRUE LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No current term set.")
            term_id = row["id"]

        cur.execute("""
            SELECT
                t.id                                            AS teacher_id,
                CONCAT(t.first_name, ' ', t.last_name)         AS teacher_name,
                t.subject_primary,
                COUNT(lp.id)                                    AS weeks_submitted,
                SUM(lp.is_on_time  = TRUE)                     AS on_time_count,
                SUM(lp.is_on_topic = TRUE)                     AS on_topic_count,
                ROUND(100.0 * SUM(lp.is_on_time = TRUE)
                      / NULLIF(COUNT(lp.id), 0), 1)            AS on_time_pct,
                ROUND(100.0 * SUM(lp.is_on_topic = TRUE)
                      / NULLIF(COUNT(lp.id), 0), 1)            AS on_topic_pct
            FROM       teachers t
            LEFT JOIN  lesson_plan_submissions lp
                ON     lp.teacher_id = t.id AND lp.term_id = %s
            WHERE      t.is_active = TRUE
            GROUP BY   t.id, t.first_name, t.last_name, t.subject_primary
            ORDER BY   on_time_pct DESC
        """, (term_id,))

        return {"term_id": term_id, "compliance": cur.fetchall()}


@router.get("/teacher/{teacher_id}")
def teacher_submissions(teacher_id: int, term_id: Optional[int] = None):
    """All lesson plan submissions for a single teacher."""
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id = %s", (teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found.")

        if term_id:
            cur.execute("""
                SELECT * FROM lesson_plan_submissions
                WHERE  teacher_id = %s AND term_id = %s
                ORDER  BY week_number
            """, (teacher_id, term_id))
        else:
            cur.execute("""
                SELECT * FROM lesson_plan_submissions
                WHERE  teacher_id = %s
                ORDER  BY term_id, week_number
            """, (teacher_id,))

        return cur.fetchall()
