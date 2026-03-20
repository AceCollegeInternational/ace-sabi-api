"""
routes/students.py — Student lookup and profile endpoints.

All data is read from the enterprise DB. Nothing is written here.
This route exists primarily to allow OpenClaw to resolve a student
name to a student_id before calling any student-specific endpoint.

IMPORTANT — Route order matters:
    /search and /class/{class_id} must be defined BEFORE /{student_id}
    otherwise FastAPI matches 'search' and 'class' as student_id values.

Endpoints:
    GET /students/search                  search by name
    GET /students/class/{class_id}        all students in a class
    GET /students/{student_id}/contact    contact details only
    GET /students/{student_id}            full profile for one student
"""

from typing import Optional
from fastapi import APIRouter, HTTPException

from database.connections import get_sabi, get_enterprise
from database.enterprise_queries import (
    fetch_student,
    fetch_student_current_class,
    fetch_class_students,
)

router = APIRouter()


# =============================================================================
# HELPERS
# =============================================================================

def _get_current_session() -> int:
    """
    Return academic_session (start year) for the current term.
    Reads from sabi_db.academic_terms where is_current = TRUE.
    academic_session = start year only e.g. 2025 from '2025/2026'.
    """
    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT academic_year FROM academic_terms WHERE is_current = TRUE LIMIT 1"
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No current term set.")
    return int(row["academic_year"].split("/")[0])


# =============================================================================
# ROUTES — fixed order: specific paths before /{student_id}
# =============================================================================

@router.get("/search")
def search_students(
    name:             str,
    academic_session: Optional[int] = None,
    active_only:      bool          = True,
):
    """
    Search for students by name. Supports partial matches.
    Each word in the search is tested against the full concatenated name
    (last + first + other) using AND — all words must be present.
    Order of words does not matter.

    This is the primary resolution endpoint — call this first to get a
    student_id before calling any other student-specific endpoint.

    Examples:
        /students/search?name=regina
        /students/search?name=oyesile+regina
        /students/search?name=oye+reg   (partial words also work)
    """
    if not name or len(name.strip()) < 2:
        raise HTTPException(
            status_code=422,
            detail="name must be at least 2 characters."
        )

    if academic_session is None:
        academic_session = _get_current_session()

    # Split search input into individual words.
    # Each word must appear somewhere in the concatenated full name.
    words = [w.strip() for w in name.strip().split() if w.strip()]

    # Build one AND condition per word.
    name_conditions = " AND ".join([
        "CONCAT(sr.last_name, ' ', sr.first_name, ' ', COALESCE(sr.other_name, '')) LIKE %s"
        for _ in words
    ])
    name_params = [f"%{w}%" for w in words]

    base_query = f"""
        SELECT
            sr.id                                           AS student_id,
            sr.last_name,
            sr.first_name,
            sr.other_name,
            CONCAT(
                sr.last_name, ' ', sr.first_name,
                CASE WHEN sr.other_name != ''
                     THEN CONCAT(' ', sr.other_name)
                     ELSE '' END
            )                                               AS full_name,
            sr.admission_status,
            sr.student_gender,
            al.level_name,
            cr.classroom_name                               AS class_name,
            COALESCE(
                NULLIF(sr.guardian_phone_no_1, ''),
                NULLIF(sr.mother_phone_no_1, ''),
                NULLIF(sr.father_phone_no_1, ''),
                NULLIF(sr.sponsor_phone_no_1, '')
            )                                               AS primary_contact
        FROM   tb_student_registrations sr
        LEFT JOIN (
            SELECT acs.student_id, MIN(acs.class_id) AS class_id
            FROM   tb_academic_class_students acs
            JOIN   tb_academic_classes ac
                ON ac.id = acs.class_id
                AND ac.academic_session = %s
            GROUP BY acs.student_id
        ) latest_class ON latest_class.student_id = sr.id
        LEFT JOIN tb_academic_classes ac
            ON  ac.id = latest_class.class_id
        LEFT JOIN tb_academic_classrooms cr ON cr.id = ac.classroom_id
        LEFT JOIN tb_academic_levels al     ON al.id = ac.level_id
        WHERE ({name_conditions})
    """
    # academic_session is first because it appears in the subquery JOIN
    params = [academic_session] + name_params

    if active_only:
        base_query += " AND sr.admission_status = 'Active'"

    base_query += " GROUP BY sr.id ORDER BY sr.last_name, sr.first_name LIMIT 20"

    with get_enterprise() as (_, ent_cur):
        ent_cur.execute(base_query, params)
        results = ent_cur.fetchall()

    if not results:
        return {
            "query":   name,
            "count":   0,
            "results": [],
            "message": f"No students found matching '{name}'."
        }

    return {
        "query":   name,
        "count":   len(results),
        "results": results,
    }


@router.get("/class/{class_id}")
def get_class_students(
    class_id:         str,
    academic_session: Optional[int] = None,
):
    """
    All active students in a class for the current academic session.
    Returns name, gender, and primary contact for each student.
    """
    if academic_session is None:
        academic_session = _get_current_session()

    with get_enterprise() as (_, ent_cur):
        students = fetch_class_students(ent_cur, class_id, academic_session)

    if not students:
        return {
            "class_id": class_id,
            "count":    0,
            "students": [],
            "message":  "No active students found in this class."
        }

    return {
        "class_id": class_id,
        "count":    len(students),
        "students": students,
    }


@router.get("/{student_id}/contact")
def get_student_contact(student_id: str):
    """
    Contact details only for a student — all available numbers in
    priority order: guardian -> mother -> father -> sponsor.

    Used when a teacher needs to call a parent quickly without
    retrieving the full student profile.
    """
    with get_enterprise() as (_, ent_cur):
        ent_cur.execute("""
            SELECT
                CONCAT(sr.last_name, ' ', sr.first_name)       AS student_name,
                sr.guardian_names,
                sr.guardian_relationship,
                sr.guardian_phone_no_1,
                sr.guardian_phone_no_2,
                sr.mother_phone_no_1,
                sr.mother_phone_no_2,
                sr.father_phone_no_1,
                sr.father_phone_no_2,
                sr.sponsor_names,
                sr.sponsor_phone_no_1,
                sr.sponsor_phone_no_2,
                COALESCE(
                    NULLIF(sr.guardian_phone_no_1, ''),
                    NULLIF(sr.mother_phone_no_1, ''),
                    NULLIF(sr.father_phone_no_1, ''),
                    NULLIF(sr.sponsor_phone_no_1, '')
                )                                               AS primary_contact
            FROM   tb_student_registrations sr
            WHERE  sr.id = %s
        """, (student_id,))
        row = ent_cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Student {student_id} not found."
        )
    return row


@router.get("/{student_id}")
def get_student(
    student_id:       str,
    academic_session: Optional[int] = None,
):
    """
    Full profile for one student including contact details and current class.
    Use /students/search first to resolve a name to a student_id.
    """
    if academic_session is None:
        academic_session = _get_current_session()

    with get_enterprise() as (_, ent_cur):
        student = fetch_student(ent_cur, student_id)
        if not student:
            raise HTTPException(
                status_code=404,
                detail=f"Student {student_id} not found."
            )
        current_class = fetch_student_current_class(
            ent_cur, student_id, academic_session
        )

    return {
        **student,
        "current_class": current_class,
    }
