"""
services/enforcement_engine.py — rule evaluation and action logging for the
Sabi enforcement system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from mysql.connector import Error as MySQLError

from database.connections import get_enterprise, get_sabi
from services.message_builder import MessageBuilder, MessageContext

logger = logging.getLogger(__name__)

RULE_KEYS = (
    "lesson_plan_submission",
    "scheme_of_work",
    "result_upload",
    "attendance_logging",
    "pd_target",
    "teacher_lateness",
    "absenteeism",
    "homework_giving",
)


@dataclass(slots=True)
class ViolationCandidate:
    rule_key: str
    teacher_id: int
    teacher_name: str
    term_id: int
    reference: str
    deadline: date | None = None
    metadata: dict[str, Any] | None = None


class EnforcementEngine:
    def __init__(self) -> None:
        self.builder = MessageBuilder()
        self.rule_checkers: dict[str, Callable[[dict[str, Any]], list[ViolationCandidate]]] = {
            "lesson_plan_submission": self._check_lesson_plan_submission,
            "scheme_of_work": self._check_scheme_of_work,
            "result_upload": self._check_result_upload,
            "attendance_logging": self._check_attendance_logging,
            "pd_target": self._check_pd_target,
            "teacher_lateness": self._check_teacher_lateness,
            "absenteeism": self._check_absenteeism,
            "homework_giving": self._check_homework_giving,
        }

    def run_all_rules(self) -> dict[str, Any]:
        with get_sabi() as (_, cur):
            term = self._get_current_term(cur)
            rules = self._get_active_rules(cur)
            actions: list[dict[str, Any]] = []
            for rule in rules:
                checker = self.rule_checkers.get(rule["rule_key"])
                if not checker:
                    logger.warning("No checker implemented for rule %s", rule["rule_key"])
                    continue
                for candidate in checker(term):
                    action = self._process_candidate(cur, rule, candidate, term)
                    if action:
                        actions.append(action)
            return {
                "date": date.today().isoformat(),
                "term_id": term["id"],
                "actions": actions,
            }

    def get_teacher_status(self, teacher_id: int) -> dict[str, Any]:
        with get_sabi() as (_, cur):
            cur.execute("SELECT id, first_name, last_name FROM teachers WHERE id = %s", (teacher_id,))
            teacher = cur.fetchone()
            if not teacher:
                raise ValueError("Teacher not found.")
            cur.execute(
                """
                SELECT el.*, er.rule_key, er.rule_name
                FROM   enforcement_log el
                JOIN   enforcement_rules er ON er.id = el.rule_id
                WHERE  el.teacher_id = %s
                ORDER  BY el.updated_at DESC, el.created_at DESC
                """,
                (teacher_id,),
            )
            return {
                "teacher": teacher,
                "records": cur.fetchall(),
            }

    def resolve_violation(self, log_id: int, resolved_by: str | None = None) -> dict[str, Any]:
        with get_sabi() as (_, cur):
            cur.execute("SELECT id, stage FROM enforcement_log WHERE id = %s", (log_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Enforcement log not found.")
            cur.execute(
                """
                UPDATE enforcement_log
                SET    stage = 'resolved', resolved_at = NOW(), updated_at = NOW()
                WHERE  id = %s
                """,
                (log_id,),
            )
            cur.execute(
                """
                INSERT INTO staff_escalations (enforcement_log_id, teacher_id, escalation_stage, recipient_role,
                                               message, created_at)
                SELECT id, teacher_id, 'resolved', 'system', %s, NOW()
                FROM enforcement_log
                WHERE id = %s
                """,
                (f"Violation resolved by {resolved_by or 'system'}", log_id),
            )
        return {"log_id": log_id, "stage": "resolved"}

    def confirm_query(self, log_id: int, confirmed_by: str) -> dict[str, Any]:
        with get_sabi() as (_, cur):
            cur.execute("SELECT id, query_draft FROM enforcement_log WHERE id = %s", (log_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Enforcement log not found.")
            cur.execute(
                """
                UPDATE enforcement_log
                SET    query_confirmed_by = %s,
                       query_served_at = NOW(),
                       updated_at = NOW()
                WHERE  id = %s
                """,
                (confirmed_by, log_id),
            )
        return {"log_id": log_id, "query_confirmed_by": confirmed_by}

    def _process_candidate(self, cur, rule: dict[str, Any], candidate: ViolationCandidate, term: dict[str, Any]) -> dict[str, Any] | None:
        cur.execute(
            """
            SELECT * FROM enforcement_log
            WHERE  rule_id = %s AND teacher_id = %s AND term_id = %s AND reference = %s
            ORDER  BY id DESC LIMIT 1
            """,
            (rule["id"], candidate.teacher_id, candidate.term_id, candidate.reference),
        )
        existing = cur.fetchone()
        stage_info = self._determine_stage(rule, candidate.deadline, existing)
        if not stage_info:
            return None

        cur.execute("SELECT telegram_id FROM teachers WHERE id = %s", (candidate.teacher_id,))
        teacher_row = cur.fetchone() or {}
        telegram_id = teacher_row.get("telegram_id")

        ctx = MessageContext(
            teacher_name=candidate.teacher_name,
            rule_name=rule["rule_name"],
            reference=candidate.reference,
            deadline=candidate.deadline.isoformat() if candidate.deadline else "today",
            policy_name=rule["rule_name"],
        )
        message = self._build_stage_message(stage_info["stage"], rule, ctx, candidate)

        if existing:
            self._update_log(cur, existing["id"], stage_info["stage"], message, rule, ctx)
            log_id = existing["id"]
        else:
            log_id = self._create_log(cur, rule, candidate, stage_info["stage"], message, ctx)

        escalation_id = None
        if stage_info["stage"] == "escalated_l1":
            escalation_id = self._create_escalation(cur, log_id, candidate.teacher_id, "principal", message)
        elif stage_info["stage"] == "escalated_l2":
            escalation_id = self._create_escalation(cur, log_id, candidate.teacher_id, "hr", message)

        return {
            "log_id": log_id,
            "rule_key": rule["rule_key"],
            "teacher_id": candidate.teacher_id,
            "teacher_name": candidate.teacher_name,
            "telegram_id": telegram_id,
            "reference": candidate.reference,
            "stage": stage_info["stage"],
            "message": message,
            "deadline": ctx.deadline,
            "escalation_id": escalation_id,
        }

    def _build_stage_message(self, stage: str, rule: dict[str, Any], ctx: MessageContext, candidate: ViolationCandidate) -> str:
        if stage == "reminder":
            return self.builder.gentle_reminder(ctx, rule.get("reminder_message"))
        if stage == "due_today":
            return self.builder.firm_warning(ctx, rule.get("due_today_message"))
        if stage == "defaulted":
            return self.builder.default_warning(ctx, rule.get("defaulted_message"))
        if stage == "escalated_l1":
            return self.builder.admin_escalation(ctx, rule.get("l1_report_template"))
        if stage == "escalated_l2":
            return self.builder.hr_query(ctx, rule.get("l2_query_template"))
        raise ValueError(f"Unsupported stage: {stage}")

    def _determine_stage(self, rule: dict[str, Any], deadline: date | None, existing: dict[str, Any] | None) -> dict[str, str] | None:
        today = date.today()
        if deadline:
            days_until = (deadline - today).days
            days_after = (today - deadline).days
        else:
            days_until = 0
            days_after = 0

        if existing and existing["stage"] == "resolved":
            return None

        if days_until > 0 and days_until <= int(rule.get("reminder_days_before") or 0):
            return {"stage": "reminder"}
        if days_until == 0:
            return {"stage": "due_today"}
        if days_after >= 1 and days_after < int(rule.get("escalate_l1_days_after") or 1):
            return {"stage": "defaulted"}
        if days_after >= int(rule.get("escalate_l1_days_after") or 1) and days_after < int(rule.get("escalate_l1_days_after") or 1) + int(rule.get("escalate_l2_days_after") or 1):
            return {"stage": "escalated_l1"}
        if days_after >= int(rule.get("escalate_l1_days_after") or 1) + int(rule.get("escalate_l2_days_after") or 1):
            return {"stage": "escalated_l2"}
        return None

    def _create_log(self, cur, rule: dict[str, Any], candidate: ViolationCandidate, stage: str, message: str, ctx: MessageContext) -> int:
        now_field = self._stage_column(stage)
        query_draft = message if stage == "escalated_l2" else None
        cur.execute(
            f"""
            INSERT INTO enforcement_log
                (rule_id, teacher_id, term_id, reference, stage, {now_field}, query_draft, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, NOW(), NOW())
            """,
            (rule["id"], candidate.teacher_id, candidate.term_id, candidate.reference, stage, query_draft),
        )
        return cur.lastrowid

    def _update_log(self, cur, log_id: int, stage: str, message: str, rule: dict[str, Any], ctx: MessageContext) -> None:
        now_field = self._stage_column(stage)
        if stage == "escalated_l2":
            cur.execute(
                f"UPDATE enforcement_log SET stage=%s, {now_field}=NOW(), query_draft=%s, updated_at=NOW() WHERE id=%s",
                (stage, message, log_id),
            )
        else:
            cur.execute(
                f"UPDATE enforcement_log SET stage=%s, {now_field}=NOW(), updated_at=NOW() WHERE id=%s",
                (stage, log_id),
            )

    def _create_escalation(self, cur, log_id: int, teacher_id: int, recipient_role: str, message: str) -> int:
        cur.execute(
            """
            INSERT INTO staff_escalations
                (enforcement_log_id, teacher_id, escalation_stage, recipient_role, message, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            """,
            (log_id, teacher_id, f"to_{recipient_role}", recipient_role, message),
        )
        return cur.lastrowid

    @staticmethod
    def _stage_column(stage: str) -> str:
        return {
            "reminder": "reminder_sent_at",
            "due_today": "due_today_sent_at",
            "defaulted": "defaulted_sent_at",
            "escalated_l1": "escalated_l1_at",
            "escalated_l2": "escalated_l2_at",
            "resolved": "resolved_at",
        }[stage]

    @staticmethod
    def _get_current_term(cur) -> dict[str, Any]:
        cur.execute("SELECT * FROM academic_terms WHERE is_current = TRUE LIMIT 1")
        term = cur.fetchone()
        if not term:
            raise ValueError("No current academic term configured.")
        return term

    @staticmethod
    def _get_active_rules(cur) -> list[dict[str, Any]]:
        cur.execute(
            "SELECT * FROM enforcement_rules WHERE is_active = TRUE ORDER BY id"
        )
        return cur.fetchall()

    def _check_lesson_plan_submission(self, term: dict[str, Any]) -> list[ViolationCandidate]:
        today = date.today()
        week = max(1, min(20, ((today - term["start_date"]).days // 7) + 1))
        deadline = term["start_date"] + timedelta(days=(week * 7) - 3)
        with get_sabi() as (_, cur):
            cur.execute(
                """
                SELECT t.id AS teacher_id,
                       CONCAT(t.first_name, ' ', t.last_name) AS teacher_name
                FROM teachers t
                LEFT JOIN lesson_plan_submissions lp
                  ON lp.teacher_id = t.id AND lp.term_id = %s AND lp.week_number = %s
                WHERE t.is_active = TRUE
                  AND (lp.id IS NULL OR lp.submitted_at IS NULL)
                """,
                (term["id"], week),
            )
            return [
                ViolationCandidate(
                    rule_key="lesson_plan_submission",
                    teacher_id=row["teacher_id"],
                    teacher_name=row["teacher_name"],
                    term_id=term["id"],
                    reference=f"Week {week} lesson plan",
                    deadline=deadline,
                )
                for row in cur.fetchall()
            ]

    def _check_scheme_of_work(self, term: dict[str, Any]) -> list[ViolationCandidate]:
        today = date.today()
        midpoint = term["start_date"] + ((term["end_date"] - term["start_date"]) / 2)
        threshold = 50 if today <= midpoint else 90
        with get_sabi() as (_, cur):
            cur.execute(
                """
                SELECT teacher_id,
                       CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                       class_name,
                       subject,
                       ROUND((topics_covered / NULLIF(total_topics, 0)) * 100, 1) AS pct
                FROM scheme_of_work sw
                JOIN teachers t ON t.id = sw.teacher_id
                WHERE sw.term_id = %s
                  AND (topics_covered / NULLIF(total_topics, 0)) * 100 < %s
                """,
                (term["id"], threshold),
            )
            return [
                ViolationCandidate(
                    rule_key="scheme_of_work",
                    teacher_id=row["teacher_id"],
                    teacher_name=row["teacher_name"],
                    term_id=term["id"],
                    reference=f"{row['subject']} {row['class_name']} scheme at {row['pct']}%",
                    deadline=term["end_date"],
                )
                for row in cur.fetchall()
            ]

    def _check_result_upload(self, term: dict[str, Any]) -> list[ViolationCandidate]:
        candidates: list[ViolationCandidate] = []
        today = date.today()
        start = term["start_date"]
        academic_session = int(term["academic_year"].split("/")[0])
        term_of_session  = int(term["term_number"])

        # Assessment deadlines — Friday of week 4, 7, 10 and Tuesday of week 13
        # Week N starts on start_date + (N-1)*7 days
        def week_friday(n: int) -> date:
            week_start = start + timedelta(days=(n - 1) * 7)
            # Friday = week_start + 4 days (Monday=0)
            return week_start + timedelta(days=(4 - week_start.weekday()) % 7 + (
                7 if week_start.weekday() > 4 else 0
            ))

        def week_tuesday(n: int) -> date:
            week_start = start + timedelta(days=(n - 1) * 7)
            return week_start + timedelta(days=(1 - week_start.weekday()) % 7 + (
                7 if week_start.weekday() > 1 else 0
            ))

        assessment_deadlines = {
            "Test 1":      week_friday(4),
            "Test 2":      week_friday(7),
            "Test 3":      week_friday(10),
            "Examination": week_tuesday(13),
        }

        # Only check assessments whose deadline has already passed
        due_assessments = [
            name for name, deadline in assessment_deadlines.items()
            if today >= deadline
        ]

        if not due_assessments:
            return []

        with get_sabi() as (_, cur):
            cur.execute(
                """
                SELECT ta.teacher_id,
                       CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                       ta.enterprise_class_id,
                       ta.enterprise_subject_id,
                       ta.class_name,
                       ta.subject_name
                FROM teacher_assignments ta
                JOIN teachers t ON t.id = ta.teacher_id
                WHERE ta.term_id = %s
                """,
                (term["id"],),
            )
            assignments = cur.fetchall()

        if not assignments:
            return []

        try:
            with get_enterprise() as (_, ent_cur):
                placeholders = ",".join(["%s"] * len(due_assessments))
                for row in assignments:
                    ent_cur.execute(
                        f"""
                        SELECT COUNT(*) AS score_count
                        FROM   tb_student_score_registers ssr
                        JOIN   tb_academic_assessments aa ON aa.id = ssr.assessment_id
                        WHERE  ssr.class_id         = %s
                        AND    ssr.subject_id       = %s
                        AND    ssr.academic_session = %s
                        AND    ssr.term_of_session  = %s
                        AND    aa.assessment_name IN ({placeholders})
                        """,
                        (
                            row["enterprise_class_id"],
                            row["enterprise_subject_id"],
                            academic_session,
                            term_of_session,
                            *due_assessments,
                        ),
                    )
                    score_row = ent_cur.fetchone()
                    if not score_row or not score_row["score_count"]:
                        due_str = ", ".join(due_assessments)
                        candidates.append(
                            ViolationCandidate(
                                rule_key="result_upload",
                                teacher_id=row["teacher_id"],
                                teacher_name=row["teacher_name"],
                                term_id=term["id"],
                                reference=f"{row['subject_name']} {row['class_name']} — {due_str}",
                                deadline=max(assessment_deadlines[a] for a in due_assessments),
                            )
                        )
        except (MySQLError, KeyError) as exc:
            logger.warning("Enterprise result-upload check failed: %s", exc)

        return candidates
    
    def _check_attendance_logging(self, term: dict[str, Any]) -> list[ViolationCandidate]:
        today = date.today()
        academic_session = int(term["academic_year"].split("/")[0])
        term_of_session  = int(term["term_number"])

        # Fetch only class teachers from enterprise DB
        # Class teacher = alternate_teacher_id on tb_academic_classes
        try:
            with get_enterprise() as (_, ent_cur):
                ent_cur.execute("""
                    SELECT DISTINCT alternate_teacher_id AS enterprise_id
                    FROM   tb_academic_classes
                    WHERE  academic_session     = %s
                    AND    alternate_teacher_id != ''
                    AND    alternate_teacher_id IS NOT NULL
                """, (academic_session,))
                class_teacher_enterprise_ids = [
                    row["enterprise_id"] for row in ent_cur.fetchall()
                ]
        except Exception as exc:
            logger.warning("Could not fetch class teachers from enterprise DB: %s", exc)
            return []

        if not class_teacher_enterprise_ids:
            return []

        placeholders = ",".join(["%s"] * len(class_teacher_enterprise_ids))

        if today.weekday() == 0:
            # Monday — check enterprise DB for week row existence
            try:
                with get_enterprise() as (_, ent_cur):
                    ent_cur.execute("""
                        SELECT DISTINCT class_id
                        FROM   tb_academic_class_morn_attendance
                        WHERE  academic_session     = %s
                        AND    term_of_session      = %s
                        AND    attendance_start_date <= %s
                        AND    attendance_end_date   >= %s
                    """, (academic_session, term_of_session, today, today))
                    logged_class_ids = {row["class_id"] for row in ent_cur.fetchall()}

                with get_enterprise() as (_, ent_cur):
                    ent_cur.execute(f"""
                        SELECT DISTINCT
                            ac.alternate_teacher_id AS enterprise_id
                        FROM   tb_academic_classes ac
                        WHERE  ac.academic_session     = %s
                        AND    ac.alternate_teacher_id != ''
                        AND    ac.alternate_teacher_id IS NOT NULL
                        AND    ac.id NOT IN ({','.join(['%s'] * len(logged_class_ids)) if logged_class_ids else 'NULL'})
                    """, (academic_session, *logged_class_ids) if logged_class_ids else (academic_session,))
                    defaulting_enterprise_ids = [
                        row["enterprise_id"] for row in ent_cur.fetchall()
                    ]
            except Exception as exc:
                logger.warning("Monday attendance check failed: %s", exc)
                return []

            if not defaulting_enterprise_ids:
                return []

            eid_placeholders = ",".join(["%s"] * len(defaulting_enterprise_ids))
            with get_sabi() as (_, cur):
                cur.execute(f"""
                    SELECT id AS teacher_id,
                           CONCAT(first_name, ' ', last_name) AS teacher_name
                    FROM   teachers
                    WHERE  enterprise_id IN ({eid_placeholders})
                    AND    is_active = TRUE
                """, tuple(defaulting_enterprise_ids))
                rows = cur.fetchall()

            return [
                ViolationCandidate(
                    rule_key="attendance_logging",
                    teacher_id=row["teacher_id"],
                    teacher_name=row["teacher_name"],
                    term_id=term["id"],
                    reference=f"Morning register for week of {today.isoformat()}",
                    deadline=today,
                )
                for row in rows
            ]

        # Tuesday to Friday — check attendance_confirmations in sabi_db
        session = "morning" if datetime.now().time() <= time(12, 0) else "noon"

        with get_sabi() as (_, cur):
            cur.execute(f"""
                SELECT t.id AS teacher_id,
                       CONCAT(t.first_name, ' ', t.last_name) AS teacher_name
                FROM   teachers t
                LEFT JOIN attendance_confirmations ac
                    ON  ac.teacher_id   = t.id
                    AND ac.confirm_date = %s
                    AND ac.session      = %s
                WHERE  t.enterprise_id IN ({placeholders})
                AND    t.is_active      = TRUE
                AND    ac.id IS NULL
            """, (today, session, *class_teacher_enterprise_ids))
            rows = cur.fetchall()

        return [
            ViolationCandidate(
                rule_key="attendance_logging",
                teacher_id=row["teacher_id"],
                teacher_name=row["teacher_name"],
                term_id=term["id"],
                reference=f"{session.title()} register confirmation for {today.isoformat()}",
                deadline=today,
            )
            for row in rows
        ]

    def _check_pd_target(self, term: dict[str, Any]) -> list[ViolationCandidate]:
        today = date.today()
        week = max(1, ((today - term["start_date"]).days // 7) + 1)
        if week < 8:
            return []
        with get_sabi() as (_, cur):
            cur.execute(
                """
                SELECT t.id AS teacher_id,
                       CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                       COALESCE(SUM(pd.duration_hours * ptw.weight_multiplier), 0) AS weighted_hours
                FROM teachers t
                LEFT JOIN pd_logs pd
                  ON pd.teacher_id = t.id AND pd.term_id = %s AND pd.is_verified = TRUE
                LEFT JOIN pd_type_weights ptw
                  ON ptw.pd_type = pd.pd_type
                WHERE t.is_active = TRUE
                GROUP BY t.id, t.first_name, t.last_name
                HAVING weighted_hours < 8
                """,
                (term["id"],),
            )
            rows = cur.fetchall()
        return [
            ViolationCandidate(
                rule_key="pd_target",
                teacher_id=row["teacher_id"],
                teacher_name=row["teacher_name"],
                term_id=term["id"],
                reference=f"PD target at {row['weighted_hours']} weighted hours",
                deadline=term["end_date"],
            )
            for row in rows
        ]

    def _check_teacher_lateness(self, term: dict[str, Any]) -> list[ViolationCandidate]:
        since = date.today() - timedelta(days=14)
        with get_sabi() as (_, cur):
            cur.execute(
                """
                SELECT teacher_id,
                       CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                       COUNT(*) AS late_count
                FROM teacher_attendance ta
                JOIN teachers t ON t.id = ta.teacher_id
                WHERE ta.term_id = %s
                  AND ta.log_date >= %s
                  AND ta.status = 'late'
                GROUP BY teacher_id, t.first_name, t.last_name
                HAVING late_count >= 3
                """,
                (term["id"], since),
            )
            rows = cur.fetchall()
        return [
            ViolationCandidate(
                rule_key="teacher_lateness",
                teacher_id=row["teacher_id"],
                teacher_name=row["teacher_name"],
                term_id=term["id"],
                reference=f"{row['late_count']} late arrivals in rolling window",
                deadline=date.today(),
            )
            for row in rows
        ]

    def _check_absenteeism(self, term: dict[str, Any]) -> list[ViolationCandidate]:
        with get_sabi() as (_, cur):
            cur.execute(
                """
                SELECT DISTINCT ta1.teacher_id,
                       CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                       ta1.log_date AS first_absence,
                       ta2.log_date AS second_absence
                FROM teacher_attendance ta1
                JOIN teacher_attendance ta2
                  ON ta2.teacher_id = ta1.teacher_id
                 AND ta2.log_date = DATE_ADD(ta1.log_date, INTERVAL 1 DAY)
                JOIN teachers t ON t.id = ta1.teacher_id
                WHERE ta1.term_id = %s
                  AND ta2.term_id = %s
                  AND ta1.status = 'absent'
                  AND ta2.status = 'absent'
                """,
                (term["id"], term["id"]),
            )
            rows = cur.fetchall()
        return [
            ViolationCandidate(
                rule_key="absenteeism",
                teacher_id=row["teacher_id"],
                teacher_name=row["teacher_name"],
                term_id=term["id"],
                reference=f"Unapproved absence on {row['first_absence']} and {row['second_absence']}",
                deadline=date.today(),
            )
            for row in rows
        ]

    def _check_homework_giving(self, term: dict[str, Any]) -> list[ViolationCandidate]:
        today = date.today()
        week = max(1, min(20, ((today - term["start_date"]).days // 7) + 1))
        deadline = term["start_date"] + timedelta(days=(week * 7) - 3)
        with get_sabi() as (_, cur):
            cur.execute(
                """
                SELECT ta.teacher_id,
                       CONCAT(t.first_name, ' ', t.last_name) AS teacher_name,
                       ta.class_name,
                       ta.subject_name
                FROM teacher_assignments ta
                JOIN teachers t ON t.id = ta.teacher_id
                LEFT JOIN homework_logs hl
                  ON hl.teacher_id = ta.teacher_id
                 AND hl.term_id = ta.term_id
                 AND hl.week_number = %s
                 AND hl.class_name = ta.class_name
                 AND hl.subject = ta.subject_name
                WHERE ta.term_id = %s
                  AND hl.id IS NULL
                """,
                (week, term["id"]),
            )
            rows = cur.fetchall()
        return [
            ViolationCandidate(
                rule_key="homework_giving",
                teacher_id=row["teacher_id"],
                teacher_name=row["teacher_name"],
                term_id=term["id"],
                reference=f"Week {week} homework for {row['subject_name']} {row['class_name']}",
                deadline=deadline,
            )
            for row in rows
        ]
