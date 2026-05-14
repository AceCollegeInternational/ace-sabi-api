"""
services/kpi_engine.py — KPI score computation engine.

This service reads data from all three databases, applies the active weights
from kpi_weights, and writes a complete score record to kpi_scores.

Each index is normalised to a 0–100 raw score before weighting.
The final score = sum of (raw_score × weight / 100) across all indices.

Called by:
    POST /kpi/compute/{teacher_id}
    POST /kpi/compute/all
"""

import logging
from datetime import date
from typing import Optional

from database.connections import get_sabi, get_enterprise, get_moodle
import config

logger = logging.getLogger(__name__)


# =============================================================================
# NORMALISATION HELPERS
# Each function returns a float 0.0–100.0 representing the raw index score.
# =============================================================================

def _pct_to_score(value: Optional[float]) -> float:
    """Clamp a percentage value to 0–100."""
    if value is None:
        return 0.0
    return max(0.0, min(100.0, float(value)))


def _rate_to_score(count: int, total: int) -> float:
    """Convert a count/total ratio to a 0–100 score."""
    if total == 0:
        return 0.0
    return _pct_to_score(count / total * 100)


def _invert_score(score: float) -> float:
    """For incident_rate: fewer incidents = higher score."""
    return 100.0 - _pct_to_score(score)


def _marking_score(avg_days: Optional[float], policy_days: int) -> float:
    """
    Score marking timeliness on a sliding scale.
    Submitted within policy_days = 100. Each extra day beyond policy = -10 points.
    """
    if avg_days is None:
        return 0.0
    if avg_days <= policy_days:
        return 100.0
    over = avg_days - policy_days
    return max(0.0, 100.0 - (over * 10))


def _pd_score(weighted_hours: float, target_hours: float = 8.0) -> float:
    """
    Normalise weighted PD hours against a per-term target.
    Default target = 8 weighted hours per term. Capped at 100.
    """
    if weighted_hours <= 0:
        return 0.0
    return min(100.0, weighted_hours / target_hours * 100)


def _mentorship_score(session_count: int, target: int = 4) -> float:
    """
    Normalise confirmed mentorship sessions against a per-term target.
    Default target = 4 sessions. Capped at 100.
    """
    return min(100.0, session_count / target * 100)


def _contribution_score(contribution_count: int, adoption_total: int) -> float:
    """
    Score curriculum contributions based on count and adoption.
    1 contribution = 50 points. Each adoption adds 10, capped at 100.
    """
    if contribution_count == 0:
        return 0.0
    base  = min(100.0, contribution_count * 50.0)
    bonus = min(50.0,  adoption_total    * 10.0)
    return min(100.0, base + bonus)


def _pastoral_score(log_count: int, target: int = 5) -> float:
    """
    Score pastoral engagement by number of logs filed per term.
    Target = 5 logs. Capped at 100.
    """
    return min(100.0, log_count / target * 100)


# =============================================================================
# DATA FETCHERS
# =============================================================================

def _fetch_weights(cur) -> dict:
    """Return {index_key: weight} for all active weights."""
    cur.execute("""
        SELECT ki.index_key, kw.weight
        FROM   kpi_weights kw
        JOIN   kpi_indices ki ON ki.id = kw.index_id
    """)
    return {row["index_key"]: float(row["weight"]) for row in cur.fetchall()}


def _fetch_attendance_stats(cur, teacher_id: int, term_id: int) -> dict:
    """Fetch attendance and punctuality stats from sabi_db."""
    cur.execute("""
        SELECT
            COUNT(*)                                            AS days_logged,
            SUM(status IN ('present','late'))                  AS days_attended,
            SUM(status = 'absent')                             AS days_absent,
            SUM(status = 'late')                               AS days_late,
            AVG(CASE WHEN minutes_late > 0 THEN minutes_late END) AS avg_minutes_late,
            -- Punctuality: days present on time / days not on approved leave
            ROUND(
                100.0 * SUM(status IN ('present', 'late'))
                / NULLIF(SUM(status NOT IN ('approved_leave','public_holiday')),0),
            2) AS punctuality_pct,
            ROUND(
                100.0 * SUM(status IN ('present','late'))
                / NULLIF(SUM(status NOT IN ('approved_leave','public_holiday')),0),
            2) AS attendance_pct
        FROM   teacher_attendance
        WHERE  teacher_id = %s AND term_id = %s
    """, (teacher_id, term_id))
    return cur.fetchone() or {}


