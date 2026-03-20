"""
routes/student_attendance.py — Student attendance queries from enterprise DB.

All data is read from tb_academic_class_morn_attendance in the enterprise DB.
Nothing is written here — student attendance is managed entirely by the
enterprise school software.

The attendance table stores one row per student per class per week.
Each row has boolean columns for Monday through Friday (1=present, 0=absent).
To get term totals we aggregate across all weeks for that term.

Endpoints:
    GET /student-attendance/student/{student_id}    full term attendance
                                                    for one student
    GET /student-attendance/class/{class_id}        attendance summary
                                                    for an entire class
    GET /student-attendance/student/{student_id}/weekly
                                                    week-by-week breakdown
                                                    for one student
"""

from typing import Optional
from fastapi import APIRouter, HTTPException

from database.connections import get_sabi, get_enterprise

router = APIRouter()


# =============================================================================
# HELPERS
# =============================================================================

def _get_session_and_term(term_id: Optional[int]) -> tuple:
    """
    Resolve academic_session and term_of_session from a sabi_db term_id.
    Defaults to current term if term_id is not provided.
    Returns (term_id, academic_session, term_of_session, term_name).
    """
    with get_sabi() as (_, cur):
        if term_id:
            cur.execute("""
                SELECT id, academic_year, term_number, term_name
                FROM   academic_terms WHERE id = %s
            """, (term_id,))
        else:
            cur.execute("""
                SELECT id, academic_year, term_number, term_name
                FROM   academic_terms WHERE is_current = TRUE LIMIT 1
            """)
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Term not found.")

    return (
        row["id"],
        int(row["academic_year"].split("/")[0]),
        int(row["term_number"]),
        row["term_name"],
    )


# =============================================================================
# ROUTES
# =============================================================================

@router.get("/student/{student_id}")
def get_student_attendance(
    student_id: str,
    term_id:    Optional[int] = None,
):
    """
    Full attendance summary for one student in a term.

    Returns:
      - Total school days in the attendance records
      - Days present, days absent
      - Attendance percentage
      - Number of weeks recorded
      - Student name and class (from enterprise DB)

    Uses the morning register (tb_academic_class_morn_attendance) as the
    primary attendance source.
    """
    tid, academic_session, term_of_session, term_name = _get_session_and_term(term_id)

    with get_enterprise() as (_, ent_cur):
        # Verify student exists
        ent_cur.execute("""
            SELECT
                CONCAT(last_name, ' ', first_name,
                    CASE WHEN other_name != ''
                         THEN CONCAT(' ', other_name)
                         ELSE '' END
                ) AS full_name,
                admission_status
            FROM tb_student_registrations
            WHERE id = %s
        """, (student_id,))
        student = ent_cur.fetchone()
        if not student:
            raise HTTPException(
                status_code=404,
                detail=f"Student {student_id} not found."
            )

        # Get current class
        ent_cur.execute("""
            SELECT
                cr.classroom_name AS class_name,
                al.level_name
            FROM   tb_academic_class_students acs
            JOIN   tb_academic_classes ac
                ON ac.id = acs.class_id
                AND ac.academic_session = %s
            JOIN   tb_academic_classrooms cr ON cr.id = ac.classroom_id
            JOIN   tb_academic_levels al     ON al.id = ac.level_id
            WHERE  acs.student_id = %s
            LIMIT  1
        """, (academic_session, student_id))
        class_info = ent_cur.fetchone()

        # Attendance summary
        ent_cur.execute("""
            SELECT
                COUNT(*)                                        AS weeks_recorded,
                COUNT(*) * 5                                    AS total_school_days,
                SUM(
                    attended_monday + attended_tuesday +
                    attended_wednesday + attended_thursday +
                    attended_friday
                )                                               AS days_present,
                SUM(5 - (
                    attended_monday + attended_tuesday +
                    attended_wednesday + attended_thursday +
                    attended_friday
                ))                                              AS days_absent,
                ROUND(
                    SUM(
                        attended_monday + attended_tuesday +
                        attended_wednesday + attended_thursday +
                        attended_friday
                    ) / NULLIF(COUNT(*) * 5, 0) * 100, 2
                )                                               AS attendance_pct,
                MIN(attendance_start_date)                      AS term_start,
                MAX(attendance_end_date)                        AS term_end
            FROM   tb_academic_class_morn_attendance
            WHERE  student_id       = %s
            AND    academic_session = %s
            AND    term_of_session  = %s
        """, (student_id, academic_session, term_of_session))
        summary = ent_cur.fetchone()

    # Determine risk flag
    attendance_pct = summary["attendance_pct"] if summary else None
    is_at_risk = (
        attendance_pct is not None and attendance_pct < 75.0
    )

    return {
        "student_id":       student_id,
        "full_name":        student["full_name"],
        "admission_status": student["admission_status"],
        "class_name":       class_info["class_name"] if class_info else None,
        "level_name":       class_info["level_name"] if class_info else None,
        "term_id":          tid,
        "term_name":        term_name,
        "academic_session": academic_session,
        "term_of_session":  term_of_session,
        "attendance": {
            "weeks_recorded":   summary["weeks_recorded"],
            "total_school_days": summary["total_school_days"],
            "days_present":     summary["days_present"],
            "days_absent":      summary["days_absent"],
            "attendance_pct":   attendance_pct,
            "term_start":       summary["term_start"],
            "term_end":         summary["term_end"],
        },
        "is_at_risk":       is_at_risk,
        "risk_threshold":   75.0,
    }


