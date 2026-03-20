"""
routes/at_risk.py — At-Risk Student Early Warning System (Use Case 06).

Identifies students who are at risk based on two independent signals:
  1. Attendance below threshold (default 75%)
  2. Academic average below threshold (default 50%)

A student flagged on either signal is considered at-risk.
A student flagged on both signals is considered high-risk.

All data is read from the enterprise DB. Nothing is written to sabi_db
by this module — it is a pure read/reporting layer.

Endpoints:
    GET /at-risk/class/{class_id}         at-risk students in one class
    GET /at-risk/teacher/{teacher_id}     at-risk students across all
                                          classes a teacher is assigned to
    GET /at-risk/student/{student_id}     full risk profile for one student
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, status

from database.connections import get_sabi, get_enterprise
from database.enterprise_queries import (
    fetch_student,
    fetch_student_current_class,
    fetch_student_attendance_summary,
    fetch_student_score_trend,
    fetch_students_below_attendance_threshold,
    fetch_students_below_score_threshold,
    fetch_teacher_classes,
)

router = APIRouter()


# =============================================================================
# HELPERS
# =============================================================================

def _get_session_params(term_id: int) -> tuple:
    """
    Resolve academic_session and term_of_session from a sabi_db term_id.
    Returns (academic_session, term_of_session).
    """
    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT academic_year, term_number FROM academic_terms WHERE id = %s",
            (term_id,)
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Term {term_id} not found.")
    academic_session = int(row["academic_year"].split("/")[0])
    term_of_session  = int(row["term_number"])
    return academic_session, term_of_session


def _get_current_term_id() -> int:
    """Return the current term_id from sabi_db."""
    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT id FROM academic_terms WHERE is_current = TRUE LIMIT 1"
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No current term set.")
    return row["id"]


def _classify_risk(attendance_flagged: bool, score_flagged: bool) -> str:
    """Return risk level string based on which signals are triggered."""
    if attendance_flagged and score_flagged:
        return "high"
    if attendance_flagged or score_flagged:
        return "at_risk"
    return "ok"


# =============================================================================
# ROUTES
# =============================================================================

@router.get("/class/{class_id}")
def at_risk_by_class(
    class_id:            str,
    term_id:             Optional[int]   = None,
    attendance_threshold: Optional[float] = 75.0,
    score_threshold:      Optional[float] = 50.0,
):
    """
    Return all at-risk students in a specific class.

    A student is flagged if they fall below either threshold:
      - attendance_threshold: minimum attendance percentage (default 75%)
      - score_threshold:      minimum average score percentage (default 50%)

    Risk levels:
      high     — below both thresholds
      at_risk  — below one threshold
      ok       — above both thresholds (not returned unless all=true)

    Returns two lists:
      flagged  — students below at least one threshold, with risk level
      summary  — counts by risk level
    """
    if term_id is None:
        term_id = _get_current_term_id()

    academic_session, term_of_session = _get_session_params(term_id)

    with get_enterprise() as (_, ent_cur):
        low_attendance = fetch_students_below_attendance_threshold(
            ent_cur, class_id, academic_session,
            term_of_session, attendance_threshold
        )
        low_scores = fetch_students_below_score_threshold(
            ent_cur, class_id, academic_session,
            term_of_session, score_threshold
        )

    # Build lookup sets for fast membership check
    low_att_ids   = {s["student_id"] for s in low_attendance}
    low_score_ids = {s["student_id"] for s in low_scores}
    all_flagged   = low_att_ids | low_score_ids

    # Build combined record per flagged student
    # Index records by student_id for quick lookup
    att_by_id   = {s["student_id"]: s for s in low_attendance}
    score_by_id = {s["student_id"]: s for s in low_scores}

    flagged = []
    for sid in all_flagged:
        att_record   = att_by_id.get(sid, {})
        score_record = score_by_id.get(sid, {})

        flagged.append({
            "student_id":       sid,
            "student_name":     (att_record or score_record).get("student_name"),
            "primary_contact":  (att_record or score_record).get("primary_contact"),
            "attendance_pct":   att_record.get("attendance_pct"),
            "overall_avg":      score_record.get("overall_avg"),
            "attendance_flagged": sid in low_att_ids,
            "score_flagged":      sid in low_score_ids,
            "risk_level":       _classify_risk(
                                    sid in low_att_ids,
                                    sid in low_score_ids
                                ),
        })

    # Sort: high risk first, then at_risk
    flagged.sort(key=lambda x: (
        0 if x["risk_level"] == "high" else 1
    ))

    high_count    = sum(1 for s in flagged if s["risk_level"] == "high")
    at_risk_count = sum(1 for s in flagged if s["risk_level"] == "at_risk")

    return {
        "class_id":            class_id,
        "term_id":             term_id,
        "academic_session":    academic_session,
        "term_of_session":     term_of_session,
        "thresholds": {
            "attendance": attendance_threshold,
            "score":      score_threshold,
        },
        "summary": {
            "total_flagged": len(flagged),
            "high_risk":     high_count,
            "at_risk":       at_risk_count,
        },
        "flagged": flagged,
    }


@router.get("/teacher/{teacher_id}")
def at_risk_by_teacher(
    teacher_id:           int,
    term_id:              Optional[int]   = None,
    attendance_threshold: Optional[float] = 75.0,
    score_threshold:      Optional[float] = 50.0,
):
    """
    Return at-risk students across all classes a teacher is assigned to
    as primary or alternate teacher.

    Useful for a class teacher's morning briefing — one call returns
    the full picture across all their classes.
    """
    if term_id is None:
        term_id = _get_current_term_id()

    academic_session, term_of_session = _get_session_params(term_id)

    # Get teacher's enterprise_id from sabi_db
    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT enterprise_id FROM teachers WHERE id = %s AND is_active = TRUE",
            (teacher_id,)
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Active teacher not found.")
    if not row["enterprise_id"]:
        raise HTTPException(
            status_code=422,
            detail="Teacher has no enterprise_id — run /teachers/sync first."
        )

    enterprise_id = row["enterprise_id"]

    with get_enterprise() as (_, ent_cur):
        # Get all classes this teacher is responsible for
        classes = fetch_teacher_classes(ent_cur, enterprise_id, academic_session)

        if not classes:
            return {
                "teacher_id":   teacher_id,
                "term_id":      term_id,
                "classes":      [],
                "total_flagged": 0,
                "message":      "No classes found for this teacher in this session.",
            }

        # Run at-risk check per class
        results = []
        total_flagged = 0

        for cls in classes:
            low_attendance = fetch_students_below_attendance_threshold(
                ent_cur, cls["class_id"], academic_session,
                term_of_session, attendance_threshold
            )
            low_scores = fetch_students_below_score_threshold(
                ent_cur, cls["class_id"], academic_session,
                term_of_session, score_threshold
            )

            low_att_ids   = {s["student_id"] for s in low_attendance}
            low_score_ids = {s["student_id"] for s in low_scores}
            all_flagged   = low_att_ids | low_score_ids
            total_flagged += len(all_flagged)

            att_by_id   = {s["student_id"]: s for s in low_attendance}
            score_by_id = {s["student_id"]: s for s in low_scores}

            flagged = []
            for sid in all_flagged:
                att_rec   = att_by_id.get(sid, {})
                score_rec = score_by_id.get(sid, {})
                flagged.append({
                    "student_id":         sid,
                    "student_name":       (att_rec or score_rec).get("student_name"),
                    "primary_contact":    (att_rec or score_rec).get("primary_contact"),
                    "attendance_pct":     att_rec.get("attendance_pct"),
                    "overall_avg":        score_rec.get("overall_avg"),
                    "attendance_flagged": sid in low_att_ids,
                    "score_flagged":      sid in low_score_ids,
                    "risk_level":         _classify_risk(
                                              sid in low_att_ids,
                                              sid in low_score_ids
                                          ),
                })

            flagged.sort(key=lambda x: 0 if x["risk_level"] == "high" else 1)

            results.append({
                "class_id":      cls["class_id"],
                "class_name":    cls["class_name"],
                "level_name":    cls["level_name"],
                "teacher_role":  cls["teacher_role"],
                "flagged_count": len(flagged),
                "flagged":       flagged,
            })

    return {
        "teacher_id":    teacher_id,
        "enterprise_id": enterprise_id,
        "term_id":       term_id,
        "thresholds": {
            "attendance": attendance_threshold,
            "score":      score_threshold,
        },
        "total_flagged": total_flagged,
        "classes":       results,
    }


@router.get("/student/{student_id}")
def at_risk_student_profile(
    student_id: str,
    term_id:    Optional[int] = None,
):
    """
    Full risk profile for a single student.

    Returns:
      - Student identity and current class
      - Full attendance summary for the term
      - Overall academic average and per-subject breakdown
      - Risk level assessment
      - Primary contact number for parent notification
    """
    if term_id is None:
        term_id = _get_current_term_id()

    academic_session, term_of_session = _get_session_params(term_id)

    with get_enterprise() as (_, ent_cur):
        student = fetch_student(ent_cur, student_id)
        if not student:
            raise HTTPException(
                status_code=404,
                detail=f"Student {student_id} not found in enterprise DB."
            )

        current_class = fetch_student_current_class(
            ent_cur, student_id, academic_session
        )
        attendance = fetch_student_attendance_summary(
            ent_cur, student_id, academic_session, term_of_session
        )
        scores = fetch_student_score_trend(
            ent_cur, student_id, academic_session, term_of_session
        )

    # Determine risk flags
    attendance_flagged = (
        attendance["attendance_pct"] is not None
        and attendance["attendance_pct"] < 75.0
    )
    score_flagged = (
        scores["overall_avg"] is not None
        and scores["overall_avg"] < 50.0
    )
    risk_level = _classify_risk(attendance_flagged, score_flagged)

    return {
        "student_id":          student_id,
        "student_name":        student.get("full_name"),
        "gender":              student.get("student_gender"),
        "admission_status":    student.get("admission_status"),
        "level":               student.get("level_name"),
        "current_class":       current_class,
        "primary_contact":     student.get("primary_contact"),
        "term_id":             term_id,
        "academic_session":    academic_session,
        "term_of_session":     term_of_session,
        "attendance":          attendance,
        "academic_performance": scores,
        "risk_assessment": {
            "risk_level":          risk_level,
            "attendance_flagged":  attendance_flagged,
            "score_flagged":       score_flagged,
            "attendance_threshold": 75.0,
            "score_threshold":     50.0,
        },
    }
