"""
routes/marking_timeliness.py — Track how promptly teachers return marked work.

Endpoints:
    POST /marking/assessment              register a new assessment
    POST /marking/assessment/{id}/submit  record when scores were submitted
    GET  /marking/teacher/{id}            all records for a teacher
    GET  /marking/summary/{term_id}       school-wide compliance summary
"""

from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi
import config

router = APIRouter()


class AssessmentCreate(BaseModel):
    teacher_id:      int
    term_id:         int
    assessment_name: str
    class_name:      Optional[str] = None
    assessment_date: date
    policy_days:     Optional[int] = None   # defaults to config value if not set


class ScoreSubmission(BaseModel):
    pass   # submission timestamp is captured server-side via NOW()


@router.post("/assessment", status_code=status.HTTP_201_CREATED)
def create_assessment(body: AssessmentCreate):
    """Register an assessment. Scores not yet submitted."""
    policy = body.policy_days or config.DEFAULT_MARKING_POLICY_DAYS
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id = %s AND is_active = TRUE",
                    (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute("""
            INSERT INTO marking_timeliness
                (teacher_id, term_id, assessment_name, class_name,
                 assessment_date, policy_days)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (body.teacher_id, body.term_id, body.assessment_name,
              body.class_name, body.assessment_date, policy))
        new_id = cur.lastrowid

    return {"id": new_id, "message": "Assessment registered."}


@router.post("/assessment/{assessment_id}/submit")
def record_submission(assessment_id: int):
    """
    Record that the teacher has submitted scores for this assessment.
    Automatically computes days_to_submit from assessment_date to now.
    """
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT id, assessment_date FROM marking_timeliness WHERE id = %s
        """, (assessment_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Assessment not found.")

        cur.execute("""
            UPDATE marking_timeliness
            SET    scores_submitted_at = NOW(),
                   days_to_submit = DATEDIFF(NOW(), assessment_date)
            WHERE  id = %s
        """, (assessment_id,))

    return {"message": "Score submission recorded."}


@router.get("/teacher/{teacher_id}")
def get_teacher_marking(teacher_id: int, term_id: Optional[int] = None):
    with get_sabi() as (_, cur):
        base = """
            SELECT * FROM marking_timeliness
            WHERE  teacher_id = %s
        """
        if term_id:
            cur.execute(base + " AND term_id = %s ORDER BY assessment_date DESC",
                        (teacher_id, term_id))
        else:
            cur.execute(base + " ORDER BY assessment_date DESC", (teacher_id,))
        return cur.fetchall()


@router.get("/summary/{term_id}")
def marking_summary(term_id: int):
    """Per-teacher marking timeliness summary for a term."""
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                t.id                                        AS teacher_id,
                CONCAT(t.first_name,' ',t.last_name)        AS teacher_name,
                COUNT(mt.id)                                AS total_assessments,
                SUM(mt.scores_submitted_at IS NOT NULL)     AS submitted_count,
                SUM(mt.is_compliant = TRUE)                 AS compliant_count,
                ROUND(AVG(mt.days_to_submit), 1)            AS avg_days_to_submit,
                ROUND(
                  100.0 * SUM(mt.is_compliant = TRUE)
                  / NULLIF(SUM(mt.scores_submitted_at IS NOT NULL),0),
                1)                                          AS compliance_pct
            FROM       teachers t
            LEFT JOIN  marking_timeliness mt
                ON     mt.teacher_id = t.id AND mt.term_id = %s
            WHERE      t.is_active = TRUE
            GROUP BY   t.id, t.first_name, t.last_name
            ORDER BY   compliance_pct DESC
        """, (term_id,))
        return {"term_id": term_id, "summary": cur.fetchall()}