def _fetch_lesson_plan_stats(cur, teacher_id: int, term_id: int) -> dict:
    cur.execute("""
        SELECT
            COUNT(*)                          AS total_weeks,
            SUM(is_on_time  = TRUE)           AS on_time,
            SUM(is_on_topic = TRUE)           AS on_topic,
            ROUND(100.0 * SUM(is_on_time=TRUE)  / NULLIF(COUNT(*),0),2) AS on_time_pct,
            ROUND(100.0 * SUM(is_on_topic=TRUE) / NULLIF(COUNT(*),0),2) AS on_topic_pct
        FROM   lesson_plan_submissions
        WHERE  teacher_id=%s AND term_id=%s
    """, (teacher_id, term_id))
    return cur.fetchone() or {}


def _fetch_observation_avg(cur, teacher_id: int, term_id: int) -> Optional[float]:
    cur.execute("""
        SELECT AVG(total_score) AS avg_score
        FROM   lesson_observations
        WHERE  teacher_id=%s AND term_id=%s
    """, (teacher_id, term_id))
    row = cur.fetchone()
    return float(row["avg_score"]) if row and row["avg_score"] is not None else None


def _fetch_marking_stats(cur, teacher_id: int, term_id: int) -> dict:
    cur.execute("""
        SELECT
            COUNT(*)                                         AS total,
            SUM(scores_submitted_at IS NOT NULL)             AS submitted,
            AVG(days_to_submit)                              AS avg_days,
            MIN(policy_days)                                 AS policy_days
        FROM   marking_timeliness
        WHERE  teacher_id=%s AND term_id=%s
    """, (teacher_id, term_id))
    return cur.fetchone() or {}


def _fetch_pd_stats(cur, teacher_id: int, term_id: int) -> float:
    """Return total weighted PD hours for verified records."""
    cur.execute("""
        SELECT COALESCE(SUM(pd.duration_hours * ptw.weight_multiplier),0) AS weighted_hours
        FROM   pd_logs pd
        JOIN   pd_type_weights ptw ON ptw.pd_type = pd.pd_type
        WHERE  pd.teacher_id=%s AND pd.term_id=%s AND pd.is_verified=TRUE
    """, (teacher_id, term_id))
    row = cur.fetchone()
    return float(row["weighted_hours"]) if row else 0.0


def _fetch_mentorship_count(cur, teacher_id: int, term_id: int) -> int:
    """Count fully confirmed mentorship sessions (as mentor or mentee)."""
    cur.execute("""
        SELECT COUNT(*) AS cnt
        FROM   mentorship_logs
        WHERE  (mentor_id=%s OR mentee_id=%s)
        AND    term_id=%s
        AND    mentor_confirmed=TRUE AND mentee_confirmed=TRUE
    """, (teacher_id, teacher_id, term_id))
    row = cur.fetchone()
    return int(row["cnt"]) if row else 0


def _fetch_contribution_stats(cur, teacher_id: int, term_id: int) -> dict:
    cur.execute("""
        SELECT COUNT(*) AS cnt, COALESCE(SUM(adoption_count),0) AS adoptions
        FROM   curriculum_contributions
        WHERE  teacher_id=%s AND term_id=%s
    """, (teacher_id, term_id))
    return cur.fetchone() or {"cnt": 0, "adoptions": 0}


