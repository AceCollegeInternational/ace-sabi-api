"""
routes/staff_roles.py — manage principal/HR/admin/HOD role assignments.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()
VALID_ROLES = ("principal", "hr", "admin", "hod")


class StaffRoleCreate(BaseModel):
    teacher_id: int
    role: str
    assigned_on: date


@router.post("", status_code=status.HTTP_201_CREATED)
def assign_role(body: StaffRoleCreate):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of: {VALID_ROLES}")

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE", (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute(
            "UPDATE staff_roles SET is_active = FALSE WHERE role = %s AND is_active = TRUE",
            (body.role,),
        )
        cur.execute(
            """
            INSERT INTO staff_roles (teacher_id, role, is_active, assigned_on)
            VALUES (%s, %s, TRUE, %s)
            """,
            (body.teacher_id, body.role, body.assigned_on),
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
        if role not in VALID_ROLES:
            raise HTTPException(status_code=422, detail=f"role must be one of: {VALID_ROLES}")
        query += " AND sr.role = %s"
        params.append(role)
    query += " ORDER BY sr.is_active DESC, sr.role, sr.assigned_on DESC"

    with get_sabi() as (_, cur):
        cur.execute(query, params)
        return {"roles": cur.fetchall()}


@router.delete("/{role_id}")
def deactivate_role(role_id: int):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id, is_active FROM staff_roles WHERE id = %s", (role_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Role assignment not found.")
        if not row["is_active"]:
            raise HTTPException(status_code=409, detail="Role assignment already inactive.")
        cur.execute("UPDATE staff_roles SET is_active = FALSE WHERE id = %s", (role_id,))
    return {"message": "Role assignment deactivated."}
