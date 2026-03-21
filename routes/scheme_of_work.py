"""
routes/scheme_of_work.py — curriculum progress tracking.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()


class SchemeCreate(BaseModel):
    teacher_id: int
    term_id: int
    subject: str
    class_name: str
    total_topics: int
    topics_covered: int = 0


class SchemeProgressUpdate(BaseModel):
    topics_covered: int


@router.post("", status_code=status.HTTP_201_CREATED)
def create_scheme(body: SchemeCreate):
    if body.total_topics <= 0:
        raise HTTPException(status_code=422, detail="total_topics must be > 0.")
    if body.topics_covered < 0 or body.topics_covered > body.total_topics:
        raise HTTPException(status_code=422, detail="topics_covered must be between 0 and total_topics.")

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE", (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")
        cur.execute("SELECT id FROM academic_terms WHERE id=%s", (body.term_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")
        cur.execute(
            """
            INSERT INTO scheme_of_work
                (teacher_id, term_id, subject, class_name, total_topics, topics_covered, last_updated)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                total_topics = VALUES(total_topics),
                topics_covered = VALUES(topics_covered),
                last_updated = NOW()
            """,
            (body.teacher_id, body.term_id, body.subject, body.class_name, body.total_topics, body.topics_covered),
        )
        new_id = cur.lastrowid
    return {"id": new_id, "message": "Scheme of work saved."}


@router.patch("/{scheme_id}/progress")
def update_scheme_progress(scheme_id: int, body: SchemeProgressUpdate):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id, total_topics FROM scheme_of_work WHERE id = %s", (scheme_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Scheme record not found.")
        if body.topics_covered < 0 or body.topics_covered > row["total_topics"]:
            raise HTTPException(status_code=422, detail="topics_covered must be between 0 and total_topics.")
        cur.execute(
            "UPDATE scheme_of_work SET topics_covered = %s, last_updated = NOW() WHERE id = %s",
            (body.topics_covered, scheme_id),
        )
    return {"message": "Scheme progress updated."}


@router.get("/teacher/{teacher_id}")
def get_teacher_scheme(teacher_id: int, term_id: Optional[int] = None):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s", (teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found.")
        query = "SELECT * FROM scheme_of_work WHERE teacher_id = %s"
        params = [teacher_id]
        if term_id:
            query += " AND term_id = %s"
            params.append(term_id)
        query += " ORDER BY term_id DESC, class_name, subject"
        cur.execute(query, params)
        return {"teacher_id": teacher_id, "records": cur.fetchall()}


@router.get("/term/{term_id}")
def get_term_scheme(term_id: int):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM academic_terms WHERE id=%s", (term_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")
        cur.execute(
            """
            SELECT sw.*, CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                   ROUND((sw.topics_covered / NULLIF(sw.total_topics, 0)) * 100, 1) AS completion_pct
            FROM scheme_of_work sw
            JOIN teachers t ON t.id = sw.teacher_id
            WHERE sw.term_id = %s
            ORDER BY teacher_name, sw.class_name, sw.subject
            """,
            (term_id,),
        )
        return {"term_id": term_id, "records": cur.fetchall()}
