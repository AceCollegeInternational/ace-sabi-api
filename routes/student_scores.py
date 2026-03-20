"""
routes/student_scores.py — Student academic score queries from enterprise DB.

All data is read from tb_student_score_registers and related tables.
Nothing is written here.

Only objective assessments are included — hardcoded in every query:
    'Test 1', 'Test 2', 'Test 3', 'Examination'
Excluded: Moral Test (subjective), Homework (completion-based)
Parent subject aggregates excluded (parent_id IS NULL = leaf subjects only)

Endpoints:
    GET /scores/student/{student_id}                all scores for a student
    GET /scores/student/{student_id}/summary        per-subject breakdown
    GET /scores/class/{class_id}/subject/{subject_id}  class scores for one subject
    GET /scores/class/{class_id}/summary            class average per subject
"""

from typing import Optional
from fastapi import APIRouter, HTTPException

from database.connections import get_sabi, get_enterprise

router = APIRouter()


# =============================================================================
# HELPERS
# =============================================================================

def _get_session_and_term(term_id: Optional[int]) -> tuple:
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


def _require_student(ent_cur, student_id: str) -> dict:
    ent_cur.execute("""
        SELECT
            CONCAT(
                last_name, ' ', first_name,
                CASE WHEN other_name != ''
                     THEN CONCAT(' ', other_name)
                     ELSE '' END
            ) AS full_name,
            admission_status
        FROM   tb_student_registrations
        WHERE  id = %s
    """, (student_id,))
    row = ent_cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found.")
    return row


# =============================================================================
# ROUTES
# =============================================================================

@router.get("/student/{student_id}")
def get_student_scores(
    student_id: str,
    term_id:    Optional[int] = None,
):
    """
    All assessment scores for a student in a term.
    One row per subject per assessment type.
    """
    tid, academic_session, term_of_session, term_name = _get_session_and_term(term_id)

    with get_enterprise() as (_, ent_cur):
        student = _require_student(ent_cur, student_id)

        ent_cur.execute("""
            SELECT
                s.id                                            AS subject_id,
                s.subject_name,
                aa.assessment_name,
                ssr.mark_obtained,
                ssr.mark_obtainable,
                ROUND(
                    ssr.mark_obtained / NULLIF(ssr.mark_obtainable, 0) * 100, 2
                )                                               AS score_pct
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa
                ON  aa.id = ssr.assessment_id
            JOIN   tb_academic_subjects s
                ON  s.id = ssr.subject_id
            WHERE  ssr.student_id       = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    aa.assessment_name IN ('Test 1','Test 2','Test 3','Examination')
            ORDER  BY s.subject_name, aa.assessment_name
        """, (student_id, academic_session, term_of_session))
        scores = ent_cur.fetchall()

    return {
        "student_id":       student_id,
        "full_name":        student["full_name"],
        "term_id":          tid,
        "term_name":        term_name,
        "academic_session": academic_session,
        "term_of_session":  term_of_session,
        "total_records":    len(scores),
        "scores":           scores,
    }


