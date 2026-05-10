"""
routes/staff_roles.py — manage leadership and scoped staff role assignments.

HOD roles use department-based scoping:
  subject_scope must be one of: Science | Humanities | Business

The SUBJECT_DEPARTMENTS dict maps enterprise DB subject names to departments.
Subjects not in the dict (primary level, non-academic) return no HOD.
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

VALID_DEPARTMENTS = ("Science", "Humanities", "Business")

# =============================================================================
# SUBJECT → DEPARTMENT MAPPING
# Keys must match enterprise DB tb_academic_subjects.subject_name exactly.
# Subjects not listed here are unmapped (primary level or non-academic).
# =============================================================================

SUBJECT_DEPARTMENTS: dict[str, str] = {
    # ── Science & Mathematics ─────────────────────────────────────────────
    "Mathematics":                    "Science",
    "Further Mathematics":            "Science",
    "Physics":                        "Science",
    "Chemistry":                      "Science",
    "Biology":                        "Science",
    "Agricultural Science":           "Science",
    "Basic Science":                  "Science",
    "Basic Science & Technology":     "Science",
    "Basic Technology":               "Science",
    "BST - Basic Science":            "Science",
    "BST - Basic Technology":         "Science",
    "BST - Information Technology":   "Science",
    "BST - Physical & Health Edu":    "Science",
    "Computer Studies":               "Science",
    "Computer Education/ICT":         "Science",
    "Data Processing":                "Science",
    "Elementary Science":             "Science",
    "Food & Nutrition":               "Science",
    "Home Economics":                 "Science",
    "PVS - Home Economics":           "Science",
    "Physical & Health Education":    "Science",
    "Quantitative Reasoning":         "Science",
    "Technical Drawing":              "Science",
    "Refrigeration & Airconditioning":"Science",
    "Fishery":                        "Science",
    "Catering Craft":                 "Science",
    "PVS - Agriculture":              "Science",
    "PVS - Entrepreneurship":         "Science",
    "Pre-Vocational Studies":         "Science",
    "Vocational Studies":             "Science",
    "Trade subjects":                 "Science",
    "Geography":                      "Science",

    # ── Humanities ───────────────────────────────────────────────────────
    "English Language":               "Humanities",
    "Literature in English":          "Humanities",
    "Yoruba Language":                "Humanities",
    "French Language":                "Humanities",
    "Christian Religious Knowledge":  "Humanities",
    "Christian Religious Studies":    "Humanities",
    "Civic Education":                "Humanities",
    "NV - Civic Education":           "Humanities",
    "NV - Social Studies":            "Humanities",
    "NV - Security Education":        "Humanities",
    "Social Studies":                 "Humanities",
    "National Values":                "Humanities",
    "Government":                     "Humanities",
    "History":                        "Humanities",
    "Cultural & Creative Arts":       "Humanities",
    "Fine Art":                       "Humanities",
    "Music":                          "Humanities",
    "Sports":                         "Humanities",
    "Moral Instruction":              "Humanities",
    "Phonics (Diction)":              "Humanities",
    "Verbal Reasoning":               "Humanities",

    # ── Business ─────────────────────────────────────────────────────────
    "Commerce":                       "Business",
    "Economics":                      "Business",
    "Financial Account":              "Business",
    "Business Studies":               "Business",
    "Insurance":                      "Business",
    "Digital Marketing":              "Business",
    "Software Development":           "Business",
    "Graphics Design":                "Business",
    "Animation":                      "Business",
    "Video Editing":                  "Business",

    # Not mapped — primary level or non-academic:
    # Free Time, Etiquette, Social Habit, Health Habit,
    # Rhyme, Writing, Hand Writing, Letter Work, Number Work
}


# =============================================================================
# MODELS
# =============================================================================

class StaffRoleCreate(BaseModel):
    teacher_id:    int
    role:          str
    assigned_on:   date
    subject_scope: Optional[str] = None
    class_scope:   Optional[str] = None
    level_scope:   Optional[str] = None
    assigned_by:   Optional[str] = None
    notes:         Optional[str] = None


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _validate_role(role: str):
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"role must be one of: {VALID_ROLES}"
        )


def _validate_scope_requirements(body: StaffRoleCreate):
    if body.role == "hod":
        if not body.subject_scope:
            raise HTTPException(
                status_code=422,
                detail="subject_scope is required for hod role. "
                       "Use: Science | Humanities | Business"
            )
        if body.subject_scope not in VALID_DEPARTMENTS:
            raise HTTPException(
                status_code=422,
                detail=f"subject_scope for hod must be one of: {VALID_DEPARTMENTS}"
            )
    if body.role == "class_teacher" and not body.class_scope:
        raise HTTPException(
            status_code=422,
            detail="class_scope is required for class_teacher role."
        )
    if body.role == "year_tutor" and not body.level_scope:
        raise HTTPException(
            status_code=422,
            detail="level_scope is required for year_tutor role."
        )


def _deactivate_role(role_id: int) -> dict:
    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT id, is_active FROM staff_roles WHERE id = %s", (role_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Role assignment not found.")
        if not row["is_active"]:
            raise HTTPException(status_code=409, detail="Role assignment already inactive.")
        cur.execute(
            "UPDATE staff_roles SET is_active = FALSE WHERE id = %s", (role_id,)
        )
    return {"message": "Role assignment deactivated."}


# =============================================================================
# EXTERNAL HELPERS — called by lesson_plans.py and enforcement_engine.py
# =============================================================================

def get_hod_for_subject(subject: str) -> Optional[dict]:
    """
    Return the active HOD responsible for a given subject.

    Resolves subject name → department via SUBJECT_DEPARTMENTS dict,
    then queries staff_roles for the HOD whose subject_scope matches
    that department.

    Returns None if:
      - Subject is not in SUBJECT_DEPARTMENTS (primary/non-academic)
      - No active HOD is designated for that department
    """
    department = SUBJECT_DEPARTMENTS.get(subject)
    if not department:
        return None

    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                sr.id           AS role_id,
                sr.teacher_id,
                t.first_name,
                t.last_name,
                t.telegram_id,
                t.email,
                sr.subject_scope AS department
            FROM   staff_roles sr
            JOIN   teachers t ON t.id = sr.teacher_id
            WHERE  sr.role          = 'hod'
            AND    sr.subject_scope = %s
            AND    sr.is_active     = TRUE
            AND    t.is_active      = TRUE
            ORDER  BY sr.assigned_on DESC
            LIMIT  1
        """, (department,))
        return cur.fetchone()


