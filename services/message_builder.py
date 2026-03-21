"""
services/message_builder.py — helper for enforcement-stage message generation.
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass(slots=True)
class MessageContext:
    teacher_name: str
    rule_name: str
    reference: str
    deadline: str | None = None
    policy_name: str | None = None
    principal_name: str | None = None


class MessageBuilder:
    """Build consistent teacher/admin enforcement messages."""

    @staticmethod
    def _safe_format(template: str | None, **values: Any) -> str:
        if template:
            return template.format(**values)
        return ""

    def gentle_reminder(self, ctx: MessageContext, template: str | None = None) -> str:
        default = (
            "Hello {teacher_name}. This is a gentle reminder that {rule_name} "
            "for {reference} is due {deadline}. Please complete it before the cutoff."
        )
        return self._safe_format(template or default, **asdict(ctx))

    def firm_warning(self, ctx: MessageContext, template: str | None = None) -> str:
        default = (
            "Warning: {teacher_name}, you are not yet compliant with the {policy_name} "
            "requirement for {reference}. This must be resolved by {deadline}."
        )
        values = {**asdict(ctx), "policy_name": ctx.policy_name or ctx.rule_name}
        return self._safe_format(template or default, **values)

    def default_warning(self, ctx: MessageContext, template: str | None = None) -> str:
        default = (
            "Default notice: {teacher_name}, you have missed the deadline for {rule_name} "
            "({reference}). This issue has now entered the enforcement process."
        )
        return self._safe_format(template or default, **asdict(ctx))

    def admin_escalation(self, ctx: MessageContext, template: str | None = None) -> str:
        default = (
            "Escalation alert for {teacher_name}: non-compliance with {rule_name} "
            "for {reference}. Please review and take administrative action."
        )
        return self._safe_format(template or default, **asdict(ctx))

    def principal_report(self, rule_name: str, defaulters: list[Dict[str, Any]], template: str | None = None) -> str:
        names = ", ".join(
            f"{row['teacher_name']} ({row.get('reference', 'no reference')})" for row in defaulters
        ) or "None"
        default = f"{rule_name} default report: {names}."
        if template:
            return template.format(rule_name=rule_name, defaulters=names)
        return default

    def hr_query(self, ctx: MessageContext, template: str | None = None) -> str:
        default = (
            "Formal query draft for {teacher_name}: failure to comply with {rule_name} "
            "for {reference}. Kindly provide written explanation within 24 hours."
        )
        return self._safe_format(template or default, **asdict(ctx))