@router.get("/student/{student_id}/summary")
def get_student_score_summary(
    student_id: str,
    term_id:    Optional[int] = None,
):
    """
    Academic performance summary for one student.
    Overall average plus per-subject breakdown.
    Weakest subjects sorted to the top.
    """
    tid, academic_session, term_of_session, term_name = _get_session_and_term(term_id)

    with get_enterprise() as (_, ent_cur):
        student = _require_student(ent_cur, student_id)

        ent_cur.execute("""
            SELECT
                ROUND(
                    AVG(ssr.mark_obtained / NULLIF(ssr.mark_obtainable, 0) * 100), 2
                )                                               AS overall_avg,
                COUNT(DISTINCT ssr.subject_id)                  AS subjects_assessed,
                COUNT(*)                                        AS total_assessments
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
            WHERE  ssr.student_id       = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    aa.assessment_name IN ('Test 1','Test 2','Test 3','Examination')
        """, (student_id, academic_session, term_of_session))
        overall = ent_cur.fetchone()

        ent_cur.execute("""
            SELECT
                s.id                                            AS subject_id,
                s.subject_name,
                ROUND(
                    AVG(ssr.mark_obtained / NULLIF(ssr.mark_obtainable, 0) * 100), 2
                )                                               AS subject_avg,
                COUNT(*)                                        AS assessments_count,
                MAX(CASE WHEN aa.assessment_name = 'Test 1'
                    THEN ROUND(ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100,2) END) AS test_1_pct,
                MAX(CASE WHEN aa.assessment_name = 'Test 2'
                    THEN ROUND(ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100,2) END) AS test_2_pct,
                MAX(CASE WHEN aa.assessment_name = 'Test 3'
                    THEN ROUND(ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100,2) END) AS test_3_pct,
                MAX(CASE WHEN aa.assessment_name = 'Examination'
                    THEN ROUND(ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100,2) END) AS exam_pct
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
            JOIN   tb_academic_subjects s     ON s.id  = ssr.subject_id
            WHERE  ssr.student_id       = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    aa.assessment_name IN ('Test 1','Test 2','Test 3','Examination')
            GROUP  BY s.id, s.subject_name
            ORDER  BY subject_avg ASC
        """, (student_id, academic_session, term_of_session))
        by_subject = ent_cur.fetchall()

    for s in by_subject:
        s["is_below_threshold"] = (
            s["subject_avg"] is not None and s["subject_avg"] < 50.0
        )

    return {
        "student_id":            student_id,
        "full_name":             student["full_name"],
        "term_id":               tid,
        "term_name":             term_name,
        "academic_session":      academic_session,
        "term_of_session":       term_of_session,
        "overall_avg":           overall["overall_avg"] if overall else None,
        "subjects_assessed":     overall["subjects_assessed"] if overall else 0,
        "total_assessments":     overall["total_assessments"] if overall else 0,
        "below_threshold_count": sum(1 for s in by_subject if s["is_below_threshold"]),
        "score_threshold":       50.0,
        "by_subject":            by_subject,
    }


@router.get("/class/{class_id}/subject/{subject_id}")
def get_class_subject_scores(
    class_id:   str,
    subject_id: str,
    term_id:    Optional[int] = None,
):
    """
    All students' scores for one subject in a class.
    Weakest students sorted to the top.
    """
    tid, academic_session, term_of_session, term_name = _get_session_and_term(term_id)

    with get_enterprise() as (_, ent_cur):
        ent_cur.execute(
            "SELECT subject_name FROM tb_academic_subjects WHERE id = %s",
            (subject_id,)
        )
        subject = ent_cur.fetchone()
        if not subject:
            raise HTTPException(status_code=404, detail=f"Subject {subject_id} not found.")

        ent_cur.execute("""
            SELECT CONCAT(cr.classroom_name, ac.class_division) AS class_name
            FROM   tb_academic_classes ac
            JOIN   tb_academic_classrooms cr ON cr.id = ac.classroom_id
            WHERE  ac.id = %s AND ac.academic_session = %s
        """, (class_id, academic_session))
        class_info = ent_cur.fetchone()

        ent_cur.execute("""
            SELECT
                ssr.student_id,
                CONCAT(
                    sr.last_name, ' ', sr.first_name,
                    CASE WHEN sr.other_name != ''
                         THEN CONCAT(' ', sr.other_name)
                         ELSE '' END
                )                                               AS full_name,
                ROUND(
                    AVG(ssr.mark_obtained / NULLIF(ssr.mark_obtainable,0)*100), 2
                )                                               AS subject_avg,
                MAX(CASE WHEN aa.assessment_name = 'Test 1'
                    THEN ROUND(ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100,2) END) AS test_1_pct,
                MAX(CASE WHEN aa.assessment_name = 'Test 2'
                    THEN ROUND(ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100,2) END) AS test_2_pct,
                MAX(CASE WHEN aa.assessment_name = 'Test 3'
                    THEN ROUND(ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100,2) END) AS test_3_pct,
                MAX(CASE WHEN aa.assessment_name = 'Examination'
                    THEN ROUND(ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100,2) END) AS exam_pct
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
            JOIN   tb_student_registrations sr ON sr.id = ssr.student_id
            WHERE  ssr.class_id         = %s
            AND    ssr.subject_id       = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    aa.assessment_name IN ('Test 1','Test 2','Test 3','Examination')
            GROUP  BY ssr.student_id, sr.last_name, sr.first_name, sr.other_name
            ORDER  BY subject_avg ASC
        """, (class_id, subject_id, academic_session, term_of_session))
        students = ent_cur.fetchall()

    for s in students:
        s["is_below_threshold"] = (
            s["subject_avg"] is not None and s["subject_avg"] < 50.0
        )

    class_avg = (
        round(
            sum(s["subject_avg"] for s in students if s["subject_avg"] is not None)
            / max(len(students), 1), 2
        ) if students else None
    )

    return {
        "class_id":              class_id,
        "class_name":            class_info["class_name"] if class_info else None,
        "subject_id":            subject_id,
        "subject_name":          subject["subject_name"],
        "term_id":               tid,
        "term_name":             term_name,
        "class_average":         class_avg,
        "below_threshold_count": sum(1 for s in students if s["is_below_threshold"]),
        "score_threshold":       50.0,
        "total_students":        len(students),
        "students":              students,
    }


