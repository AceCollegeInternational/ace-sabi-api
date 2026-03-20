"""
database/enterprise_queries.py — Reusable query functions for the enterprise DB.

All functions in this module are read-only against the enterprise DB.
They are used by the at-risk system, parent notification, pastoral logs
display, and the KPI engine.

Convention:
  - academic_session = start year of academic year (e.g. 2025 for 2025/2026)
  - term_of_session  = term number 1, 2, or 3
  - student_id       = tb_student_registrations.id
  - enterprise_id    = tb_faculty_registrations.id
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# STUDENT IDENTITY
# =============================================================================

def fetch_student(ent_cur, student_id: str) -> Optional[dict]:
    """
    Fetch a student's core identity record from tb_student_registrations.
    Returns name, level, course, admission status, and contact numbers.
    Returns None if student not found.
    """
    try:
        ent_cur.execute("""
            SELECT
                sr.id,
                sr.last_name,
                sr.first_name,
                sr.other_name,
                CONCAT(sr.last_name, ' ', sr.first_name,
                       CASE WHEN sr.other_name != '' THEN CONCAT(' ', sr.other_name)
                            ELSE '' END)              AS full_name,
                sr.admission_date,
                sr.date_of_birth,
                sr.student_gender,
                sr.admission_status,
                al.level_name,
                -- Guardian contact (first priority)
                sr.guardian_names,
                sr.guardian_relationship,
                sr.guardian_phone_no_1,
                sr.guardian_phone_no_2,
                -- Mother contact (second priority)
                sr.mother_phone_no_1,
                sr.mother_phone_no_2,
                -- Father contact (third priority)
                sr.father_phone_no_1,
                sr.father_phone_no_2,
                -- Sponsor contact (last resort)
                sr.sponsor_names,
                sr.sponsor_phone_no_1,
                sr.sponsor_phone_no_2,
                -- Best available contact number:
                -- guardian → mother → father → sponsor
                COALESCE(
                    NULLIF(sr.guardian_phone_no_1, ''),
                    NULLIF(sr.mother_phone_no_1, ''),
                    NULLIF(sr.father_phone_no_1, ''),
                    NULLIF(sr.sponsor_phone_no_1, '')
                )                                     AS primary_contact
            FROM   tb_student_registrations sr
            LEFT JOIN tb_academic_levels al ON al.id = sr.level_id
            WHERE  sr.id = %s
        """, (student_id,))
        return ent_cur.fetchone()
    except Exception as e:
        logger.warning("Could not fetch student %s: %s", student_id, e)
        return None


def fetch_student_current_class(
    ent_cur, student_id: str, academic_session: int
) -> Optional[dict]:
    """
    Fetch the student's current class for a given academic session.
    Joins tb_academic_class_students → tb_academic_classes → tb_academic_levels.
    """
    try:
        ent_cur.execute("""
            SELECT
                ac.id                                          AS class_id,
                cr.classroom_name   AS class_name,
                cr.classroom_name,
                al.level_name,
                ac.primary_teacher_id,
                ac.alternate_teacher_id
            FROM   tb_academic_class_students acs
            JOIN   tb_academic_classes ac
                ON ac.id = acs.class_id
                AND ac.academic_session = %s
            JOIN   tb_academic_classrooms cr ON cr.id = ac.classroom_id
            JOIN   tb_academic_levels al     ON al.id = ac.level_id
            WHERE  acs.student_id = %s
            LIMIT  1
        """, (academic_session, student_id))
        return ent_cur.fetchone()
    except Exception as e:
        logger.warning("Could not fetch class for student %s: %s", student_id, e)
        return None


# =============================================================================
# STUDENT ATTENDANCE
# =============================================================================

def fetch_student_attendance_summary(
    ent_cur,
    student_id:       str,
    academic_session: int,
    term_of_session:  int
) -> dict:
    """
    Compute a student's attendance summary for a term.

    The attendance tables store one row per class per week with boolean
    columns for each weekday (1=present, 0=absent). This query aggregates
    across all weeks to produce total days present, total school days,
    and attendance percentage.

    Combines both morning and afternoon registers — a student must be
    present in both to count as a full day. If only morning or only
    afternoon data exists, morning register is used as the primary source.

    Returns:
        total_school_days  : total weekdays in the term records
        days_present       : days the student was marked present
        days_absent        : total_school_days - days_present
        attendance_pct     : days_present / total_school_days * 100
        weeks_recorded     : number of weeks with attendance data
    """
    try:
        ent_cur.execute("""
            SELECT
                COUNT(*) * 5                                    AS total_school_days,
                SUM(
                    attended_monday + attended_tuesday +
                    attended_wednesday + attended_thursday +
                    attended_friday
                )                                               AS days_present,
                COUNT(*)                                        AS weeks_recorded
            FROM   tb_academic_class_morn_attendance
            WHERE  student_id       = %s
            AND    academic_session = %s
            AND    term_of_session  = %s
        """, (student_id, academic_session, term_of_session))
        row = ent_cur.fetchone()

        if not row or not row["total_school_days"]:
            return {
                "total_school_days": 0,
                "days_present":      0,
                "days_absent":       0,
                "attendance_pct":    None,
                "weeks_recorded":    0,
            }

        total   = int(row["total_school_days"])
        present = int(row["days_present"] or 0)
        absent  = total - present
        pct     = round(present / total * 100, 2) if total > 0 else None

        return {
            "total_school_days": total,
            "days_present":      present,
            "days_absent":       absent,
            "attendance_pct":    pct,
            "weeks_recorded":    int(row["weeks_recorded"]),
        }
    except Exception as e:
        logger.warning(
            "Could not fetch attendance for student %s: %s", student_id, e
        )
        return {
            "total_school_days": 0,
            "days_present":      0,
            "days_absent":       0,
            "attendance_pct":    None,
            "weeks_recorded":    0,
        }


def fetch_students_below_attendance_threshold(
    ent_cur,
    class_id:         str,
    academic_session: int,
    term_of_session:  int,
    threshold_pct:    float = 75.0
) -> list:
    """
    Return all students in a class whose attendance is below threshold_pct.
    Default threshold is 75% — the standard minimum attendance requirement.
    Used by the at-risk early warning system.
    """
    try:
        ent_cur.execute("""
            SELECT
                a.student_id,
                CONCAT(sr.last_name, ' ', sr.first_name)       AS student_name,
                cr.classroom_name   AS class_name,
                COUNT(*) * 5                                    AS total_school_days,
                SUM(
                    a.attended_monday + a.attended_tuesday +
                    a.attended_wednesday + a.attended_thursday +
                    a.attended_friday
                )                                               AS days_present,
                ROUND(
                    SUM(
                        a.attended_monday + a.attended_tuesday +
                        a.attended_wednesday + a.attended_thursday +
                        a.attended_friday
                    ) / (COUNT(*) * 5) * 100, 2
                )                                               AS attendance_pct,
                COALESCE(
                    NULLIF(sr.guardian_phone_no_1, ''),
                    NULLIF(sr.mother_phone_no_1, ''),
                    NULLIF(sr.father_phone_no_1, ''),
                    NULLIF(sr.sponsor_phone_no_1, '')
                )                                               AS primary_contact
            FROM   tb_academic_class_morn_attendance a
            JOIN   tb_student_registrations sr ON sr.id = a.student_id
            JOIN   tb_academic_classes ac
                ON  ac.id = a.class_id
                AND ac.academic_session = %s
            JOIN   tb_academic_classrooms cr ON cr.id = ac.classroom_id
            WHERE  a.class_id         = %s
            AND    a.academic_session = %s
            AND    a.term_of_session  = %s
            GROUP  BY a.student_id, sr.last_name, sr.first_name,
                      cr.classroom_name,
                      sr.guardian_phone_no_1, sr.mother_phone_no_1,
                      sr.father_phone_no_1, sr.sponsor_phone_no_1
            HAVING attendance_pct < %s
            ORDER  BY attendance_pct ASC
        """, (academic_session, class_id, academic_session, term_of_session, threshold_pct))
        return ent_cur.fetchall()
    except Exception as e:
        logger.warning(
            "Could not fetch low-attendance students for class %s: %s",
            class_id, e
        )
        return []


# =============================================================================
# STUDENT ACADEMIC PERFORMANCE
# =============================================================================

def fetch_student_term_scores(
    ent_cur,
    student_id:       str,
    academic_session: int,
    term_of_session:  int
) -> list:
    """
    Fetch all assessment scores for a student in a term.
    Excludes parent subject aggregates (parent_id IS NULL = leaf subjects only).
    Excludes Moral Test and Homework — uses only objective assessments.
    Returns one row per subject per assessment type.
    """
    try:
        ent_cur.execute("""
            SELECT
                ssr.subject_id,
                s.subject_name,
                aa.assessment_name,
                ssr.mark_obtained,
                ssr.mark_obtainable,
                ROUND(
                    ssr.mark_obtained / ssr.mark_obtainable * 100, 2
                )                                               AS score_pct
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
            JOIN   tb_academic_subjects s     ON s.id  = ssr.subject_id
            WHERE  ssr.student_id       = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    ssr.parent_id IS NULL
            AND    aa.assessment_name IN (
                       'Test 1', 'Test 2', 'Test 3', 'Examination'
                   )
            ORDER  BY s.subject_name, aa.assessment_name
        """, (student_id, academic_session, term_of_session))
        return ent_cur.fetchall()
    except Exception as e:
        logger.warning(
            "Could not fetch scores for student %s: %s", student_id, e
        )
        return []


def fetch_student_score_trend(
    ent_cur,
    student_id:       str,
    academic_session: int,
    term_of_session:  int
) -> dict:
    """
    Compute a student's overall academic performance for a term.
    Returns average score percentage and per-subject averages.
    Used by the at-risk system to detect underperformance.
    """
    try:
        # Overall average
        ent_cur.execute("""
            SELECT
                ROUND(
                    AVG(ssr.mark_obtained / ssr.mark_obtainable * 100), 2
                )                                               AS overall_avg,
                COUNT(DISTINCT ssr.subject_id)                  AS subjects_assessed
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
            WHERE  ssr.student_id       = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    ssr.parent_id IS NULL
            AND    aa.assessment_name IN (
                       'Test 1', 'Test 2', 'Test 3', 'Examination'
                   )
        """, (student_id, academic_session, term_of_session))
        overall = ent_cur.fetchone()

        # Per-subject averages
        ent_cur.execute("""
            SELECT
                s.subject_name,
                ROUND(
                    AVG(ssr.mark_obtained / ssr.mark_obtainable * 100), 2
                )                                               AS subject_avg,
                COUNT(*)                                        AS assessments_count
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
            JOIN   tb_academic_subjects s     ON s.id  = ssr.subject_id
            WHERE  ssr.student_id       = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    ssr.parent_id IS NULL
            AND    aa.assessment_name IN (
                       'Test 1', 'Test 2', 'Test 3', 'Examination'
                   )
            GROUP  BY ssr.subject_id, s.subject_name
            ORDER  BY subject_avg ASC
        """, (student_id, academic_session, term_of_session))
        by_subject = ent_cur.fetchall()

        return {
            "overall_avg":        overall["overall_avg"] if overall else None,
            "subjects_assessed":  overall["subjects_assessed"] if overall else 0,
            "by_subject":         by_subject,
        }
    except Exception as e:
        logger.warning(
            "Could not fetch score trend for student %s: %s", student_id, e
        )
        return {"overall_avg": None, "subjects_assessed": 0, "by_subject": []}


def fetch_students_below_score_threshold(
    ent_cur,
    class_id:         str,
    academic_session: int,
    term_of_session:  int,
    threshold_pct:    float = 50.0
) -> list:
    """
    Return all students in a class whose overall average score is below
    threshold_pct. Default threshold is 50%.
    Used by the at-risk early warning system.
    """
    try:
        ent_cur.execute("""
            SELECT
                ssr.student_id,
                CONCAT(sr.last_name, ' ', sr.first_name)       AS student_name,
                cr.classroom_name   AS class_name,
                ROUND(
                    AVG(ssr.mark_obtained / ssr.mark_obtainable * 100), 2
                )                                               AS overall_avg,
                COALESCE(
                    NULLIF(sr.guardian_phone_no_1, ''),
                    NULLIF(sr.mother_phone_no_1, ''),
                    NULLIF(sr.father_phone_no_1, ''),
                    NULLIF(sr.sponsor_phone_no_1, '')
                )                                               AS primary_contact
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
            JOIN   tb_student_registrations sr ON sr.id = ssr.student_id
            JOIN   tb_academic_classes ac
                ON  ac.id = ssr.class_id
                AND ac.academic_session = %s
            JOIN   tb_academic_classrooms cr ON cr.id = ac.classroom_id
            WHERE  ssr.class_id         = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    ssr.parent_id IS NULL
            AND    aa.assessment_name IN (
                       'Test 1', 'Test 2', 'Test 3', 'Examination'
                   )
            GROUP  BY ssr.student_id, sr.last_name, sr.first_name,
                      cr.classroom_name,
                      sr.guardian_phone_no_1, sr.mother_phone_no_1,
                      sr.father_phone_no_1, sr.sponsor_phone_no_1
            HAVING overall_avg < %s
            ORDER  BY overall_avg ASC
        """, (academic_session, class_id, academic_session, term_of_session, threshold_pct))
        return ent_cur.fetchall()
    except Exception as e:
        logger.warning(
            "Could not fetch low-scoring students for class %s: %s",
            class_id, e
        )
        return []


# =============================================================================
# CLASS ROSTER
# =============================================================================

def fetch_class_students(
    ent_cur,
    class_id:         str,
    academic_session: int
) -> list:
    """
    Return all active students in a class for a given academic session.
    """
    try:
        ent_cur.execute("""
            SELECT
                acs.student_id,
                CONCAT(sr.last_name, ' ', sr.first_name)       AS student_name,
                sr.student_gender,
                sr.admission_status,
                cr.classroom_name   AS class_name,
                COALESCE(
                    NULLIF(sr.guardian_phone_no_1, ''),
                    NULLIF(sr.mother_phone_no_1, ''),
                    NULLIF(sr.father_phone_no_1, ''),
                    NULLIF(sr.sponsor_phone_no_1, '')
                )                                               AS primary_contact
            FROM   tb_academic_class_students acs
            JOIN   tb_student_registrations sr ON sr.id = acs.student_id
            JOIN   tb_academic_classes ac
                ON ac.id = acs.class_id
                AND ac.academic_session = %s
            JOIN   tb_academic_classrooms cr ON cr.id = ac.classroom_id
            WHERE  acs.class_id        = %s
            AND    sr.admission_status = 'Active'
            ORDER  BY sr.last_name, sr.first_name        
        """, (academic_session, class_id))
        return ent_cur.fetchall()
    except Exception as e:
        logger.warning(
            "Could not fetch students for class %s: %s", class_id, e
        )
        return []


# =============================================================================
# TEACHER CLASSES
# =============================================================================

def fetch_teacher_classes(
    ent_cur,
    enterprise_id:    str,
    academic_session: int
) -> list:
    """
    Return all classes a teacher is assigned to as primary or alternate teacher.
    Used to scope at-risk queries to only the teacher's classes.
    """
    try:
        ent_cur.execute("""
            SELECT DISTINCT
                ac.id                                           AS class_id,
                cr.classroom_name   AS class_name,
                cr.classroom_name,
                al.level_name,
                ac.primary_teacher_id,
                ac.alternate_teacher_id,
                CASE
                    WHEN ac.level_id IN (
                        SELECT level_id
                        FROM   tb_academic_classes
                        WHERE  primary_teacher_id = %s
                        AND    academic_session   = %s
                    ) THEN 'year_tutor'
                    ELSE 'class_teacher'
                END                                             AS teacher_role
            FROM   tb_academic_classes ac
            JOIN   tb_academic_classrooms cr ON cr.id = ac.classroom_id
            JOIN   tb_academic_levels al     ON al.id = ac.level_id
            WHERE  ac.academic_session = %s
            AND    (
                ac.level_id IN (
                    SELECT level_id
                    FROM   tb_academic_classes
                    WHERE  primary_teacher_id = %s
                    AND    academic_session   = %s
                )
                OR
                ac.alternate_teacher_id = %s
            )
            ORDER  BY al.level_name, cr.classroom_name
        """, (
        enterprise_id, academic_session,
        academic_session,
        enterprise_id, academic_session,
        enterprise_id))
        return ent_cur.fetchall()
    except Exception as e:
        logger.warning(
            "Could not fetch classes for teacher %s: %s", enterprise_id, e
        )
        return []

