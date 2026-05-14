"""
routes/homework_logs.py — weekly homework tracking.
"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()


class HomeworkLogCreate(BaseModel):
    teacher_id: int
    term_id: int
    class_name: str
    subject: str
    week_number: int
    given_on: date
    due_date: Optional[date] = None
    description: Optional[str] = None


@router.post("", status_code=status.HTTP_201_CREATED)
def log_homework(body: HomeworkLogCreate):
    if not 1 <= body.week_number <= 20:
        raise HTTPException(status_code=422, detail="week_number must be between 1 and 20.")
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE", (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")
        cur.execute("SELECT id FROM academic_terms WHERE id=%s", (body.term_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")

        # Auto-calculate due_date as Friday of the week given_on falls in
        # This is the teacher's compliance deadline for logging homework
        if body.due_date is None:
            days_until_friday = (4 - body.given_on.weekday()) % 7
            body.due_date = body.given_on + timedelta(days=days_until_friday)

        cur.execute(
            """
            INSERT INTO homework_logs
                (teacher_id, term_id, class_name, subject, week_number, given_on, due_date, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                given_on = VALUES(given_on),
                due_date = VALUES(due_date),
                description = VALUES(description)
            """,
            (body.teacher_id, body.term_id, body.class_name, body.subject, body.week_number, body.given_on, body.due_date, body.description),
        )
        new_id = cur.lastrowid
    return {"id": new_id, "message": "Homework log recorded."}


@router.get("/teacher/{teacher_id}")
def get_teacher_homework(teacher_id: int, term_id: Optional[int] = None, week_number: Optional[int] = None):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s", (teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found.")
        query = "SELECT * FROM homework_logs WHERE teacher_id = %s"
        params = [teacher_id]
        if term_id:
            query += " AND term_id = %s"
            params.append(term_id)
        if week_number:
            query += " AND week_number = %s"
            params.append(week_number)
        query += " ORDER BY term_id DESC, week_number DESC, class_name, subject"
        cur.execute(query, params)
        return {"teacher_id": teacher_id, "records": cur.fetchall()}


@router.get("/class/{class_name}")
def get_class_homework(class_name: str, term_id: Optional[int] = None, week_number: Optional[int] = None):
    with get_sabi() as (_, cur):
        query = "SELECT * FROM homework_logs WHERE class_name = %s"
        params = [class_name]
        if term_id:
            query += " AND term_id = %s"
            params.append(term_id)
        if week_number:
            query += " AND week_number = %s"
            params.append(week_number)
        query += " ORDER BY week_number DESC, subject"
        cur.execute(query, params)
        return {"class_name": class_name, "records": cur.fetchall()}
