"""
main.py — Sabi API application entry point.

Run in development:
    uvicorn main:app --reload --port 8000

Run in production (inside Docker):
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

import config
from database.connections import init_pools, close_pools, check_all_connections
from middleware.auth import require_api_key

# Route imports — uncomment each as the file is created
from routes import teachers
from routes import terms
from routes import teacher_attendance
from routes import lesson_plans
from routes import observations
from routes import marking_timeliness
from routes import professional_growth
from routes import student_feedback
from routes import disciplinary
from routes import kpi
from routes import pastoral_logs
from routes import at_risk
from routes import students
from routes import student_attendance
from routes import student_scores
from routes import notifications
from routes import enforcement

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO if config.ENVIRONMENT == "production" else logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# =============================================================================
# LIFESPAN — startup and shutdown
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("Sabi API starting up — environment: %s", config.ENVIRONMENT)

    missing = config.validate_config()
    if missing:
        logger.critical("Missing required config values: %s", missing)
        sys.exit(1)

    init_pools()
    logger.info("Database pools initialised.")

    yield  # application runs here

    # ── Shutdown ─────────────────────────────────────────────────────────────
    close_pools()
    logger.info("Sabi API shut down cleanly.")


# =============================================================================
# APPLICATION
# =============================================================================

app = FastAPI(
    title=config.APP_TITLE,
    description=config.APP_DESCRIPTION,
    version=config.APP_VERSION,
    lifespan=lifespan,
    # Disable automatic /docs and /redoc in production
    docs_url="/docs" if config.ENVIRONMENT != "production" else None,
    redoc_url=None,
)

# CORS — restrict to localhost in production unless a dashboard domain is added
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"] if config.ENVIRONMENT != "production" else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# =============================================================================
# HEALTH CHECK — unauthenticated, used by Docker/monitoring
# =============================================================================

@app.get("/health", tags=["System"])
def health_check():
    """
    Returns database connectivity status for all three databases.
    No authentication required — used by load balancers and monitoring.
    """
    db_status = check_all_connections()
    overall   = all(v["status"] == "ok" for v in db_status.values())
    return {
        "status":    "ok" if overall else "degraded",
        "databases": db_status,
        "version":   config.APP_VERSION,
    }


# =============================================================================
# ROUTERS
# All routes require API key authentication via the global dependency.
# =============================================================================

PROTECTED = {"dependencies": [Depends(require_api_key)]}

app.include_router(teachers.router,           prefix="/teachers",           tags=["Teachers"],           **PROTECTED)
app.include_router(terms.router,              prefix="/terms",              tags=["Academic Terms"],      **PROTECTED)
app.include_router(teacher_attendance.router, prefix="/attendance",         tags=["Teacher Attendance"], **PROTECTED)
app.include_router(lesson_plans.router,       prefix="/lesson-plans",       tags=["Lesson Plans"],       **PROTECTED)
app.include_router(observations.router,       prefix="/observations",       tags=["Observations"],       **PROTECTED)
app.include_router(marking_timeliness.router, prefix="/marking",            tags=["Marking Timeliness"], **PROTECTED)
app.include_router(professional_growth.router,prefix="/professional-growth",tags=["Professional Growth"],**PROTECTED)
app.include_router(student_feedback.router,   prefix="/student-feedback",   tags=["Student Feedback"],   **PROTECTED)
app.include_router(disciplinary.router,       prefix="/disciplinary",       tags=["Disciplinary"],       **PROTECTED)
app.include_router(kpi.router,                prefix="/kpi",                tags=["KPI"],                **PROTECTED)
app.include_router(pastoral_logs.router,      prefix="/pastoral-logs",      tags=["Pastoral Logs"],      **PROTECTED)
app.include_router(at_risk.router,            prefix="/at-risk",            tags=["At-Risk Students"],   **PROTECTED)
app.include_router(students.router,           prefix="/students",           tags=["Students"],           **PROTECTED)
app.include_router(student_attendance.router, prefix="/student-attendance", tags=["Student Attendance"], **PROTECTED)
app.include_router(student_scores.router,     prefix="/scores",             tags=["Student Scores"],     **PROTECTED)
app.include_router(notifications.router,      prefix="/notifications",      tags=["Parent Notifications"], **PROTECTED)
app.include_router(enforcement.router,        prefix="/enforcement",        tags=["Enforcement"],       **PROTECTED)
