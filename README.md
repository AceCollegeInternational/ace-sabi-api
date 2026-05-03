# ace-sabi-api

**School Intelligence API for ACE College International**

A production FastAPI system that federates three independent
institutional databases into a single intelligence layer —
delivering actionable school operations data to authorised
staff through a RBAC-gated Telegram bot interface.

**Live at:** `sabi.acecollege.com.ng`
**Telegram interface:** `@SabiAceOpsBot`
**Licence:** AGPL-3.0

---

## The Problem This Solves

Commercial school management systems produce static reports.
They do not deliver actionable intelligence into the
communication channels Nigerian school staff actually use.

A Vice Principal checking a dashboard at 9pm is less useful
than a message that says: *"3 students in SSS2 have missed
4 consecutive days — here are their parent numbers."*

Sabi delivers school intelligence into Telegram — the channel
ACE College staff already use — with role-based access control
so every staff member sees exactly what their role authorises,
and nothing more.

---

## What It Does

### Student Intelligence
- **At-risk detection** — cross-references attendance streaks,
  score trends, and Moodle LMS inactivity to identify students
  requiring intervention before issues escalate
- **Student profiles** — aggregates ERP and LMS data into a
  single intelligence card per student
- **Student search** — find any student by name, class, or ID

### Attendance Pipeline
- **Gap detection** — flags students with consecutive absences
  above a configurable threshold
- **Daily logging compliance** — validates that class teachers
  have submitted attendance for the day
- **Bulk attendance ingestion** — accepts class-level attendance
  records via API

### Parent Notification Workflow
- **Automated alerts** — absence notifications, score drop
  warnings, fee reminders, general announcements
- **Notification audit log** — every parent communication
  is timestamped, attributed to the issuing staff member,
  and permanently logged

### Staff Accountability Engine
- **8-rule enforcement system** with escalation logic
- **HOD notifications** — automatic escalation when
  department-level thresholds are breached
- **Staff summary** — principal-level overview of
  task assignment and completion status

### Academic Scores
- **Score ingestion** — accepts assessment results per
  student per subject
- **Trend analysis** — identifies students with declining
  performance across consecutive assessments
- **Subject performance** — class-level and student-level
  score summaries

---

## Architecture

Sabi federates three independent MySQL databases at query time:
one_arps_aci   School ERP — students, attendance, scores,
staff records, fee data (13+ years of data)
sabi_db        Sabi metadata — bot sessions, notification
logs, staff roles, enforcement records,
audit trail
studypal       Moodle LMS — course enrolment, quiz scores,
assignment submissions, activity logs

No data is migrated or duplicated. Each source system
continues to evolve independently. Sabi is the integration
layer — not a replacement for any of them.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system
diagram, data flow documentation, module structure, and the
rationale behind every major technical decision.

---

## Tech Stack

| Component | Technology |
|---|---|
| API Framework | FastAPI (Python 3.11) |
| ASGI Server | uvicorn |
| Databases | MySQL 8.0 × 3 schemas |
| Telegram Interface | OpenClaw bridge |
| Reverse Proxy | Caddy (automatic HTTPS) |
| Hosting | Contabo VPS · Ubuntu 24.04 |
| Process Management | systemd |
| External Integrations | Google Sheets API (teacher assignments) |

---

## Telegram Commands

Commands are issued via `@SabiAceOpsBot`. Access is gated
by staff role — each command returns only data the
requesting staff member is authorised to see.
!students at-risk          At-risk student list (VP+ access)
!student [name/id]         Full student intelligence card
!attendance today          Today's attendance summary by class
!attendance gaps           Students with consecutive absences
!notify parent [id] [msg]  Send formatted message to parent
!staff summary             Staff accountability report (Principal)
!fees outstanding          Outstanding fee ledger (Bursar)
!scores [class] [subject]  Class score summary

---

## Staff Role Hierarchy
Principal → Vice Principal → Head of Department
→ Class Teacher → Bursar

Each role has a defined scope of endpoint access.
Role assignments are managed in `sabi_db.staff_roles`.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the full
access matrix.

---

## API Documentation

Interactive API documentation (Swagger UI) is available
at the live deployment:
https://sabi.acecollege.com.ng/docs

The OpenAPI specification is also exported to
`docs/openapi.json` in this repository.

---

## Deployment

### Requirements
- Python 3.11+
- MySQL 8.0 (three schemas — see Environment Variables)
- Caddy (reverse proxy)
- OpenClaw (Telegram bridge)
- Ubuntu 20.04+ (recommended)

### Setup

```bash
# Clone the repository
git clone https://github.com/AceCollegeInternational/ace-sabi-api
cd ace-sabi-api

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your database credentials and API keys
nano .env

# Run the API
uvicorn main:app --host 0.0.0.0 --port 8000

# For production — use systemd service
# See docs/deployment/systemd.md
```

### Database Setup

Sabi requires read access to an existing school ERP
(`one_arps_aci`) and Moodle LMS (`studypal`) database.
It creates and manages its own metadata schema (`sabi_db`).

```bash
# Create the sabi_db schema
mysql -u root -p < docs/schema/sabi_db_init.sql
```

Schema reference documentation for all three databases
is in `docs/schema/`.

### Caddy Configuration
sabi.yourdomain.com {
reverse_proxy localhost:8000
}

Caddy handles TLS automatically. No certificate
management required.

---

## Environment Variables

Copy `.env.example` to `.env` and populate all values.
See `.env.example` for descriptions of every variable.

Required variable groups:
- `ERP_DB_*` — school ERP database credentials
- `SABI_DB_*` — Sabi metadata database credentials
- `LMS_DB_*` — Moodle LMS database credentials
- `OPENCLAW_*` — Telegram bot bridge configuration
- `GOOGLE_SHEETS_*` — teacher assignment sync (optional)

**Never commit `.env` to version control.**
It is gitignored by default.

---

## Security and Data Privacy

This repository contains no student data, staff records,
parent contact information, or institutional credentials.

All sensitive configuration is loaded from environment
variables at runtime. Production deployments should
restrict database users to the minimum required
permissions per schema.

**Responsible disclosure:** If you identify a security
vulnerability in this codebase, please contact the
maintainer directly rather than opening a public issue.

---

## Licence

AGPL-3.0 — see [LICENSE](LICENSE)

The AGPL-3.0 licence requires that any deployment of
this software — including modified versions served over
a network — must make the corresponding source code
available to users of that network service. This ensures
that improvements to Sabi made by other institutions
are shared back with the community.

---

## Part of the ACE College International Open-Source Stack

This repository is one of 9 production systems open-sourced
by ACE College International. See the full portfolio at:

[github.com/AceCollegeInternational](https://github.com/AceCollegeInternational)

---

*Built and maintained by the Principal & Lead Developer,*
*ACE College International, Ikorodu, Lagos, Nigeria.*
*Self-taught. Production-deployed. Institutionally accountable.*
