"""
routes/teacher_attendance.py — Daily teacher attendance and punctuality.

Endpoints:
    POST /attendance/log               log one teacher's attendance for a day
    POST /attendance/log/bulk          log multiple teachers in one call (morning register)
    GET  /attendance/{teacher_id}      attendance records for one teacher
    GET  /attendance/summary/{term_id} school-wide summary for a term
    GET  /attendance/today             who is present, absent, or late today
"""

from datetime import date, time, datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi
import config

router = APIRouter()


# =============================================================================
# MODELS
# =============================================================================

VALID_STATUSES = ("present", "absent", "late", "approved_leave", "public_holiday")

class AttendanceEntry(BaseModel):
    teacher_id:    int
    log_date:      date
    status:        str
    arrival_time:  Optional[time] = None   # required when status = "present" or "late"
    notes:         Optional[str]  = None
    logged_by:     Optional[str]  = None

    def validate_status(self):
        if self.status not in VALID_STATUSES:
            raise ValueError(f"status must be one of: {VALID_STATUSES}")

class BulkAttendanceRequest(BaseModel):
    entries:  List[AttendanceEntry]
    term_id:  int


class SingleAttendanceRequest(AttendanceEntry):
    term_id:  int


# =============================================================================
# HELPERS
# =============================================================================

