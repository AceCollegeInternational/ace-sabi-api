"""
routes/student_feedback.py — End-of-term anonymous student ratings.

Endpoints:
    POST /student-feedback              submit aggregated ratings for a class
    GET  /student-feedback/teacher/{id} all feedback for a teacher
    GET  /student-feedback/term/{id}    all feedback for a term
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator

from database.connections import get_sabi
import config

router = APIRouter()


class FeedbackSubmission(BaseModel):
    teacher_id:     int
    term_id:        int
    class_name:     str
    score_clarity:  int   # 0–100
    score_safety:   int
    score_care:     int
    response_count: int
    class_size:     int

    @field_validator("score_clarity","score_safety","score_care")
    @classmethod
    def check_score(cls, v):
        if not (0 <= v <= 100):
            raise ValueError("Scores must be between 0 and 100.")
        return v


@router.post("", status_code=status.HTTP_201_CREATED)
def submit_feedback(body: FeedbackSubmission):
    """
    Submit aggregated (not individual) student ratings for a teacher/class.
    Individual responses are never stored — only the class-level averages.
    If response rate is below the configured minimum threshold the record is
    still saved but the KPI engine will exclude it from scoring.
    """
    response_rate = (
        round(body.response_count / body.class_size * 100, 1)
        if body.class_size > 0 else 0.0
    )

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE",
                    (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute("""
            INSERT INTO student_feedback
                (teacher_id, term_id, class_name,
                 score_clarity, score_safety, score_care,
                 response_count, class_size)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                score_clarity  = VALUES(score_clarity),
                score_safety   = VALUES(score_safety),
                score_care     = VALUES(score_care),
                response_count = VALUES(response_count),
                class_size     = VALUES(class_size),
                collected_at   = NOW()
        """, (body.teacher_id, body.term_id, body.class_name,
              body.score_clarity, body.score_safety, body.score_care,
              body.response_count, body.class_size))

    meets_threshold = response_rate >= config.MIN_STUDENT_FEEDBACK_RESPONSE_RATE
    return {
        "message":         "Feedback recorded.",
        "response_rate":   response_rate,
        "meets_threshold": meets_threshold,
        "threshold":       config.MIN_STUDENT_FEEDBACK_RESPONSE_RATE,
    }


@router.get("/teacher/{teacher_id}")
def get_teacher_feedback(teacher_id: int, term_id: Optional[int] = None):
    with get_sabi() as (_, cur):
        if term_id:
            cur.execute("""
                SELECT *, ROUND(response_count/NULLIF(class_size,0)*100,1) AS response_rate_pct
                FROM   student_feedback
                WHERE  teacher_id=%s AND term_id=%s
                ORDER  BY class_name
            """, (teacher_id, term_id))
        else:
            cur.execute("""
                SELECT *, ROUND(response_count/NULLIF(class_size,0)*100,1) AS response_rate_pct
                FROM   student_feedback
                WHERE  teacher_id=%s
                ORDER  BY term_id, class_name
            """, (teacher_id,))
        return cur.fetchall()


@router.get("/term/{term_id}")
def get_term_feedback(term_id: int):
    """All student feedback for a term with teacher names."""
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                sf.*,
                CONCAT(t.first_name,' ',t.last_name) AS teacher_name,
                t.subject_primary,
                ROUND(sf.response_count/NULLIF(sf.class_size,0)*100,1) AS response_rate_pct
            FROM   student_feedback sf
            JOIN   teachers t ON t.id = sf.teacher_id
            WHERE  sf.term_id=%s
            ORDER  BY sf.aggregate_score DESC
        """, (term_id,))
        return cur.fetchall()
