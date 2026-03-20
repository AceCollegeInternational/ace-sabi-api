"""
routes/professional_growth.py — PD logs, mentorship, curriculum contributions.

Endpoints:
    POST /professional-growth/pd                   log a PD event
    POST /professional-growth/pd/{id}/verify       admin verifies a PD entry
    GET  /professional-growth/pd/teacher/{id}      PD log for one teacher
    POST /professional-growth/mentorship            log a mentorship session
    POST /professional-growth/mentorship/{id}/confirm  confirm as mentor or mentee
    GET  /professional-growth/mentorship/teacher/{id}  sessions for one teacher
    POST /professional-growth/contributions         log a curriculum resource
    PATCH /professional-growth/contributions/{id}/adoption  update adoption count
    GET  /professional-growth/summary/{term_id}    full growth summary for a term
"""

from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi

router = APIRouter()

PD_TYPES = ("subject_specific","pedagogy","technology","leadership","general")


# =============================================================================
# MODELS
# =============================================================================

class PDLog(BaseModel):
    teacher_id:      int
    term_id:         int
    pd_type:         str
    title:           str
    provider:        Optional[str] = None
    attended_on:     date
    duration_hours:  float
    evidence_ref:    Optional[str] = None
    notes:           Optional[str] = None


class MentorshipLog(BaseModel):
    term_id:          int
    mentor_id:        int
    mentee_id:        int
    session_date:     date
    duration_minutes: int
    topic:            Optional[str] = None
    notes:            Optional[str] = None


class ContributionLog(BaseModel):
    teacher_id:    int
    term_id:       int
    title:         str
    resource_type: str
    file_reference: Optional[str] = None


RESOURCE_TYPES = ("question_bank","revision_guide","teaching_aid",
                  "worksheet","scheme_of_work","other")

CONFIRM_ROLES = ("mentor", "mentee")


# =============================================================================
# PD ROUTES
# =============================================================================

