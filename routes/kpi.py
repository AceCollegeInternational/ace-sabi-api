"""
routes/kpi.py — KPI computation triggers, leaderboard, weights, and brief.

Endpoints:
    POST /kpi/compute/{teacher_id}   compute score for one teacher
    POST /kpi/compute/all            compute scores for all active teachers
    GET  /kpi/leaderboard            ranked scores for current term
    GET  /kpi/weights                view current active weights
    PUT  /kpi/weights                update weights (must sum to 100)
    GET  /kpi/brief                  principal's summary for morning brief
"""

from typing import Optional, List
from fastapi import APIRouter, HTTPException, status, Query
from pydantic import BaseModel

from database.connections import get_sabi
from services.kpi_engine import compute_kpi

router = APIRouter()


# =============================================================================
# MODELS
# =============================================================================

class WeightUpdate(BaseModel):
    """
    List of {index_key, weight} pairs.
    All active indices must be included and the weights must sum to 100.00.
    """
    weights: List[dict]   # [{index_key: str, weight: float}]
    updated_by: str


# =============================================================================
# COMPUTE
# =============================================================================

@router.post("/compute/{teacher_id}")
def compute_one(teacher_id: int, term_id: Optional[int] = None):
    """
    Compute and store the KPI score for a single teacher.
    If term_id is not provided, uses the current term.
    """
    with get_sabi() as (_, cur):
        if not term_id:
            cur.execute("SELECT id FROM academic_terms WHERE is_current=TRUE LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No current term set.")
            term_id = row["id"]

    try:
        result = compute_kpi(teacher_id, term_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500,
            detail=f"Score computation failed: {str(e)}")

    return result


@router.post("/compute/all")
def compute_all(term_id: Optional[int] = None):
    """
    Compute and store KPI scores for every active teacher.
    Returns a summary: succeeded, failed (with reasons).
    """
    with get_sabi() as (_, cur):
        if not term_id:
            cur.execute("SELECT id FROM academic_terms WHERE is_current=TRUE LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No current term set.")
            term_id = row["id"]

        cur.execute("""
            SELECT id, CONCAT(first_name,' ',last_name) AS name
            FROM   teachers WHERE is_active=TRUE
        """)
        teachers = cur.fetchall()

    succeeded = []
    failed    = []

    for t in teachers:
        try:
            result = compute_kpi(t["id"], term_id)
            succeeded.append({
                "teacher_id":  t["id"],
                "name":        t["name"],
                "total_score": result["total_score"],
                "is_eligible": result["is_eligible"],
            })
        except Exception as e:
            failed.append({"teacher_id": t["id"], "name": t["name"], "error": str(e)})

    return {
        "term_id":   term_id,
        "succeeded": len(succeeded),
        "failed":    len(failed),
        "results":   succeeded,
        "errors":    failed,
    }


# =============================================================================
# LEADERBOARD
# =============================================================================

@router.get("/leaderboard")
def leaderboard(
    term_id:       Optional[int] = None,
    eligible_only: bool = Query(False, description="Only show incentive-eligible teachers")
):
    """
    Ranked KPI scores for a term (defaults to current).
    Shows total score, category breakdown, eligibility, and delta from last term.
    """
    with get_sabi() as (_, cur):
        if not term_id:
            cur.execute("SELECT id, term_name FROM academic_terms WHERE is_current=TRUE LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No current term set.")
            term_id   = row["id"]
            term_name = row["term_name"]
        else:
            cur.execute("SELECT term_name FROM academic_terms WHERE id=%s", (term_id,))
            row = cur.fetchone()
            term_name = row["term_name"] if row else str(term_id)

        query = """
            SELECT
                ks.teacher_id,
                CONCAT(t.first_name,' ',t.last_name)  AS teacher_name,
                t.subject_primary,
                ROUND(ks.total_score, 2)              AS total_score,
                ROUND(ks.score_academic_impact, 2)    AS academic_impact,
                ROUND(ks.score_professional_reliability,2) AS professional_reliability,
                ROUND(ks.score_professional_growth,2) AS professional_growth,
                ROUND(ks.score_institutional_care,2)  AS institutional_care,
                ks.is_eligible,
                ks.score_delta,
                ks.computed_at
            FROM   kpi_scores ks
            JOIN   teachers t ON t.id = ks.teacher_id
            WHERE  ks.term_id = %s
        """
        params = [term_id]
        if eligible_only:
            query += " AND ks.is_eligible = TRUE"
        query += " ORDER BY ks.total_score DESC"

        cur.execute(query, params)
        rankings = cur.fetchall()

        # Add rank position
        for i, row in enumerate(rankings, 1):
            row["rank"] = i

    return {
        "term_id":   term_id,
        "term_name": term_name,
        "rankings":  rankings,
    }


# =============================================================================
# WEIGHTS MANAGEMENT
# =============================================================================