def _fetch_pastoral_count(cur, teacher_id: int, term_id: int) -> int:
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM pastoral_logs
        WHERE  teacher_id=%s AND term_id=%s
    """, (teacher_id, term_id))
    row = cur.fetchone()
    return int(row["cnt"]) if row else 0


def _fetch_student_feedback_avg(
    cur, teacher_id: int, term_id: int
) -> Optional[float]:
    """
    Average aggregate_score across classes, only including classes
    that met the minimum response rate threshold.
    """
    threshold = config.MIN_STUDENT_FEEDBACK_RESPONSE_RATE
    cur.execute("""
        SELECT AVG(aggregate_score) AS avg_score
        FROM   student_feedback
        WHERE  teacher_id=%s AND term_id=%s
        AND    (response_count / NULLIF(class_size,0) * 100) >= %s
    """, (teacher_id, term_id, threshold))
    row = cur.fetchone()
    return float(row["avg_score"]) if row and row["avg_score"] is not None else None


def _fetch_incident_rate(cur, teacher_id: int, term_id: int) -> float:
    """
    Fetch incident count for this teacher's classes.
    We use pastoral_logs with type='discipline' as a proxy.
    Returns incidents as a percentage of total logs (for normalisation).
    """
    cur.execute("""
        SELECT
            SUM(log_type='discipline') AS discipline_count,
            COUNT(*)                   AS total_logs
        FROM   pastoral_logs
        WHERE  teacher_id=%s AND term_id=%s
    """, (teacher_id, term_id))
    row = cur.fetchone()
    if not row or not row["total_logs"]:
        return 0.0
    return float(row["discipline_count"]) / float(row["total_logs"]) * 100


# Enterprise DB fetchers
def _fetch_comprehension_score(
    ent_cur, enterprise_id: str, academic_session: int, term_of_session: int
) -> Optional[float]:
    """
    Average student score across all subjects taught by this teacher,
    expressed as a percentage out of 100.

    academic_session = start year of the academic year (e.g. 2025 for 2025/2026)
    term_of_session  = term number (1, 2, or 3)
    """
    try:
        ent_cur.execute("""
            SELECT
                AVG(ssr.mark_obtained / ssr.mark_obtainable * 100) AS avg_score
            FROM   tb_faculty_registrations fr
            LEFT JOIN tb_faculty_subjects fs
                ON  fs.faculty_id = fr.id
            LEFT JOIN tb_student_score_registers ssr
                ON  ssr.subject_id       = fs.subject_id
                AND ssr.academic_session = %s
                AND ssr.term_of_session  = %s
            LEFT JOIN tb_academic_assessments aa
                ON  aa.id = ssr.assessment_id
            WHERE  fr.id = %s
            AND    aa.assessment_name IN ('Test 1','Test 2','Test 3','Examination')
            AND    ssr.parent_id IS NULL
        """, (academic_session, term_of_session, enterprise_id))
        row = ent_cur.fetchone()
        return float(row["avg_score"]) if row and row["avg_score"] is not None else None
    except Exception as e:
        logger.warning("Could not fetch comprehension score for %s: %s", enterprise_id, e)
        return None


# Moodle fetchers
def _fetch_value_added(moodle_cur, enterprise_id: str, term_id: int) -> Optional[float]:
    """
    Compute value-added from Moodle evaluation scores.
    Compare students' actual scores against their predicted scores
    (based on prior term average). Returns a normalised 0–100 value
    where 50 = met prediction, >50 = exceeded, <50 = below.
    Adjust the query to match your Moodle schema.
    """
    try:
        moodle_cur.execute("""
            SELECT AVG(actual_score - predicted_score) AS avg_delta
            FROM   mdl_value_added_view
            WHERE  teacher_enterprise_id = %s AND term_id = %s
        """, (enterprise_id, term_id))
        row = moodle_cur.fetchone()
        if row and row["avg_delta"] is not None:
            # Map delta to 0–100: delta of 0 = 50, +20 = 100, -20 = 0
            return _pct_to_score(50.0 + float(row["avg_delta"]) * 2.5)
        return None
    except Exception as e:
        logger.warning("Could not fetch value-added for %s: %s", enterprise_id, e)
        return None


def _fetch_learning_retention(moodle_cur, enterprise_id: str, term_id: int) -> Optional[float]:
    """
    Fetch learning retention score from Moodle revision question data.
    Adjust to match your Moodle schema.
    """
    try:
        moodle_cur.execute("""
            SELECT AVG(retention_score) AS avg_retention
            FROM   mdl_retention_view
            WHERE  teacher_enterprise_id = %s AND term_id = %s
        """, (enterprise_id, term_id))
        row = moodle_cur.fetchone()
        return float(row["avg_retention"]) if row and row["avg_retention"] is not None else None
    except Exception as e:
        logger.warning("Could not fetch retention for %s: %s", enterprise_id, e)
        return None


# =============================================================================
# MAIN COMPUTATION
# =============================================================================

def compute_kpi(teacher_id: int, term_id: int) -> dict:
    """
    Compute and persist the KPI score for one teacher in one term.
    Returns the full score record.
    """
    notes = []   # warnings about missing data

    with get_sabi() as (sabi_conn, sabi_cur):

        # ── Validate teacher and term ────────────────────────────────────────
        sabi_cur.execute(
            "SELECT id, enterprise_id FROM teachers WHERE id=%s AND is_active=TRUE",
            (teacher_id,)
        )
        teacher = sabi_cur.fetchone()
        if not teacher:
            raise ValueError(f"Active teacher {teacher_id} not found.")

        sabi_cur.execute("SELECT id FROM academic_terms WHERE id=%s", (term_id,))
        if not sabi_cur.fetchone():
            raise ValueError(f"Term {term_id} not found.")

        # Fetch term details needed for enterprise DB queries.
        # academic_session = start year only (e.g. 2025 from "2025/2026")
        # term_of_session  = term number (1, 2, or 3)
        sabi_cur.execute(
            "SELECT academic_year, term_number FROM academic_terms WHERE id=%s",
            (term_id,)
        )
        term_row = sabi_cur.fetchone()
        academic_session = int(term_row["academic_year"].split("/")[0])
        term_of_session  = int(term_row["term_number"])

        enterprise_id = teacher["enterprise_id"]

        # ── Load weights ─────────────────────────────────────────────────────
        weights = _fetch_weights(sabi_cur)

        # ── Fetch all sabi_db data ────────────────────────────────────────────
        att       = _fetch_attendance_stats(sabi_cur, teacher_id, term_id)
        lp        = _fetch_lesson_plan_stats(sabi_cur, teacher_id, term_id)
        obs_avg   = _fetch_observation_avg(sabi_cur, teacher_id, term_id)
        mk        = _fetch_marking_stats(sabi_cur, teacher_id, term_id)
        pd_hrs    = _fetch_pd_stats(sabi_cur, teacher_id, term_id)
        mentor_ct = _fetch_mentorship_count(sabi_cur, teacher_id, term_id)
        contrib   = _fetch_contribution_stats(sabi_cur, teacher_id, term_id)
        pastoral  = _fetch_pastoral_count(sabi_cur, teacher_id, term_id)
        sf_avg    = _fetch_student_feedback_avg(sabi_cur, teacher_id, term_id)
        inc_rate  = _fetch_incident_rate(sabi_cur, teacher_id, term_id)

        # ── Disciplinary gateway ─────────────────────────────────────────────
        sabi_cur.execute("""
            SELECT COUNT(*) AS cnt FROM disciplinary_gateway
            WHERE  teacher_id=%s AND is_active=TRUE
        """, (teacher_id,))
        active_actions = sabi_cur.fetchone()["cnt"]
        is_eligible = (active_actions == 0)
        ineligibility_reason = (
            None if is_eligible
            else f"{active_actions} active disciplinary action(s)."
        )

        # ── Previous term score (for delta) ──────────────────────────────────
        sabi_cur.execute("""
            SELECT ks.total_score
            FROM   kpi_scores ks
            JOIN   academic_terms at ON at.id = ks.term_id
            WHERE  ks.teacher_id=%s
            AND    ks.term_id != %s
            ORDER  BY at.academic_year DESC, at.term_number DESC
            LIMIT  1
        """, (teacher_id, term_id))
        prev_row = sabi_cur.fetchone()
        prev_score = float(prev_row["total_score"]) if prev_row else None

        # ── Fetch from enterprise and Moodle (outside sabi context) ─────────────
        comprehension  = None
        value_added    = None
        learning_ret   = None
        
        if enterprise_id:
            with get_enterprise() as (_, ent_cur):
                comprehension = _fetch_comprehension_score(ent_cur, enterprise_id, academic_session, term_of_session)
                
            with get_moodle() as (_, moodle_cur):
                value_added  = _fetch_value_added(moodle_cur, enterprise_id, term_id)
                learning_ret = _fetch_learning_retention(moodle_cur, enterprise_id, term_id)
        else:
            notes.append("No enterprise_id on teacher — comprehension, value-added, "
                         "retention, and parent engagement scores set to 0.")
    
        # ── Normalise raw scores ─────────────────────────────────────────────────
        if comprehension is None:
            notes.append("comprehension_score: no data from enterprise DB.")
        if value_added is None:
            notes.append("value_added_progress: no data from Moodle.")
        if learning_ret is None:
            notes.append("learning_retention: no data from Moodle.")
        if obs_avg is None:
            notes.append("observation_score: no observations recorded this term.")
        if sf_avg is None:
            notes.append("student_feedback: no qualifying feedback this term.")

        raw = {
            "comprehension_score":     _pct_to_score(comprehension),
            "value_added_progress":    _pct_to_score(value_added),
            "learning_retention":      _pct_to_score(learning_ret),
            "observation_score":       _pct_to_score(obs_avg),
            "punctuality":             _pct_to_score(att.get("punctuality_pct")),
            "lesson_plan_compliance":  _pct_to_score(lp.get("on_time_pct")),
            "teacher_attendance":      _pct_to_score(att.get("attendance_pct")),
            "marking_timeliness":      _marking_score(
                                           mk.get("avg_days"),
                                           int(mk.get("policy_days") or config.DEFAULT_MARKING_POLICY_DAYS)
                                       ),
            "pd_quality_score":        _pd_score(pd_hrs),
            "peer_mentorship":         _mentorship_score(mentor_ct),
            "curriculum_contribution": _contribution_score(
                                           int(contrib.get("cnt", 0)),
                                           int(contrib.get("adoptions", 0))
                                       ),
            "pastoral_logs":           _pastoral_score(pastoral),
            "student_feedback":        _pct_to_score(sf_avg),
            "incident_rate":           _invert_score(inc_rate),
        }
        
        # ── Apply lateness penalty to punctuality score ──────────────────────────
        # Level 2 (4–9 late arrivals): deduction = (late_count - 3) * 0.5 points
        # Level 3 (10+ late arrivals): deduction = min((late_count - 9), 8) points
        sabi_cur.execute("""
            SELECT COUNT(*) AS late_count
            FROM   teacher_attendance
            WHERE  teacher_id = %s
            AND    term_id    = %s
            AND    status     = 'late'
        """, (teacher_id, term_id))
        late_row   = sabi_cur.fetchone()
        late_count = int(late_row["late_count"]) if late_row else 0
    
        if late_count >= 10:
            penalty_pts = min(late_count - 9, 8)
        elif late_count >= 4:
            penalty_pts = (late_count - 3) * 0.5
        else:
            penalty_pts = 0.0
    
        if penalty_pts > 0:
            punctuality_weight = weights.get("punctuality", 8.0)
            if punctuality_weight > 0:
                raw_deduction = penalty_pts * 100.0 / punctuality_weight
                raw["punctuality"] = max(0.0, raw["punctuality"] - raw_deduction)
                notes.append(
                    f"Lateness penalty applied: {late_count} late arrivals, "
                    f"{penalty_pts} KPI points deducted from punctuality."
                )
        
        # ── Apply weights ────────────────────────────────────────────────────────
        # weighted contribution = raw_score × weight / 100
        weighted = {k: raw[k] * weights.get(k, 0.0) / 100.0 for k in raw}

        # Category subtotals
        academic    = sum(weighted[k] for k in
                          ["comprehension_score","value_added_progress",
                           "learning_retention","observation_score"])
        reliability = sum(weighted[k] for k in
                          ["punctuality","lesson_plan_compliance",
                           "teacher_attendance","marking_timeliness"])
        growth      = sum(weighted[k] for k in
                          ["pd_quality_score","peer_mentorship","curriculum_contribution"])
        care        = sum(weighted[k] for k in
                          ["pastoral_logs","student_feedback","incident_rate"])

        total = academic + reliability + growth + care
        delta = round(total - prev_score, 3) if prev_score is not None else None

    # ── Persist ──────────────────────────────────────────────────────────────
    with get_sabi() as (_, sabi_cur):
        sabi_cur.execute("""
            INSERT INTO kpi_scores (
                teacher_id, term_id,
                score_academic_impact, score_professional_reliability,
                score_professional_growth, score_institutional_care,
                raw_comprehension_score, raw_value_added_progress,
                raw_learning_retention, raw_observation_score,
                raw_punctuality, raw_lesson_plan_compliance,
                raw_teacher_attendance, raw_marking_timeliness,
                raw_pd_quality_score, raw_peer_mentorship,
                raw_curriculum_contribution, raw_pastoral_logs,
                raw_student_feedback,
                raw_incident_rate,
                total_score, is_eligible, ineligibility_reason,
                previous_term_score, score_delta,
                computed_at, computation_notes
            ) VALUES (
                %s,%s, %s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s, NOW(),%s
            )
            ON DUPLICATE KEY UPDATE
                score_academic_impact          = VALUES(score_academic_impact),
                score_professional_reliability = VALUES(score_professional_reliability),
                score_professional_growth      = VALUES(score_professional_growth),
                score_institutional_care       = VALUES(score_institutional_care),
                raw_comprehension_score        = VALUES(raw_comprehension_score),
                raw_value_added_progress       = VALUES(raw_value_added_progress),
                raw_learning_retention         = VALUES(raw_learning_retention),
                raw_observation_score          = VALUES(raw_observation_score),
                raw_punctuality                = VALUES(raw_punctuality),
                raw_lesson_plan_compliance     = VALUES(raw_lesson_plan_compliance),
                raw_teacher_attendance         = VALUES(raw_teacher_attendance),
                raw_marking_timeliness         = VALUES(raw_marking_timeliness),
                raw_pd_quality_score           = VALUES(raw_pd_quality_score),
                raw_peer_mentorship            = VALUES(raw_peer_mentorship),
                raw_curriculum_contribution    = VALUES(raw_curriculum_contribution),
                raw_pastoral_logs              = VALUES(raw_pastoral_logs),
                raw_student_feedback           = VALUES(raw_student_feedback),
                raw_incident_rate              = VALUES(raw_incident_rate),
                total_score                    = VALUES(total_score),
                is_eligible                    = VALUES(is_eligible),
                ineligibility_reason           = VALUES(ineligibility_reason),
                previous_term_score            = VALUES(previous_term_score),
                score_delta                    = VALUES(score_delta),
                computed_at                    = NOW(),
                computation_notes              = VALUES(computation_notes)
        """, (
            teacher_id, term_id,
            round(academic, 3), round(reliability, 3),
            round(growth, 3),   round(care, 3),
            raw["comprehension_score"],    raw["value_added_progress"],
            raw["learning_retention"],     raw["observation_score"],
            raw["punctuality"],            raw["lesson_plan_compliance"],
            raw["teacher_attendance"],     raw["marking_timeliness"],
            raw["pd_quality_score"],       raw["peer_mentorship"],
            raw["curriculum_contribution"],raw["pastoral_logs"],
            raw["student_feedback"],       raw["incident_rate"],
            round(total, 3), is_eligible, ineligibility_reason,
            prev_score, delta,
            "; ".join(notes) if notes else None
        ))

    return {
        "teacher_id":  teacher_id,
        "term_id":     term_id,
        "total_score": round(total, 2),
        "is_eligible": is_eligible,
        "categories": {
            "academic_impact":           round(academic, 2),
            "professional_reliability":  round(reliability, 2),
            "professional_growth":       round(growth, 2),
            "institutional_care":        round(care, 2),
        },
        "raw_scores":  {k: round(v, 2) for k, v in raw.items()},
        "score_delta": delta,
        "notes":       notes,
    }
