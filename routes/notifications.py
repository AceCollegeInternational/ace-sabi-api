"""
routes/notifications.py — Parent notification log.

Tracks every parent/guardian contact attempt made by teachers.
Provides a complete communication history per student.

A notification can be linked to a pastoral log (the concern that
triggered the call) or logged independently.

Endpoints:
    POST /notifications                           log a contact attempt
    GET  /notifications/student/{student_id}      all notifications for
                                                  a student
    GET  /notifications/teacher/{teacher_id}      all notifications made
                                                  by a teacher
    PATCH /notifications/{id}/outcome             update outcome after
                                                  a callback is received
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi, get_enterprise

router = APIRouter()

VALID_CHANNELS = ("phone_call", "whatsapp", "sms", "in_person", "other")
VALID_OUTCOMES = (
    "reached", "not_reached", "left_message",
    "wrong_number", "callback_promised"
)


# =============================================================================
# MODELS
# =============================================================================

class NotificationCreate(BaseModel):
    teacher_id:             int
    term_id:                int
    enterprise_student_id:  str
    contact_name:           str
    contact_phone:          str
    contact_relationship:   Optional[str]      = None
    channel:                str                = "phone_call"
    subject:                str
    pastoral_log_id:        Optional[int]      = None
    outcome:                str                = "reached"
    notes:                  Optional[str]      = None
    notified_at:            Optional[datetime] = None


class OutcomeUpdate(BaseModel):
    outcome: str
    notes:   Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================

def _validate_choices(channel: str, outcome: str):
    if channel not in VALID_CHANNELS:
        raise HTTPException(
            status_code=422,
            detail=f"channel must be one of: {VALID_CHANNELS}"
        )
    if outcome not in VALID_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"outcome must be one of: {VALID_OUTCOMES}"
        )


def _enrich_with_student_name(records: list) -> list:
    """
    Fetch student names from enterprise DB and attach to notification records.
    Groups records by student_id to minimise DB round trips.
    """
    if not records:
        return records

    student_ids = list({r["enterprise_student_id"] for r in records})

    with get_enterprise() as (_, ent_cur):
        placeholders = ",".join(["%s"] * len(student_ids))
        ent_cur.execute(f"""
            SELECT
                id,
                CONCAT(
                    last_name, ' ', first_name,
                    CASE WHEN other_name != ''
                         THEN CONCAT(' ', other_name)
                         ELSE '' END
                ) AS student_name
            FROM tb_student_registrations
            WHERE id IN ({placeholders})
        """, student_ids)
        name_map = {row["id"]: row["student_name"] for row in ent_cur.fetchall()}

    for r in records:
        r["student_name"] = name_map.get(r["enterprise_student_id"])

    return records


# =============================================================================
# ROUTES
# =============================================================================

@router.post("", status_code=status.HTTP_201_CREATED)
def log_notification(body: NotificationCreate):
    """
    Log a parent/guardian contact attempt.

    channel options:
      phone_call, whatsapp, sms, in_person, other

    outcome options:
      reached          — contact answered, conversation happened
      not_reached      — called but no answer
      left_message     — left voicemail or message
      wrong_number     — number is incorrect
      callback_promised — contact promised to call back

    pastoral_log_id is optional — link it to the pastoral concern
    that triggered the call for a complete audit trail.

    notified_at defaults to now if not provided — supply it if logging
    a call that was made earlier in the day.
    """
    _validate_choices(body.channel, body.outcome)

    notified_at = body.notified_at or datetime.now()

    with get_sabi() as (_, cur):
        # Validate teacher
        cur.execute(
            "SELECT id FROM teachers WHERE id = %s AND is_active = TRUE",
            (body.teacher_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Active teacher not found.")

        # Validate term
        cur.execute(
            "SELECT id FROM academic_terms WHERE id = %s",
            (body.term_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Term not found.")

        # Validate pastoral log if provided
        if body.pastoral_log_id:
            cur.execute(
                "SELECT id FROM pastoral_logs WHERE id = %s",
                (body.pastoral_log_id,)
            )
            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail=f"Pastoral log {body.pastoral_log_id} not found."
                )

        cur.execute("""
            INSERT INTO parent_notifications (
                teacher_id, term_id, enterprise_student_id,
                contact_name, contact_phone, contact_relationship,
                channel, subject, pastoral_log_id,
                outcome, notes, notified_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            body.teacher_id, body.term_id, body.enterprise_student_id,
            body.contact_name, body.contact_phone, body.contact_relationship,
            body.channel, body.subject, body.pastoral_log_id,
            body.outcome, body.notes, notified_at
        ))
        new_id = cur.lastrowid

        # Auto-update the linked pastoral log when parent is successfully reached.
        # Any outcome other than not_reached or wrong_number counts as contact made.
        pastoral_log_updated = False
        if body.pastoral_log_id and body.outcome in (
            "reached", "left_message", "callback_promised"
        ):
            cur.execute("""
                UPDATE pastoral_logs
                SET    parent_notified     = TRUE,
                       parent_notified_at  = NOW()
                WHERE  id = %s
                AND    parent_notified = FALSE
            """, (body.pastoral_log_id,))
            pastoral_log_updated = cur.rowcount > 0

    response = {
        "id":      new_id,
        "message": "Parent notification logged."
    }
    if body.pastoral_log_id:
        response["pastoral_log_updated"] = pastoral_log_updated
        if pastoral_log_updated:
            response["message"] = (
                "Parent notification logged and pastoral log marked as parent notified."
            )

    return response


