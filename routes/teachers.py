"""
routes/teachers.py — Teacher register (read + sync from enterprise).

Teachers are the authoritative source of truth in the enterprise DB.
Sabi holds a lightweight mirror with Telegram IDs and Sabi-specific fields.

Endpoints:
    GET   /teachers                   list all active teachers
    GET   /teachers/{id}              single teacher with KPI summary
    GET   /teachers/{id}/subjects     teaching subjects/classes from teacher_assignments
    PATCH /teachers/{id}/telegram     set or update a teacher's Telegram ID
    POST  /teachers/sync              upsert from enterprise DB
    GET   /teachers/{id}/kpi/history  all past KPI scores for a teacher
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi, get_enterprise

router = APIRouter()


def _normalize_employment_type(raw: Optional[str]) -> str:
    """Map enterprise employment labels to the sabi_db ENUM values."""
    value = (raw or "").strip().lower()
    mapping = {
        "regular": "full_time",
        "full time": "full_time",
        "full_time": "full_time",
        "parttime": "part_time",
        "part time": "part_time",
        "part_time": "part_time",
        "contract": "contract",
    }
    return mapping.get(value, "full_time")


# =============================================================================
# MODELS
# =============================================================================

class TelegramUpdate(BaseModel):
    telegram_id: int


# =============================================================================
# HELPERS
# =============================================================================

def _require_teacher(cur, teacher_id: int) -> dict:
    cur.execute("SELECT * FROM teachers WHERE id = %s", (teacher_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Teacher not found.")
    return row


# =============================================================================
# ROUTES
# =============================================================================

@router.get("")
def list_teachers(active_only: bool = True):
    """
    List teachers. active_only=true (default) returns only active staff.
    Pass ?active_only=false to include departed teachers.
    """
    with get_sabi() as (_, cur):
        if active_only:
            cur.execute("""
                SELECT id, enterprise_id, telegram_id, first_name, last_name,
                       email, subject_primary, subject_secondary,
                       employment_type, date_joined, is_active
                FROM   teachers
                WHERE  is_active = TRUE
                ORDER  BY last_name, first_name
            """)
        else:
            cur.execute("""
                SELECT id, enterprise_id, telegram_id, first_name, last_name,
                       email, subject_primary, subject_secondary,
                       employment_type, date_joined, is_active
                FROM   teachers
                ORDER  BY last_name, first_name
            """)
        return cur.fetchall()


@router.get("/me")
def get_me(telegram_id: int):
    """
    Resolve a Telegram user ID to a Sabi teacher record.
    Returns 404 if the Telegram ID is not linked to any active teacher.
    """
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                id, enterprise_id, telegram_id,
                first_name, last_name, email,
                subject_primary, subject_secondary,
                employment_type, date_joined, is_active
            FROM   teachers
            WHERE  telegram_id = %s
            AND    is_active   = TRUE
        """, (telegram_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Telegram ID not recognised. You are not registered in Sabi. "
                   "Please contact the school administrator to link your account."
        )

    return row


@router.get("/{teacher_id}")
def get_teacher(teacher_id: int):
    """
    Return a teacher's profile plus their most recent KPI score if available.
    """
    with get_sabi() as (_, cur):
        teacher = _require_teacher(cur, teacher_id)

        # Attach most recent KPI score
        cur.execute("""
            SELECT ks.total_score, ks.is_eligible, ks.score_delta,
                   ks.computed_at, at.term_name
            FROM   kpi_scores ks
            JOIN   academic_terms at ON at.id = ks.term_id
            WHERE  ks.teacher_id = %s
            ORDER  BY ks.computed_at DESC
            LIMIT  1
        """, (teacher_id,))
        teacher["latest_kpi"] = cur.fetchone()

    return teacher