@router.get("/weights")
def get_weights():
    """Return current active KPI weights with category grouping."""
    with get_sabi() as (_, cur):
        cur.execute("""
            SELECT
                kc.category_name,
                kc.category_key,
                ki.index_key,
                ki.index_name,
                ki.data_source,
                kw.weight,
                kw.updated_at,
                kw.updated_by
            FROM   kpi_weights kw
            JOIN   kpi_indices ki  ON ki.id  = kw.index_id
            JOIN   kpi_categories kc ON kc.id = ki.category_id
            ORDER  BY kc.display_order, ki.display_order
        """)
        rows = cur.fetchall()

        # Compute total to verify integrity
        total = sum(float(r["weight"]) for r in rows)

    return {
        "weights":     rows,
        "total_weight": round(total, 2),
        "is_valid":    abs(total - 100.0) < 0.01,
    }


@router.put("/weights")
def update_weights(body: WeightUpdate):
    """
    Replace active KPI weights.
    All index_keys must be provided and the weights must sum to exactly 100.00.
    """
    if not body.weights:
        raise HTTPException(status_code=422, detail="weights list is empty.")

    total = sum(float(w["weight"]) for w in body.weights)
    if abs(total - 100.0) > 0.01:
        raise HTTPException(
            status_code=422,
            detail=f"Weights must sum to 100.00. Current sum: {round(total,2)}."
        )

    with get_sabi() as (_, cur):
        # Validate all index_keys exist
        for w in body.weights:
            cur.execute("SELECT id FROM kpi_indices WHERE index_key=%s",
                        (w["index_key"],))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=422,
                    detail=f"Unknown index_key: {w['index_key']}")

            cur.execute("""
                UPDATE kpi_weights SET weight=%s, updated_by=%s
                WHERE  index_id = %s
            """, (w["weight"], body.updated_by, row["id"]))

    return {"message": "Weights updated.", "new_total": round(total, 2)}


# =============================================================================
# PRINCIPAL'S BRIEF
# =============================================================================

@router.get("/brief")
def principal_brief(term_id: Optional[int] = None):
    """
    KPI summary for the principal's morning brief.
    Returns: school-wide average, top 3 performers, bottom 3, eligible count,
    teachers with no score yet this term, and any computation warnings.
    """
    with get_sabi() as (_, cur):
        if not term_id:
            cur.execute("SELECT id, term_name FROM academic_terms WHERE is_current=TRUE LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No current term set.")
            term_id   = row["id"]
            term_name = row["term_name"]
        else:
            cur.execute("SELECT term_name FROM academic_terms WHERE id=%s", (term_id,))
            row = cur.fetchone()
            term_name = row["term_name"] if row else str(term_id)

        # Overall stats
        cur.execute("""
            SELECT
                COUNT(*)                          AS teachers_scored,
                ROUND(AVG(total_score),2)         AS school_average,
                ROUND(MAX(total_score),2)         AS highest_score,
                ROUND(MIN(total_score),2)         AS lowest_score,
                SUM(is_eligible=TRUE)             AS eligible_count,
                SUM(is_eligible=FALSE)            AS ineligible_count
            FROM kpi_scores WHERE term_id=%s
        """, (term_id,))
        stats = cur.fetchone()

        # Top 3
        cur.execute("""
            SELECT CONCAT(t.first_name,' ',t.last_name) AS name,
                   t.subject_primary, ROUND(ks.total_score,2) AS score,
                   ks.is_eligible, ks.score_delta
            FROM   kpi_scores ks JOIN teachers t ON t.id=ks.teacher_id
            WHERE  ks.term_id=%s
            ORDER  BY ks.total_score DESC LIMIT 3
        """, (term_id,))
        top_3 = cur.fetchall()

        # Bottom 3 (excluding unscored)
        cur.execute("""
            SELECT CONCAT(t.first_name,' ',t.last_name) AS name,
                   t.subject_primary, ROUND(ks.total_score,2) AS score,
                   ks.is_eligible, ks.computation_notes
            FROM   kpi_scores ks JOIN teachers t ON t.id=ks.teacher_id
            WHERE  ks.term_id=%s
            ORDER  BY ks.total_score ASC LIMIT 3
        """, (term_id,))
        bottom_3 = cur.fetchall()

        # Teachers not yet scored
        cur.execute("""
            SELECT CONCAT(t.first_name,' ',t.last_name) AS name, t.subject_primary
            FROM   teachers t
            WHERE  t.is_active=TRUE
            AND    t.id NOT IN (
                SELECT teacher_id FROM kpi_scores WHERE term_id=%s
            )
        """, (term_id,))
        not_scored = cur.fetchall()

        # Most improved (highest positive delta)
        cur.execute("""
            SELECT CONCAT(t.first_name,' ',t.last_name) AS name,
                   ROUND(ks.score_delta,2) AS improvement
            FROM   kpi_scores ks JOIN teachers t ON t.id=ks.teacher_id
            WHERE  ks.term_id=%s AND ks.score_delta IS NOT NULL
            ORDER  BY ks.score_delta DESC LIMIT 3
        """, (term_id,))
        most_improved = cur.fetchall()

    return {
        "term_id":       term_id,
        "term_name":     term_name,
        "overview":      stats,
        "top_3":         top_3,
        "bottom_3":      bottom_3,
        "most_improved": most_improved,
        "not_scored":    not_scored,
    }