def _compute_minutes_late(arrival: Optional[time], expected_str: str) -> Optional[int]:
    """Return minutes late, or None if arrival is missing."""
    if not arrival:
        return None
    h, m = map(int, expected_str.split(":"))
    expected = time(h, m)
    if arrival <= expected:
        return 0
    delta = (datetime.combine(date.today(), arrival)
             - datetime.combine(date.today(), expected))
    return int(delta.total_seconds() // 60)


def _require_current_teacher(cur, teacher_id: int):
    cur.execute("SELECT id FROM teachers WHERE id = %s AND is_active = TRUE", (teacher_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail=f"Active teacher {teacher_id} not found.")


def _require_term(cur, term_id: int):
    cur.execute("SELECT id FROM academic_terms WHERE id = %s", (term_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail=f"Term {term_id} not found.")


def _upsert_entry(cur, term_id: int, entry: AttendanceEntry):
    """Insert or update a single attendance record."""
    if entry.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status '{entry.status}'. Must be one of {VALID_STATUSES}."
        )
    minutes_late = _compute_minutes_late(entry.arrival_time, config.SCHOOL_START_TIME)

    cur.execute("""
        INSERT INTO teacher_attendance
            (teacher_id, term_id, log_date, status, arrival_time,
             expected_time, minutes_late, notes, logged_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            status        = VALUES(status),
            arrival_time  = VALUES(arrival_time),
            minutes_late  = VALUES(minutes_late),
            notes         = VALUES(notes),
            logged_by     = VALUES(logged_by)
    """, (
        entry.teacher_id, term_id, entry.log_date, entry.status,
        entry.arrival_time, config.SCHOOL_START_TIME,
        minutes_late, entry.notes, entry.logged_by
    ))


# =============================================================================
# ROUTES
# =============================================================================

@router.post("/log", status_code=status.HTTP_201_CREATED)
def log_single(body: SingleAttendanceRequest):
    """Log attendance for one teacher on one day."""
    with get_sabi() as (_, cur):
        _require_current_teacher(cur, body.teacher_id)
        _require_term(cur, body.term_id)
        _upsert_entry(cur, body.term_id, body)
    return {"message": "Attendance logged."}


@router.post("/log/bulk", status_code=status.HTTP_201_CREATED)
def log_bulk(body: BulkAttendanceRequest):
    """
    Log attendance for multiple teachers in a single call.
    Designed for the morning register: the admin sends one message with
    all present/absent/late statuses and OpenClaw posts them here.
    Any entry that fails validation is reported in 'errors' but does not
    block the rest of the batch.
    """
    if not body.entries:
        raise HTTPException(status_code=422, detail="entries list is empty.")

    saved  = 0
    errors = []

    with get_sabi() as (_, cur):
        _require_term(cur, body.term_id)
        for entry in body.entries:
            try:
                _require_current_teacher(cur, entry.teacher_id)
                _upsert_entry(cur, body.term_id, entry)
                saved += 1
            except HTTPException as e:
                errors.append({"teacher_id": entry.teacher_id, "error": e.detail})
            except Exception as e:
                errors.append({"teacher_id": entry.teacher_id, "error": str(e)})

    return {"saved": saved, "errors": errors}


@router.get("/today")
def get_today():
    """
    Return attendance status for all active teachers for today.
    Records not yet logged appear as status: 'not_logged'.
    """
    today = date.today()
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                t.id AS teacher_id,
                CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                t.subject_primary,
                COALESCE(ta.status, 'not_logged')  AS status,
                ta.arrival_time,
                ta.minutes_late,
                ta.notes
            FROM       teachers t
            LEFT JOIN  teacher_attendance ta
                ON     ta.teacher_id = t.id AND ta.log_date = %s
            WHERE      t.is_active = TRUE
            ORDER BY   t.last_name, t.first_name
        """, (today,))
        return {"date": today, "records": cur.fetchall()}


@router.get("/{teacher_id}")
def get_teacher_attendance(
    teacher_id: int,
    term_id:    Optional[int] = None
):
    """
    Return attendance records for one teacher.
    Optionally filter by term_id.
    """
    with get_sabi() as (_, cur):
        _require_current_teacher(cur, teacher_id)

        if term_id:
            cur.execute("""
                SELECT ta.*, at.term_name
                FROM   teacher_attendance ta
                JOIN   academic_terms at ON at.id = ta.term_id
                WHERE  ta.teacher_id = %s AND ta.term_id = %s
                ORDER  BY ta.log_date DESC
            """, (teacher_id, term_id))
        else:
            cur.execute("""
                SELECT ta.*, at.term_name
                FROM   teacher_attendance ta
                JOIN   academic_terms at ON at.id = ta.term_id
                WHERE  ta.teacher_id = %s
                ORDER  BY ta.log_date DESC
            """, (teacher_id,))

        records = cur.fetchall()

    return {"teacher_id": teacher_id, "records": records}


@router.get("/summary/{term_id}")
def get_term_summary(term_id: int):
    """
    School-wide attendance summary for a term.
    Returns per-teacher stats: days present, absent, late, approved leave,
    attendance rate, and average minutes late.
    """
    with get_sabi() as (_, cur):
        _require_term(cur, term_id)
        cur.execute("""
            SELECT
                t.id                                           AS teacher_id,
                CONCAT(t.first_name, ' ', t.last_name)        AS teacher_name,
                t.subject_primary,
                COUNT(ta.id)                                   AS days_logged,
                SUM(ta.status = 'present')                     AS days_present,
                SUM(ta.status = 'absent')                      AS days_absent,
                SUM(ta.status = 'late')                        AS days_late,
                SUM(ta.status = 'approved_leave')              AS days_approved_leave,
                ROUND(
                    100.0 * SUM(ta.status IN ('present','late'))
                    / NULLIF(SUM(ta.status NOT IN ('public_holiday','approved_leave')), 0),
                2)                                             AS attendance_pct,
                ROUND(AVG(CASE WHEN ta.minutes_late > 0
                               THEN ta.minutes_late END), 1)   AS avg_minutes_late
            FROM       teachers t
            LEFT JOIN  teacher_attendance ta
                ON     ta.teacher_id = t.id AND ta.term_id = %s
            WHERE      t.is_active = TRUE
            GROUP BY   t.id, t.first_name, t.last_name, t.subject_primary
            ORDER BY   attendance_pct DESC
        """, (term_id,))
        return {"term_id": term_id, "summary": cur.fetchall()}
