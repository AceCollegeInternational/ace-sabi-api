"""
routes/enforcement.py — enforcement rule management and daily check endpoints.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from database.connections import get_sabi
from services.enforcement_engine import EnforcementEngine, RULE_KEYS

router = APIRouter()
engine = EnforcementEngine()


class ResolveRequest(BaseModel):
    resolved_by: Optional[str] = None


class QueryConfirmRequest(BaseModel):
    confirmed_by: str


class RuleUpdate(BaseModel):
    is_active: Optional[bool] = None
    reminder_days_before: Optional[int] = None
    escalate_l1_days_after: Optional[int] = None
    escalate_l2_days_after: Optional[int] = None
    reminder_message: Optional[str] = None
    due_today_message: Optional[str] = None
    defaulted_message: Optional[str] = None
    l1_report_template: Optional[str] = None
    l2_query_template: Optional[str] = None


@router.get("/check")
def run_enforcement_check():
    try:
        return engine.run_all_rules()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/status/{teacher_id}")
def get_teacher_enforcement_status(teacher_id: int):
    try:
        return engine.get_teacher_status(teacher_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/resolve/{log_id}")
def resolve_enforcement(log_id: int, body: ResolveRequest):
    try:
        return engine.resolve_violation(log_id, body.resolved_by)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/query/confirm/{log_id}")
def confirm_query(log_id: int, body: QueryConfirmRequest):
    if not body.confirmed_by.strip():
        raise HTTPException(status_code=422, detail="confirmed_by is required.")
    try:
        return engine.confirm_query(log_id, body.confirmed_by.strip())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/rules")
def list_rules():
    with get_sabi() as (_, cur):
        cur.execute("SELECT * FROM enforcement_rules ORDER BY id")
        return {"rules": cur.fetchall()}


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleUpdate):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="At least one field must be provided.")

    with get_sabi() as (_, cur):
        cur.execute("SELECT id FROM enforcement_rules WHERE id = %s", (rule_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Rule not found.")

        fields = ", ".join(f"{key} = %s" for key in updates)
        values = list(updates.values()) + [rule_id]
        cur.execute(
            f"UPDATE enforcement_rules SET {fields}, updated_at = NOW() WHERE id = %s",
            values,
        )
        cur.execute("SELECT * FROM enforcement_rules WHERE id = %s", (rule_id,))
        return cur.fetchone()


@router.get("/health")
def enforcement_health():
    return {
        "status": "ok",
        "supported_rules": list(RULE_KEYS),
        "date": date.today().isoformat(),
    }
