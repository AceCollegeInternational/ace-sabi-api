# Sabi API Progress Delta vs Earlier Product Gap Review

Date: 2026-04-25

## Executive delta

The earlier gap review is now materially out of date for the enforcement expansion scope.

Implemented since that review snapshot (now present in the codebase):
- `routes/staff_roles.py`
- `routes/teacher_assignments.py`
- `routes/attendance_confirmations.py`
- `routes/scheme_of_work.py`
- `routes/homework_logs.py`

All five route groups are imported and registered in `main.py` with their own URL prefixes.

## What has progressed beyond the old review

### 1) Missing enforcement route groups from the old review are now implemented

The old review claimed these endpoint families were not implemented. They are now live:

- Staff roles
  - `POST /staff-roles`
  - `GET /staff-roles`
  - `DELETE /staff-roles/{role_id}`
- Teacher assignments
  - `POST /teacher-assignments/sync`
  - `GET /teacher-assignments/teacher/{teacher_id}`
  - `GET /teacher-assignments/term/{term_id}`
- Attendance confirmations
  - `POST /attendance-confirmations/confirm`
  - `GET /attendance-confirmations/pending`
  - `GET /attendance-confirmations/teacher/{teacher_id}`
- Scheme of work
  - `POST /scheme-of-work`
  - `PATCH /scheme-of-work/{scheme_id}/progress`
  - `GET /scheme-of-work/teacher/{teacher_id}`
  - `GET /scheme-of-work/term/{term_id}`
- Homework logs
  - `POST /homework-logs`
  - `GET /homework-logs/teacher/{teacher_id}`
  - `GET /homework-logs/class/{class_name}`

### 2) Database support for those modules is now present

The enforcement migration includes first-class tables for:
- `staff_roles`
- `teacher_assignments`
- `attendance_confirmations`
- `scheme_of_work`
- `homework_logs`

It also includes enforcement artifacts the old review discussed only as planned:
- `enforcement_rules`
- `enforcement_log`
- `staff_escalations`

### 3) API surface has expanded

Compared to the old snapshot, `main.py` now includes and mounts five additional routers for the enforcement plan expansion:
- `/staff-roles`
- `/teacher-assignments`
- `/attendance-confirmations`
- `/scheme-of-work`
- `/homework-logs`

## Remaining major product gaps (still true)

The broader "full product vision" gaps in the old review are still largely valid:

- No dedicated route modules for anonymous staff voice, cover coordination, fees/budget, exams, inventory, maintenance, governance reporting, or reputation monitoring.
- No dedicated service modules for fee/budget/exam/inventory/maintenance/governance/reputation or anonymity/cover workflows.
- No explicit scheduler/cron job artifacts in repo for reminder/escalation automation families named in the review.

## Progress scorecard (beyond old review state)

- Enforcement submodule route groups previously listed as missing: **5/5 implemented (100%)**.
- Enforcement data model additions for those groups: **implemented**.
- Broader non-enforcement product families (finance, ops, leadership intelligence, reputation): **still mostly unimplemented**.

## Bottom line

Development has moved **significantly beyond** the old review in the enforcement-plan area, but only **modestly beyond** it for the larger cross-domain product vision. The codebase now reflects a completed enforcement expansion slice, while most Tier-2/Tier-3 strategic modules from the product brief remain open.
