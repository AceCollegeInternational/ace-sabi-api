"""
routes/staff_roles.py — manage leadership and scoped staff role assignments.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()
VALID_ROLES = (
    "principal",
    "vice_principal",
    "hr",
    "admin",
    "hod",
    "class_teacher",
    "year_tutor",
)


class StaffRoleCreate(BaseModel):
    teacher_id: int
    role: str
    assigned_on: date
    subject_scope: Optional[str] = None
    class_scope: Optional[str] = None
    level_scope: Optional[str] = None
    assigned_by: Optional[str] = None
    notes: Optional[str] = None


def _validate_role(role: str):
    if role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of: {VALID_ROLES}")


def _validate_scope_requirements(body: StaffRoleCreate):
    if body.role == "hod" and not body.subject_scope:
        raise HTTPException(status_code=422, detail="subject_scope is required for hod role.")
    if body.role == "class_teacher" and not body.class_scope:
        raise HTTPException(status_code=422, detail="class_scope is required for class_teacher role.")
    if body.role == "year_tutor" and not body.level_scope:
        raise HTTPException(status_code=422, detail="level_scope is required for year_tutor role.")


@router.post("", status_code=status.HTTP_201_CREATED)
def assign_role(body: StaffRoleCreate):
    _validate_role(body.role)
    _validate_scope_requirements(body)

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE", (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute(
            """
            UPDATE staff_roles
            SET    is_active = FALSE
            WHERE  role = %s
            AND    is_active = TRUE
            AND    COALESCE(subject_scope, '') = COALESCE(%s, '')
            AND    COALESCE(class_scope, '') = COALESCE(%s, '')
            AND    COALESCE(level_scope, '') = COALESCE(%s, '')
            """,
            (body.role, body.subject_scope, body.class_scope, body.level_scope),
        )
        cur.execute(
            """
            INSERT INTO staff_roles (
                teacher_id, role, subject_scope, class_scope, level_scope,
                is_active, assigned_on, assigned_by, notes
            )
            VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, %s)
            """,
            (
                body.teacher_id,
                body.role,
                body.subject_scope,
                body.class_scope,
                body.level_scope,
                body.assigned_on,
                body.assigned_by,
                body.notes,
            ),
        )
        new_id = cur.lastrowid

    return {"id": new_id, "message": "Staff role assigned."}


@router.get("")
def list_roles(active_only: bool = True, role: Optional[str] = None):
    query = """
        SELECT sr.*, CONCAT(t.first_name, ' ', t.last_name) AS teacher_name
        FROM   staff_roles sr
        JOIN   teachers t ON t.id = sr.teacher_id
        WHERE  1=1
    """
    params = []
    if active_only:
        query += " AND sr.is_active = TRUE"
    if role:
        _validate_role(role)
        query += " AND sr.role = %s"
        params.append(role)
    query += " ORDER BY sr.is_active DESC, sr.role, sr.assigned_on DESC"

    with get_sabi() as (_, cur):
        cur.execute(query, params)
        return {"roles": cur.fetchall()}


@router.get("/role/{role}")
def get_role_holders(role: str, active_only: bool = True):
    _validate_role(role)
    query = """
        SELECT sr.*, CONCAT(t.first_name, ' ', t.last_name) AS teacher_name
        FROM   staff_roles sr
        JOIN   teachers t ON t.id = sr.teacher_id
        WHERE  sr.role = %s
    """
    params = [role]
    if active_only:
        query += " AND sr.is_active = TRUE"
    query += " ORDER BY sr.is_active DESC, sr.assigned_on DESC"

    with get_sabi() as (_, cur):
        cur.execute(query, params)
        return {"role": role, "records": cur.fetchall()}


@router.get("/hod/{subject}")
def get_hod_for_subject(subject: str):
    with get_sabi() as (_, cur):
        cur.execute(
            """
            SELECT sr.*, CONCAT(t.first_name, ' ', t.last_name) AS teacher_name
            FROM   staff_roles sr
            JOIN   teachers t ON t.id = sr.teacher_id
            WHERE  sr.role = 'hod'
            AND    sr.is_active = TRUE
            AND    LOWER(sr.subject_scope) = LOWER(%s)
            ORDER  BY sr.assigned_on DESC
            LIMIT  1
            """,
            (subject,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No active HOD found for that subject.")
        return row


@router.get("/principal")
def get_current_principal():
    with get_sabi() as (_, cur):
        cur.execute(
            """
            SELECT sr.*, CONCAT(t.first_name, ' ', t.last_name) AS teacher_name
            FROM   staff_roles sr
            JOIN   teachers t ON t.id = sr.teacher_id
            WHERE  sr.role = 'principal'
            AND    sr.is_active = TRUE
            ORDER  BY sr.assigned_on DESC
            LIMIT  1
            """
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No active principal found.")
        return row


@router.patch("/{role_id}/deactivate")
def deactivate_role_patch(role_id: int):
    return _deactivate_role(role_id)


@router.delete("/{role_id}")
def deactivate_role_delete(role_id: int):
    return _deactivate_role(role_id)


def _deactivate_role(role_id: int):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id, is_active FROM staff_roles WHERE id = %s", (role_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Role assignment not found.")
        if not row["is_active"]:
            raise HTTPException(status_code=409, detail="Role assignment already inactive.")
        cur.execute("UPDATE staff_roles SET is_active = FALSE WHERE id = %s", (role_id,))
    return {"message": "Role assignment deactivated."}