@router.get("/class/{class_id}/summary")
def get_class_score_summary(
    class_id: str,
    term_id:  Optional[int] = None,
):
    """
    Class average score per subject for a term.
    Weakest subjects sorted to the top.
    HOD and principal view.
    """
    tid, academic_session, term_of_session, term_name = _get_session_and_term(term_id)

    with get_enterprise() as (_, ent_cur):
        ent_cur.execute("""
            SELECT
                CONCAT(cr.classroom_name, ac.class_division)   AS class_name,
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

        ent_cur.execute("""
            SELECT
                s.id                                            AS subject_id,
                s.subject_name,
                ROUND(
                    AVG(ssr.mark_obtained / NULLIF(ssr.mark_obtainable,0)*100), 2
                )                                               AS class_avg,
                COUNT(DISTINCT ssr.student_id)                  AS students_assessed,
                ROUND(AVG(CASE WHEN aa.assessment_name = 'Test 1'
                    THEN ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100 END),2) AS test_1_avg,
                ROUND(AVG(CASE WHEN aa.assessment_name = 'Test 2'
                    THEN ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100 END),2) AS test_2_avg,
                ROUND(AVG(CASE WHEN aa.assessment_name = 'Test 3'
                    THEN ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100 END),2) AS test_3_avg,
                ROUND(AVG(CASE WHEN aa.assessment_name = 'Examination'
                    THEN ssr.mark_obtained /
                         NULLIF(ssr.mark_obtainable,0)*100 END),2) AS exam_avg
            FROM   tb_student_score_registers ssr
            JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
            JOIN   tb_academic_subjects s     ON s.id  = ssr.subject_id
            WHERE  ssr.class_id         = %s
            AND    ssr.academic_session = %s
            AND    ssr.term_of_session  = %s
            AND    aa.assessment_name IN ('Test 1','Test 2','Test 3','Examination')
            GROUP  BY s.id, s.subject_name
            ORDER  BY class_avg ASC
        """, (class_id, academic_session, term_of_session))
        subjects = ent_cur.fetchall()

    for s in subjects:
        s["is_below_threshold"] = (
            s["class_avg"] is not None and s["class_avg"] < 50.0
        )

    overall_avg = (
        round(
            sum(s["class_avg"] for s in subjects if s["class_avg"] is not None)
            / max(len(subjects), 1), 2
        ) if subjects else None
    )

    return {
        "class_id":                 class_id,
        "class_name":               class_info["class_name"],
        "level_name":               class_info["level_name"],
        "term_id":                  tid,
        "term_name":                term_name,
        "academic_session":         academic_session,
        "term_of_session":          term_of_session,
        "overall_class_average":    overall_avg,
        "subjects_assessed":        len(subjects),
        "subjects_below_threshold": sum(1 for s in subjects if s["is_below_threshold"]),
        "score_threshold":          50.0,
        "by_subject":               subjects,
    }