@router.get("/student/{student_id}")
def get_student_notifications(
    student_id: str,
    term_id:    Optional[int] = None,
):
    """
    Full notification history for a student.

    Returns all parent contact attempts for this student across all
    teachers, with teacher names, contact details, outcomes, and notes.
    Most recent first.

    Optionally filter by term_id.
    """
    with get_sabi() as (_, cur):
        if term_id:
            cur.execute("""
                SELECT
                    pn.*,
                    CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                    at.term_name
                FROM   parent_notifications pn
                JOIN   teachers t        ON t.id  = pn.teacher_id
                JOIN   academic_terms at ON at.id = pn.term_id
                WHERE  pn.enterprise_student_id = %s
                AND    pn.term_id = %s
                ORDER  BY pn.notified_at DESC
            """, (student_id, term_id))
        else:
            cur.execute("""
                SELECT
                    pn.*,
                    CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                    at.term_name
                FROM   parent_notifications pn
                JOIN   teachers t        ON t.id  = pn.teacher_id
                JOIN   academic_terms at ON at.id = pn.term_id
                WHERE  pn.enterprise_student_id = %s
                ORDER  BY pn.notified_at DESC
            """, (student_id,))
        records = cur.fetchall()

    if not records:
        return {
            "student_id": student_id,
            "count":      0,
            "records":    [],
        }

    # Attach student name from enterprise DB
    records = _enrich_with_student_name(records)

    return {
        "student_id":   student_id,
        "student_name": records[0].get("student_name") if records else None,
        "count":        len(records),
        "records":      records,
    }


@router.get("/teacher/{teacher_id}")
def get_teacher_notifications(
    teacher_id: int,
    term_id:    Optional[int] = None,
):
    """
    All parent contact attempts made by a specific teacher.
    Optionally filter by term_id.
    Most recent first.
    """
    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT id FROM teachers WHERE id = %s", (teacher_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found.")

        if term_id:
            cur.execute("""
                SELECT
                    pn.*,
                    at.term_name
                FROM   parent_notifications pn
                JOIN   academic_terms at ON at.id = pn.term_id
                WHERE  pn.teacher_id = %s
                AND    pn.term_id    = %s
                ORDER  BY pn.notified_at DESC
            """, (teacher_id, term_id))
        else:
            cur.execute("""
                SELECT
                    pn.*,
                    at.term_name
                FROM   parent_notifications pn
                JOIN   academic_terms at ON at.id = pn.term_id
                WHERE  pn.teacher_id = %s
                ORDER  BY pn.notified_at DESC
            """, (teacher_id,))
        records = cur.fetchall()

    records = _enrich_with_student_name(records)

    return {
        "teacher_id": teacher_id,
        "count":      len(records),
        "records":    records,
    }


@router.patch("/{notification_id}/outcome")
def update_outcome(notification_id: int, body: OutcomeUpdate):
    """
    Update the outcome of a notification after the fact.

    Use this when:
      - A teacher logged 'callback_promised' and the parent later called back
      - An initial 'not_reached' was followed by a successful call
      - Additional notes need to be added after the conversation

    Only outcome and notes can be updated — the original contact
    details and timestamp are preserved for audit purposes.
    """
    if body.outcome not in VALID_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"outcome must be one of: {VALID_OUTCOMES}"
        )

    with get_sabi() as (_, cur):
        cur.execute(
            "SELECT id FROM parent_notifications WHERE id = %s",
            (notification_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Notification not found.")

        cur.execute("""
            UPDATE parent_notifications
            SET    outcome = %s,
                   notes   = CASE WHEN %s IS NOT NULL THEN %s ELSE notes END
            WHERE  id = %s
        """, (body.outcome, body.notes, body.notes, notification_id))

    return {"message": "Outcome updated."}
