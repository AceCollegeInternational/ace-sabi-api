"""
routes/disciplinary.py — Disciplinary gateway (eligibility, not scoring).

Endpoints:
    POST /disciplinary                        issue a disciplinary action
    POST /disciplinary/{id}/resolve           resolve an active action
    GET  /disciplinary/{teacher_id}/eligible  is teacher eligible for incentive?
    GET  /disciplinary/{teacher_id}/history   full disciplinary history
"""

from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()

ACTION_TYPES = ("query","verbal_warning","written_warning","final_warning","suspension")


class DisciplinaryAction(BaseModel):
    teacher_id:  int
    term_id:     int
    action_type: str
    issued_date: date
    reason:      str
    issued_by:   str


class ResolutionUpdate(BaseModel):
    resolved_date:     date
    resolution_notes:  str


@router.post("", status_code=status.HTTP_201_CREATED)
def issue_action(body: DisciplinaryAction):
    if body.action_type not in ACTION_TYPES:
        raise HTTPException(status_code=422,
            detail=f"action_type must be one of: {ACTION_TYPES}")

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE",
                    (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute("""
            INSERT INTO disciplinary_gateway
                (teacher_id, term_id, action_type, issued_date,
                 reason, issued_by, is_active)
            VALUES (%s,%s,%s,%s,%s,%s,TRUE)
        """, (body.teacher_id, body.term_id, body.action_type,
              body.issued_date, body.reason, body.issued_by))
        new_id = cur.lastrowid

    return {
        "id":      new_id,
        "message": f"{body.action_type} issued. Teacher is now ineligible for incentive until this is resolved.",
    }


@router.post("/{action_id}/resolve")
def resolve_action(action_id: int, body: ResolutionUpdate):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id, is_active FROM disciplinary_gateway WHERE id=%s",
                    (action_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Action not found.")
        if not row["is_active"]:
            raise HTTPException(status_code=409, detail="Action already resolved.")

        cur.execute("""
            UPDATE disciplinary_gateway
            SET    is_active=FALSE, resolved_date=%s, resolution_notes=%s
            WHERE  id=%s
        """, (body.resolved_date, body.resolution_notes, action_id))

    return {"message": "Action resolved. Teacher eligibility restored (subject to KPI score)."}


@router.get("/{teacher_id}/eligible")
def check_eligibility(teacher_id: int):
    """
    Returns whether a teacher currently has any active disciplinary actions.
    If is_eligible = False, incentive cannot be paid regardless of KPI score.
    """
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT id, action_type, issued_date, reason
            FROM   disciplinary_gateway
            WHERE  teacher_id=%s AND is_active=TRUE
            ORDER  BY issued_date DESC
        """, (teacher_id,))
        active_actions = cur.fetchall()

    is_eligible = len(active_actions) == 0
    return {
        "teacher_id":      teacher_id,
        "is_eligible":     is_eligible,
        "active_actions":  active_actions,
        "ineligibility_reason": (
            None if is_eligible
            else f"{len(active_actions)} active disciplinary action(s) on record."
        ),
    }


@router.get("/{teacher_id}/history")
def get_history(teacher_id: int):
    """Full disciplinary history for a teacher, most recent first."""
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT dg.*, at.term_name
            FROM   disciplinary_gateway dg
            JOIN   academic_terms at ON at.id = dg.term_id
            WHERE  dg.teacher_id=%s
            ORDER  BY dg.issued_date DESC
        """, (teacher_id,))
        return cur.fetchall()
