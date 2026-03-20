"""
routes/observations.py — Classroom observation records.

Endpoints:
    POST /observations                    log a new observation
    GET  /observations/teacher/{id}       all observations for a teacher
    POST /observations/{id}/share         mark as shared with teacher
"""

from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator

from database.connections import get_sabi

router = APIRouter()


class ObservationCreate(BaseModel):
    teacher_id:             int
    term_id:                int
    observed_on:            date
    observer_name:          str
    subject:                Optional[str] = None
    class_name:             Optional[str] = None
    score_questioning:      int   # 0–25
    score_engagement:       int
    score_differentiation:  int
    score_pacing:           int
    strengths:              Optional[str] = None
    areas_to_improve:       Optional[str] = None

    @field_validator("score_questioning","score_engagement",
                     "score_differentiation","score_pacing")
    @classmethod
    def check_range(cls, v):
        if not (0 <= v <= 25):
            raise ValueError("Each rubric score must be between 0 and 25.")
        return v


@router.post("", status_code=status.HTTP_201_CREATED)
def create_observation(body: ObservationCreate):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id = %s AND is_active = TRUE",
                    (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute("""
            INSERT INTO lesson_observations
                (teacher_id, term_id, observed_on, observer_name, subject,
                 class_name, score_questioning, score_engagement,
                 score_differentiation, score_pacing,
                 strengths, areas_to_improve)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            body.teacher_id, body.term_id, body.observed_on,
            body.observer_name, body.subject, body.class_name,
            body.score_questioning, body.score_engagement,
            body.score_differentiation, body.score_pacing,
            body.strengths, body.areas_to_improve
        ))
        obs_id = cur.lastrowid
        total  = (body.score_questioning + body.score_engagement
                  + body.score_differentiation + body.score_pacing)

    return {"id": obs_id, "total_score": total, "message": "Observation recorded."}


@router.get("/teacher/{teacher_id}")
def get_teacher_observations(teacher_id: int, term_id: Optional[int] = None):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id = %s", (teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found.")

        if term_id:
            cur.execute("""
                SELECT * FROM lesson_observations
                WHERE  teacher_id = %s AND term_id = %s
                ORDER  BY observed_on DESC
            """, (teacher_id, term_id))
        else:
            cur.execute("""
                SELECT * FROM lesson_observations
                WHERE  teacher_id = %s
                ORDER  BY observed_on DESC
            """, (teacher_id,))

        return cur.fetchall()


@router.post("/{obs_id}/share")
def share_with_teacher(obs_id: int):
    """Mark an observation as shared with the teacher (via Telegram)."""
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM lesson_observations WHERE id = %s", (obs_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Observation not found.")
        cur.execute("""
            UPDATE lesson_observations
            SET    shared_with_teacher = TRUE, shared_at = NOW()
            WHERE  id = %s
        """, (obs_id,))
    return {"message": "Marked as shared."}
