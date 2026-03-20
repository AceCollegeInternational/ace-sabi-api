"""
config.py — Central configuration for Sabi API
All sensitive values are read from environment variables.
Never hardcode credentials in this file.

Create a .env file at the project root (excluded from version control).
See .env.example for the required variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# DATABASE CONNECTIONS
# =============================================================================

# Sabi DB — read/write. Runs on the same Contabo VPS as this API.
SABI_DB = {
    "host":     os.getenv("SABI_DB_HOST", "127.0.0.1"),
    "port":     int(os.getenv("SABI_DB_PORT", "3306")),
    "database": os.getenv("SABI_DB_NAME", "sabi_db"),
    "user":     os.getenv("SABI_DB_USER"),
    "password": os.getenv("SABI_DB_PASSWORD"),
    "charset":  "utf8mb4",
    # Pool settings — conservative for a single-school deployment
    "pool_size": int(os.getenv("SABI_DB_POOL_SIZE", "5")),
    "pool_name": "sabi_pool",
}

# Enterprise DB — read-only. Lives on a separate Contabo server.
ENTERPRISE_DB = {
    "host":     os.getenv("ENTERPRISE_DB_HOST"),
    "port":     int(os.getenv("ENTERPRISE_DB_PORT", "3306")),
    "database": os.getenv("ENTERPRISE_DB_NAME"),
    "user":     os.getenv("ENTERPRISE_DB_USER"),
    "password": os.getenv("ENTERPRISE_DB_PASSWORD"),
    "charset":  "utf8mb4",
    "pool_size": int(os.getenv("ENTERPRISE_DB_POOL_SIZE", "3")),
    "pool_name": "enterprise_pool",
}

# Moodle DB — read-only. Lives on the same separate Contabo server.
MOODLE_DB = {
    "host":     os.getenv("MOODLE_DB_HOST"),
    "port":     int(os.getenv("MOODLE_DB_PORT", "3306")),
    "database": os.getenv("MOODLE_DB_NAME"),
    "user":     os.getenv("MOODLE_DB_USER"),
    "password": os.getenv("MOODLE_DB_PASSWORD"),
    "charset":  "utf8mb4",
    "pool_size": int(os.getenv("MOODLE_DB_POOL_SIZE", "3")),
    "pool_name": "moodle_pool",
}


# =============================================================================
# API SETTINGS
# =============================================================================

# FastAPI application metadata
APP_TITLE       = "Sabi API"
APP_DESCRIPTION = "School Intelligence API — Teacher KPI, Attendance, and Operations"
APP_VERSION     = "1.0.0"

# Environment: "development" | "production"
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# In production, set this to your server's actual domain.
# Used to construct absolute URLs where needed.
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")


# =============================================================================
# SCHOOL CONFIGURATION
# =============================================================================

# Expected teacher arrival time (24-hour). Used for punctuality calculations.
SCHOOL_START_TIME = os.getenv("SCHOOL_START_TIME", "07:30")

# Default marking policy: teachers should return marked work within this many days.
# Can be overridden per assessment in marking_timeliness table.
DEFAULT_MARKING_POLICY_DAYS = int(os.getenv("DEFAULT_MARKING_POLICY_DAYS", "7"))

# Maximum observation score per rubric dimension (4 dimensions × this = 100)
OBSERVATION_DIMENSION_MAX = 25

# Minimum student response rate (%) for student feedback to be included in KPI.
# Below this threshold, student feedback score is excluded and weight redistributed.
MIN_STUDENT_FEEDBACK_RESPONSE_RATE = float(
    os.getenv("MIN_STUDENT_FEEDBACK_RESPONSE_RATE", "50.0")
)


# =============================================================================
# VALIDATION
# =============================================================================

def validate_config() -> list[str]:
    """
    Returns a list of missing required configuration values.
    Call at application startup. Raise if the list is non-empty.
    """
    required = [
        ("SABI_DB_USER",          SABI_DB["user"]),
        ("SABI_DB_PASSWORD",      SABI_DB["password"]),
        ("ENTERPRISE_DB_HOST",    ENTERPRISE_DB["host"]),
        ("ENTERPRISE_DB_NAME",    ENTERPRISE_DB["database"]),
        ("ENTERPRISE_DB_USER",    ENTERPRISE_DB["user"]),
        ("ENTERPRISE_DB_PASSWORD",ENTERPRISE_DB["password"]),
        ("MOODLE_DB_HOST",        MOODLE_DB["host"]),
        ("MOODLE_DB_NAME",        MOODLE_DB["database"]),
        ("MOODLE_DB_USER",        MOODLE_DB["user"]),
        ("MOODLE_DB_PASSWORD",    MOODLE_DB["password"]),
    ]
    return [name for name, value in required if not value]