@router.get("/{teacher_id}/subjects")
def get_teacher_subjects(teacher_id: int, term_id: Optional[int] = None):
    """
    Return subjects (and classes) taught by a teacher from teacher_assignments.
    If term_id is not provided, uses the current term when available.
    """
    with get_sabi() as (_, cur):
        _require_teacher(cur, teacher_id)

        resolved_term_id = term_id
        if resolved_term_id is None:
            cur.execute("SELECT id FROM academic_terms WHERE is_current = TRUE LIMIT 1")
            row = cur.fetchone()
            if row:
                resolved_term_id = row["id"]

        if resolved_term_id is not None:
            cur.execute("SELECT id FROM academic_terms WHERE id = %s", (resolved_term_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Term not found.")
            cur.execute(
                """
                SELECT subject_name, class_name, enterprise_subject_id, enterprise_class_id
                FROM   teacher_assignments
                WHERE  teacher_id = %s
                AND    term_id = %s
                ORDER  BY subject_name, class_name
                """,
                (teacher_id, resolved_term_id),
            )
            rows = cur.fetchall()
        else:
            cur.execute(
                """
                SELECT subject_name, class_name, enterprise_subject_id, enterprise_class_id, term_id
                FROM   teacher_assignments
                WHERE  teacher_id = %s
                ORDER  BY term_id DESC, subject_name, class_name
                """,
                (teacher_id,),
            )
            rows = cur.fetchall()

    unique_subjects = sorted({r["subject_name"] for r in rows if r.get("subject_name")})
    return {
        "teacher_id": teacher_id,
        "term_id": resolved_term_id,
        "source": "teacher_assignments",
        "subjects": unique_subjects,
        "assignments": rows,
    }


@router.patch("/{teacher_id}/telegram")
def update_telegram_id(teacher_id: int, body: TelegramUpdate):
    """
    Set or update the Telegram user ID for a teacher.
    Called when a teacher first messages the Staff Bot and identifies themselves.
    """
    with get_sabi() as (_, cur):
        _require_teacher(cur, teacher_id)

        # Check for conflicts — Telegram ID must be unique
        cur.execute(
            "SELECT id FROM teachers WHERE telegram_id = %s AND id != %s",
            (body.telegram_id, teacher_id)
        )
        if cur.fetchone():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This Telegram ID is already linked to another teacher."
            )

        cur.execute(
            "UPDATE teachers SET telegram_id = %s WHERE id = %s",
            (body.telegram_id, teacher_id)
        )

    return {"message": "Telegram ID updated."}


@router.post("/sync")
def sync_from_enterprise():
    """
    Pull the staff list from the enterprise DB and upsert into sabi_db.teachers.

    - New staff records are inserted.
    - Existing staff (matched on enterprise_id) have name/email/subject updated.
    - Staff no longer in the enterprise active list are marked is_active = FALSE.

    Returns a summary: inserted, updated, deactivated counts.
    """
    # ── 1. Read from enterprise DB ───────────────────────────────────────────
    # Adjust the SELECT below to match your enterprise DB column names exactly.
    with get_enterprise() as (_, cur):
        cur.execute("""
            SELECT
                id            AS enterprise_id,
                first_name,
                last_name,
                faculty_email_address_1 AS email,
                faculty_phone_no_1      AS phone,
                employment_type,
                employment_date         AS date_joined
            FROM   tb_faculty_registrations
            WHERE  academic_active = 1
            AND    employment_status = 'Active'
        """)
        enterprise_staff = cur.fetchall()

    if not enterprise_staff:
        return {"message": "No active staff found in enterprise DB.", "inserted": 0, "updated": 0, "deactivated": 0}

    enterprise_ids = {s["enterprise_id"] for s in enterprise_staff}

    inserted = 0
    updated  = 0

    # ── 2. Upsert into Sabi ──────────────────────────────────────────────────
    with get_sabi() as (_, cur):
        for staff in enterprise_staff:
            cur.execute(
                "SELECT id FROM teachers WHERE enterprise_id = %s",
                (staff["enterprise_id"],)
            )
            existing = cur.fetchone()

            if existing:
                cur.execute("""
                    UPDATE teachers SET
                        first_name        = %s,
                        last_name         = %s,
                        email             = %s,
                        phone             = %s,
                        subject_primary   = %s,
                        subject_secondary = %s,
                        employment_type   = %s,
                        date_joined       = %s,
                        is_active         = TRUE
                    WHERE enterprise_id = %s
                """, (
                    staff["first_name"], staff["last_name"],
                    staff["email"], staff.get("phone"),
                    staff.get("subject_primary"), staff.get("subject_secondary"),
                    _normalize_employment_type(staff.get("employment_type")),
                    staff["date_joined"], staff["enterprise_id"]
                ))
                updated += 1
            else:
                cur.execute("""
                    INSERT INTO teachers
                        (enterprise_id, first_name, last_name, email, phone,
                         subject_primary, subject_secondary, employment_type, date_joined)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    staff["enterprise_id"], staff["first_name"], staff["last_name"],
                    staff["email"], staff.get("phone"),
                    staff.get("subject_primary"), staff.get("subject_secondary"),
                    _normalize_employment_type(staff.get("employment_type")),
                    staff["date_joined"]
                ))
                inserted += 1

        # ── 3. Deactivate staff no longer in enterprise ──────────────────────
        if enterprise_ids:
            placeholders = ",".join(["%s"] * len(enterprise_ids))
            cur.execute(f"""
                UPDATE teachers
                SET    is_active = FALSE
                WHERE  enterprise_id NOT IN ({placeholders})
                AND    is_active = TRUE
            """, tuple(enterprise_ids))
            deactivated = cur.rowcount
        else:
            deactivated = 0

    return {
        "message":     "Sync complete.",
        "inserted":    inserted,
        "updated":     updated,
        "deactivated": deactivated,
    }


@router.get("/{teacher_id}/kpi/history")
def get_kpi_history(teacher_id: int):
    """
    Return all computed KPI scores for a teacher across all terms,
    including the per-category breakdown and eligibility status.
    """
    with get_sabi() as (_, cur):
        _require_teacher(cur, teacher_id)
        cur.execute("""
            SELECT
                ks.*,
                at.term_name,
                at.academic_year,
                at.term_number
            FROM   kpi_scores ks
            JOIN   academic_terms at ON at.id = ks.term_id
            WHERE  ks.teacher_id = %s
            ORDER  BY at.academic_year DESC, at.term_number DESC
        """, (teacher_id,))
        return cur.fetchall()