def get_role_holders(role: str) -> list:
    """
    Return all active holders of a given role.
    Called by the enforcement engine to route escalation messages.
    """
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                sr.id           AS role_id,
                sr.teacher_id,
                t.first_name,
                t.last_name,
                t.telegram_id,
                t.email,
                sr.subject_scope,
                sr.class_scope,
                sr.level_scope
            FROM   staff_roles sr
            JOIN   teachers t ON t.id = sr.teacher_id
            WHERE  sr.role      = %s
            AND    sr.is_active = TRUE
            AND    t.is_active  = TRUE
            ORDER  BY t.last_name, t.first_name
        """, (role,))
        return cur.fetchall()


# =============================================================================
# ROUTES
# =============================================================================

@router.post("", status_code=status.HTTP_201_CREATED)
def assign_role(body: StaffRoleCreate):
    """
    Assign a role to a teacher.

    For HOD roles, subject_scope must be the department name:
      Science | Humanities | Business

    Automatically deactivates the previous holder of the same
    role+scope combination before creating the new record.
    This ensures only one active HOD per department at any time.
    """
    _validate_role(body.role)
    _validate_scope_requirements(body)

    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT id FROM teachers WHERE id = %s AND is_active = TRUE",
            (body.teacher_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        # Deactivate any existing holder of the same role+scope
        cur.execute("""
            UPDATE staff_roles
            SET    is_active = FALSE
            WHERE  role      = %s
            AND    is_active = TRUE
            AND    COALESCE(subject_scope, '') = COALESCE(%s, '')
            AND    COALESCE(class_scope,   '') = COALESCE(%s, '')
            AND    COALESCE(level_scope,   '') = COALESCE(%s, '')
        """, (
            body.role,
            body.subject_scope,
            body.class_scope,
            body.level_scope,
        ))

        cur.execute("""
            INSERT INTO staff_roles (
                teacher_id, role, subject_scope, class_scope, level_scope,
                is_active, assigned_on, assigned_by, notes
            )
            VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, %s)
        """, (
            body.teacher_id,
            body.role,
            body.subject_scope,
            body.class_scope,
            body.level_scope,
            body.assigned_on,
            body.assigned_by,
            body.notes,
        ))
        new_id = cur.lastrowid

    return {"id": new_id, "message": "Staff role assigned."}


@router.get("")
def list_roles(active_only: bool = True, role: Optional[str] = None):
    """List role assignments. Filter by active status and/or role type."""
    query = """
        SELECT
            sr.*,
            CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
            t.telegram_id,
            t.email
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
def get_role_holders_route(role: str, active_only: bool = True):
    """All holders of a specific role."""
    _validate_role(role)
    query = """
        SELECT
            sr.*,
            CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
            t.telegram_id,
            t.email
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
def get_hod_by_subject(subject: str):
    """
    Get the HOD responsible for a specific subject.
    Resolves subject → department via SUBJECT_DEPARTMENTS mapping,
    then returns the HOD for that department.
    Returns 404 if subject is unmapped or no HOD is designated.
    """
    department = SUBJECT_DEPARTMENTS.get(subject)
    if not department:
        raise HTTPException(
            status_code=404,
            detail=f"Subject '{subject}' is not mapped to any department. "
                   "It may be a primary level or non-academic subject."
        )

    hod = get_hod_for_subject(subject)
    if not hod:
        raise HTTPException(
            status_code=404,
            detail=f"No active HOD designated for the {department} department. "
                   f"Assign one via POST /staff-roles with subject_scope='{department}'."
        )
    return {**hod, "subject_queried": subject}


@router.get("/principal")
def get_current_principal():
    """Get the current principal. Returns 404 if none designated."""
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                sr.*,
                CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                t.telegram_id,
                t.email
            FROM   staff_roles sr
            JOIN   teachers t ON t.id = sr.teacher_id
            WHERE  sr.role      = 'principal'
            AND    sr.is_active = TRUE
            ORDER  BY sr.assigned_on DESC
            LIMIT  1
        """)
        row = cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="No principal designated. Assign one via POST /staff-roles."
        )
    return row


@router.patch("/{role_id}/deactivate")
def deactivate_role_patch(role_id: int):
    """Deactivate a role assignment via PATCH."""
    return _deactivate_role(role_id)


@router.delete("/{role_id}")
def deactivate_role_delete(role_id: int):
    """Deactivate a role assignment via DELETE."""
    return _deactivate_role(role_id)