@router.get("/student/{student_id}/weekly")
def get_student_weekly_attendance(
    student_id: str,
    term_id:    Optional[int] = None,
):
    """
    Week-by-week attendance breakdown for one student.

    Returns one row per week showing which days the student was present
    or absent, the week date range, and a weekly total.

    Useful when a teacher needs to see the pattern — e.g. whether a
    student is consistently absent on Mondays or missing full weeks.
    """
    tid, academic_session, term_of_session, term_name = _get_session_and_term(term_id)

    with get_enterprise() as (_, ent_cur):
        # Verify student exists
        ent_cur.execute("""
            SELECT CONCAT(last_name, ' ', first_name) AS full_name
            FROM   tb_student_registrations WHERE id = %s
        """, (student_id,))
        student = ent_cur.fetchone()
        if not student:
            raise HTTPException(
                status_code=404,
                detail=f"Student {student_id} not found."
            )

        ent_cur.execute("""
            SELECT
                week_of_term,
                attendance_start_date               AS week_start,
                attendance_end_date                 AS week_end,
                attended_monday                     AS monday,
                attended_tuesday                    AS tuesday,
                attended_wednesday                  AS wednesday,
                attended_thursday                   AS thursday,
                attended_friday                     AS friday,
                (
                    attended_monday + attended_tuesday +
                    attended_wednesday + attended_thursday +
                    attended_friday
                )                                   AS days_present,
                (5 - (
                    attended_monday + attended_tuesday +
                    attended_wednesday + attended_thursday +
                    attended_friday
                ))                                  AS days_absent
            FROM   tb_academic_class_morn_attendance
            WHERE  student_id       = %s
            AND    academic_session = %s
            AND    term_of_session  = %s
            ORDER  BY week_of_term ASC
        """, (student_id, academic_session, term_of_session))
        weeks = ent_cur.fetchall()

    return {
        "student_id":      student_id,
        "full_name":       student["full_name"],
        "term_id":         tid,
        "term_name":       term_name,
        "weeks_recorded":  len(weeks),
        "weekly_breakdown": weeks,
    }


