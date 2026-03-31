"""
routes/teacher_assignments.py — teacher/class/subject assignment management.
"""

from typing import List

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()


class AssignmentRow(BaseModel):
    teacher_id: int
    term_id: int
    enterprise_class_id: str
    enterprise_subject_id: str
    class_name: str
    subject_name: str


class AssignmentSyncRequest(BaseModel):
    assignments: List[AssignmentRow]


@router.post("/sync", status_code=status.HTTP_201_CREATED)
def sync_assignments(body: AssignmentSyncRequest):
    if not body.assignments:
        raise HTTPException(status_code=422, detail="assignments list is empty.")

    saved = 0
    errors = []
    with get_sabi() as (_, cur):
        for row in body.assignments:
            try:
                cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE", (row.teacher_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"Teacher {row.teacher_id} not found.")
                cur.execute("SELECT id FROM academic_terms WHERE id=%s", (row.term_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"Term {row.term_id} not found.")
                cur.execute(
                    """
                    INSERT INTO teacher_assignments
                        (teacher_id, term_id, enterprise_class_id, enterprise_subject_id, class_name, subject_name)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        class_name = VALUES(class_name),
                        subject_name = VALUES(subject_name)
                    """,
                    (
                        row.teacher_id,
                        row.term_id,
                        row.enterprise_class_id,
                        row.enterprise_subject_id,
                        row.class_name,
                        row.subject_name,
                    ),
                )
                saved += 1
            except HTTPException as exc:
                errors.append({"teacher_id": row.teacher_id, "error": exc.detail})

    return {"saved": saved, "errors": errors}


@router.get("/teacher/{teacher_id}")
def get_teacher_assignments(teacher_id: int):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s", (teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found.")
        cur.execute(
            """
            SELECT ta.*, at.term_name
            FROM teacher_assignments ta
            JOIN academic_terms at ON at.id = ta.term_id
            WHERE ta.teacher_id = %s
            ORDER BY ta.term_id DESC, ta.class_name, ta.subject_name
            """,
            (teacher_id,),
        )
        return {"teacher_id": teacher_id, "assignments": cur.fetchall()}


@router.get("/term/{term_id}")
def get_term_assignments(term_id: int):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM academic_terms WHERE id=%s", (term_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")
        cur.execute(
            """
            SELECT ta.*, CONCAT(t.first_name, ' ', t.last_name) AS teacher_name
            FROM teacher_assignments ta
            JOIN teachers t ON t.id = ta.teacher_id
            WHERE ta.term_id = %s
            ORDER BY ta.class_name, ta.subject_name, teacher_name
            """,
            (term_id,),
        )
        return {"term_id": term_id, "assignments": cur.fetchall()}
