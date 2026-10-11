"""Organisation settings, usage metering and the audit trail."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from ..auth import CurrentUser, get_current_user, require_admin
from ..db import fetch_all, fetch_one, get_db
from ..schemas import AuditItem, OrgOut, TenantOut, UsageSummary, UserOut
from .facilities import list_facilities

router = APIRouter(tags=["organisation"])

# Illustrative list prices per million tokens, used only to estimate spend on the Usage page.
PRICE_PER_MILLION_INPUT = 3.0
PRICE_PER_MILLION_OUTPUT = 15.0


@router.get("/api/org", response_model=OrgOut)
def organisation(user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)) -> OrgOut:
    tenant = fetch_one(conn, "SELECT id, slug, name FROM tenants WHERE id = %s", (user.tenant_id,))
    users = fetch_all(
        conn,
        "SELECT id, email, name, role, title, last_login_at FROM users WHERE tenant_id = %s ORDER BY role, name",
        (user.tenant_id,),
    )
    return OrgOut(
        tenant=TenantOut(**tenant),
        facilities=list_facilities(conn, user.tenant_id),
        users=[UserOut(**u, tenant=TenantOut(**tenant)) for u in users],
    )


@router.get("/api/usage", response_model=UsageSummary)
def usage(days: int = Query(30, ge=1, le=365), user: CurrentUser = Depends(require_admin),
          conn: Connection = Depends(get_db)) -> UsageSummary:
    """AI usage recorded in usage_events for this tenant. Empty until an AI feature records usage."""
    p = {"t": user.tenant_id, "days": days}
    window = "tenant_id = %(t)s AND ts > now() - make_interval(days => %(days)s)"
    totals = fetch_one(
        conn,
        f"""SELECT count(*) AS n, coalesce(sum(input_tokens), 0) AS inp, coalesce(sum(output_tokens), 0) AS outp,
                   coalesce(sum(predictions), 0) AS preds
            FROM usage_events WHERE {window}""",
        p,
    )
    by_feature = fetch_all(
        conn,
        f"""SELECT feature, count(*) AS calls, sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
                   sum(predictions) AS predictions,
                   round(avg(elapsed_ms)) AS avg_ms,
                   count(*) FILTER (WHERE status <> 'ok' AND status <> 'cache_hit') AS errors
            FROM usage_events WHERE {window} GROUP BY feature ORDER BY calls DESC""",
        p,
    )
    by_day = fetch_all(
        conn,
        f"""SELECT date_trunc('day', ts)::date AS day, count(*) AS calls,
                   sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens
            FROM usage_events WHERE {window} GROUP BY 1 ORDER BY 1""",
        p,
    )
    recent = fetch_all(
        conn,
        """SELECT u.ts, u.feature, u.model, u.status, u.input_tokens, u.output_tokens, u.predictions, u.elapsed_ms,
                   usr.name AS user_name
            FROM usage_events u LEFT JOIN users usr ON usr.id = u.user_id
            WHERE u.tenant_id = %(t)s AND u.ts > now() - make_interval(days => %(days)s) ORDER BY u.ts DESC LIMIT 25""",
        p,
    )
    cost = totals["inp"] / 1e6 * PRICE_PER_MILLION_INPUT + totals["outp"] / 1e6 * PRICE_PER_MILLION_OUTPUT
    return UsageSummary(
        days=days, total_events=totals["n"], total_input_tokens=totals["inp"], total_output_tokens=totals["outp"],
        total_predictions=totals["preds"], estimated_cost_usd=round(cost, 4),
        by_feature=by_feature, by_day=by_day, recent=recent,
    )


@router.get("/api/audit", response_model=list[AuditItem])
def audit_trail(limit: int = Query(100, ge=1, le=500), user: CurrentUser = Depends(require_admin),
                conn: Connection = Depends(get_db)) -> list[AuditItem]:
    rows = fetch_all(
        conn,
        """SELECT a.id, a.ts, u.name AS user_name, a.action, a.target, a.detail
           FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
           WHERE a.tenant_id = %s ORDER BY a.ts DESC LIMIT %s""",
        (user.tenant_id, limit),
    )
    return [AuditItem(**r) for r in rows]