@router.get("/class/{class_id}")
def get_class_attendance(
    class_id: str,
    term_id:  Optional[int] = None,
):
    """
    Attendance summary for every student in a class for a term.

    Returns per-student totals sorted by attendance percentage ascending
    so the most at-risk students appear first.

    Also returns class-level statistics:
      - Class average attendance percentage
      - Number of students below 75% threshold
      - Number of students with perfect attendance
    """
    tid, academic_session, term_of_session, term_name = _get_session_and_term(term_id)

    with get_enterprise() as (_, ent_cur):
        # Verify class exists
        ent_cur.execute("""
            SELECT
                cr.classroom_name AS class_name,
                al.level_name
            FROM   tb_academic_classes ac
            JOIN   tb_academic_classrooms cr ON cr.id = ac.classroom_id
            JOIN   tb_academic_levels al     ON al.id = ac.level_id
            WHERE  ac.id = %s AND ac.academic_session = %s
        """, (class_id, academic_session))
        class_info = ent_cur.fetchone()
        if not class_info:
            raise HTTPException(
                status_code=404,
                detail=f"Class {class_id} not found for session {academic_session}."
            )

        # Per-student attendance summary
        ent_cur.execute("""
            SELECT
                a.student_id,
                CONCAT(
                    sr.last_name, ' ', sr.first_name,
                    CASE WHEN sr.other_name != ''
                         THEN CONCAT(' ', sr.other_name)
                         ELSE '' END
                )                                               AS full_name,
                sr.student_gender,
                COUNT(*) * 5                                    AS total_school_days,
                SUM(
                    a.attended_monday + a.attended_tuesday +
                    a.attended_wednesday + a.attended_thursday +
                    a.attended_friday
                )                                               AS days_present,
                SUM(5 - (
                    a.attended_monday + a.attended_tuesday +
                    a.attended_wednesday + a.attended_thursday +
                    a.attended_friday
                ))                                              AS days_absent,
                ROUND(
                    SUM(
                        a.attended_monday + a.attended_tuesday +
                        a.attended_wednesday + a.attended_thursday +
                        a.attended_friday
                    ) / NULLIF(COUNT(*) * 5, 0) * 100, 2
                )                                               AS attendance_pct,
                COALESCE(
                    NULLIF(sr.guardian_phone_no_1, ''),
                    NULLIF(sr.mother_phone_no_1, ''),
                    NULLIF(sr.father_phone_no_1, ''),
                    NULLIF(sr.sponsor_phone_no_1, '')
                )                                               AS primary_contact
            FROM   tb_academic_class_morn_attendance a
            JOIN   tb_student_registrations sr ON sr.id = a.student_id
            WHERE  a.class_id        = %s
            AND    a.academic_session = %s
            AND    a.term_of_session  = %s
            GROUP  BY
                a.student_id, sr.last_name, sr.first_name, sr.other_name,
                sr.student_gender, sr.guardian_phone_no_1,
                sr.mother_phone_no_1, sr.father_phone_no_1,
                sr.sponsor_phone_no_1
            ORDER  BY attendance_pct ASC
        """, (class_id, academic_session, term_of_session))
        students = ent_cur.fetchall()

    # Add risk flag per student
    for s in students:
        s["is_at_risk"] = (
            s["attendance_pct"] is not None and s["attendance_pct"] < 75.0
        )

    # Class-level statistics
    total_students    = len(students)
    at_risk_count     = sum(1 for s in students if s["is_at_risk"])
    perfect_count     = sum(
        1 for s in students
        if s["attendance_pct"] is not None and s["attendance_pct"] == 100.0
    )
    class_avg = (
        round(
            sum(s["attendance_pct"] for s in students if s["attendance_pct"] is not None)
            / max(total_students, 1), 2
        )
        if students else None
    )

    return {
        "class_id":        class_id,
        "class_name":      class_info["class_name"],
        "level_name":      class_info["level_name"],
        "term_id":         tid,
        "term_name":       term_name,
        "academic_session": academic_session,
        "term_of_session": term_of_session,
        "class_summary": {
            "total_students":    total_students,
            "class_average_pct": class_avg,
            "at_risk_count":     at_risk_count,
            "perfect_attendance": perfect_count,
            "risk_threshold":    75.0,
        },
        "students": students,
    }