@router.post("/pd", status_code=status.HTTP_201_CREATED)
def log_pd(body: PDLog):
    if body.pd_type not in PD_TYPES:
        raise HTTPException(status_code=422,
            detail=f"pd_type must be one of: {PD_TYPES}")
    if body.duration_hours <= 0:
        raise HTTPException(status_code=422, detail="duration_hours must be > 0.")

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE",
                    (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute("""
            INSERT INTO pd_logs
                (teacher_id, term_id, pd_type, title, provider,
                 attended_on, duration_hours, evidence_ref, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (body.teacher_id, body.term_id, body.pd_type, body.title,
              body.provider, body.attended_on, body.duration_hours,
              body.evidence_ref, body.notes))
        new_id = cur.lastrowid

    return {"id": new_id, "message": "PD event logged."}


@router.post("/pd/{pd_id}/verify")
def verify_pd(pd_id: int, verified_by: str):
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM pd_logs WHERE id=%s", (pd_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="PD record not found.")
        cur.execute("""
            UPDATE pd_logs
            SET    is_verified = TRUE, verified_by = %s, verified_at = NOW()
            WHERE  id = %s
        """, (verified_by, pd_id))
    return {"message": "PD event verified."}


@router.get("/pd/teacher/{teacher_id}")
def get_teacher_pd(teacher_id: int, term_id: Optional[int] = None):
    with get_sabi() as (_, cur):
        if term_id:
            cur.execute("""
                SELECT pd.*, ptw.weight_multiplier
                FROM   pd_logs pd
                JOIN   pd_type_weights ptw ON ptw.pd_type = pd.pd_type
                WHERE  pd.teacher_id=%s AND pd.term_id=%s
                ORDER  BY pd.attended_on DESC
            """, (teacher_id, term_id))
        else:
            cur.execute("""
                SELECT pd.*, ptw.weight_multiplier
                FROM   pd_logs pd
                JOIN   pd_type_weights ptw ON ptw.pd_type = pd.pd_type
                WHERE  pd.teacher_id=%s
                ORDER  BY pd.attended_on DESC
            """, (teacher_id,))
        return cur.fetchall()


# =============================================================================
# MENTORSHIP ROUTES
# =============================================================================

@router.post("/mentorship", status_code=status.HTTP_201_CREATED)
def log_mentorship(body: MentorshipLog):
    if body.mentor_id == body.mentee_id:
        raise HTTPException(status_code=422,
            detail="mentor_id and mentee_id cannot be the same person.")

    with get_sabi() as (_, cur):
        for tid, label in [(body.mentor_id, "Mentor"), (body.mentee_id, "Mentee")]:
            cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE", (tid,))
            if not cur.fetchone():
                raise HTTPException(status_code=404,
                    detail=f"{label} teacher {tid} not found.")

        cur.execute("""
            INSERT INTO mentorship_logs
                (term_id, mentor_id, mentee_id, session_date,
                 duration_minutes, topic, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (body.term_id, body.mentor_id, body.mentee_id,
              body.session_date, body.duration_minutes,
              body.topic, body.notes))
        new_id = cur.lastrowid

    return {"id": new_id,
            "message": "Mentorship session logged. Awaiting confirmation from both parties."}


@router.post("/mentorship/{session_id}/confirm")
def confirm_mentorship(session_id: int, role: str):
    """
    role must be 'mentor' or 'mentee'.
    Both parties must confirm before the session counts toward KPI.
    """
    if role not in CONFIRM_ROLES:
        raise HTTPException(status_code=422,
            detail="role must be 'mentor' or 'mentee'.")

    with get_sabi() as (_, cur):
        cur.execute("SELECT * FROM mentorship_logs WHERE id=%s", (session_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found.")

        if role == "mentor":
            cur.execute("""
                UPDATE mentorship_logs
                SET    mentor_confirmed=TRUE, mentor_confirmed_at=NOW()
                WHERE  id=%s
            """, (session_id,))
        else:
            cur.execute("""
                UPDATE mentorship_logs
                SET    mentee_confirmed=TRUE, mentee_confirmed_at=NOW()
                WHERE  id=%s
            """, (session_id,))

        # Re-fetch to return current confirmation status
        cur.execute("""
            SELECT mentor_confirmed, mentee_confirmed
            FROM   mentorship_logs WHERE id=%s
        """, (session_id,))
        updated = cur.fetchone()

    fully_confirmed = (updated["mentor_confirmed"] and updated["mentee_confirmed"])
    return {
        "mentor_confirmed": updated["mentor_confirmed"],
        "mentee_confirmed": updated["mentee_confirmed"],
        "counts_toward_kpi": fully_confirmed,
    }


@router.get("/mentorship/teacher/{teacher_id}")
def get_teacher_mentorship(teacher_id: int, term_id: Optional[int] = None):
    with get_sabi() as (_, cur):
        base = """
            SELECT ml.*,
                   CONCAT(m.first_name,' ',m.last_name) AS mentor_name,
                   CONCAT(e.first_name,' ',e.last_name) AS mentee_name
            FROM   mentorship_logs ml
            JOIN   teachers m ON m.id = ml.mentor_id
            JOIN   teachers e ON e.id = ml.mentee_id
            WHERE  (ml.mentor_id=%s OR ml.mentee_id=%s)
        """
        params = [teacher_id, teacher_id]
        if term_id:
            base += " AND ml.term_id=%s"
            params.append(term_id)
        base += " ORDER BY ml.session_date DESC"
        cur.execute(base, params)
        return cur.fetchall()


# =============================================================================
# CURRICULUM CONTRIBUTION ROUTES
# =============================================================================

@router.post("/contributions", status_code=status.HTTP_201_CREATED)
def log_contribution(body: ContributionLog):
    if body.resource_type not in RESOURCE_TYPES:
        raise HTTPException(status_code=422,
            detail=f"resource_type must be one of: {RESOURCE_TYPES}")

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM teachers WHERE id=%s AND is_active=TRUE",
                    (body.teacher_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        cur.execute("""
            INSERT INTO curriculum_contributions
                (teacher_id, term_id, title, resource_type, file_reference)
            VALUES (%s,%s,%s,%s,%s)
        """, (body.teacher_id, body.term_id, body.title,
              body.resource_type, body.file_reference))
        new_id = cur.lastrowid

    return {"id": new_id, "message": "Contribution logged."}


@router.patch("/contributions/{contribution_id}/adoption")
def update_adoption(contribution_id: int, count: int):
    """Update the number of colleagues who have adopted this resource."""
    if count < 0:
        raise HTTPException(status_code=422, detail="count must be >= 0.")
    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM curriculum_contributions WHERE id=%s",
                    (contribution_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Contribution not found.")
        cur.execute("""
            UPDATE curriculum_contributions SET adoption_count=%s WHERE id=%s
        """, (count, contribution_id))
    return {"message": "Adoption count updated."}


# =============================================================================
# SUMMARY
# =============================================================================

@router.get("/summary/{term_id}")
def growth_summary(term_id: int):
    """
    Per-teacher professional growth summary for a term.
    Includes weighted PD hours, mentorship session count, contribution count.
    """
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                t.id                                        AS teacher_id,
                CONCAT(t.first_name,' ',t.last_name)        AS teacher_name,

                -- PD: sum of hours × type multiplier, verified only
                ROUND(SUM(
                    CASE WHEN pd.is_verified THEN pd.duration_hours * ptw.weight_multiplier
                    ELSE 0 END
                ), 2)                                       AS weighted_pd_hours,

                COUNT(DISTINCT pd.id)                       AS pd_events,

                -- Mentorship: fully confirmed sessions where teacher is mentor or mentee
                COUNT(DISTINCT CASE
                    WHEN ml.mentor_confirmed AND ml.mentee_confirmed THEN ml.id
                END)                                        AS mentorship_sessions,

                -- Contributions
                COUNT(DISTINCT cc.id)                       AS contributions,
                COALESCE(SUM(cc.adoption_count), 0)         AS total_adoptions

            FROM       teachers t
            LEFT JOIN  pd_logs pd
                ON     pd.teacher_id = t.id AND pd.term_id = %s
            LEFT JOIN  pd_type_weights ptw ON ptw.pd_type = pd.pd_type
            LEFT JOIN  mentorship_logs ml
                ON     (ml.mentor_id = t.id OR ml.mentee_id = t.id)
                AND    ml.term_id = %s
            LEFT JOIN  curriculum_contributions cc
                ON     cc.teacher_id = t.id AND cc.term_id = %s
            WHERE      t.is_active = TRUE
            GROUP BY   t.id, t.first_name, t.last_name
            ORDER BY   weighted_pd_hours DESC
        """, (term_id, term_id, term_id))

        return {"term_id": term_id, "summary": cur.fetchall()}
