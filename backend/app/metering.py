"""Record one row in usage_events per AI call, so the Usage page can show calls, tokens and cost.

    from ..metering import record_usage

    record_usage(conn, user, feature="readmission-risk", model="readmission.joblib",
                 predictions=1, status="ok", elapsed_ms=12)

Nothing in the provided platform calls this yet. AI features you add are expected to.
"""
from __future__ import annotations

from psycopg import Connection

from .auth import CurrentUser
from .db import execute


def record_usage(
    conn: Connection,
    user: CurrentUser,
    feature: str,
    status: str = "ok",
    model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    predictions: int = 0,
    elapsed_ms: int | None = None,
    request_id: str | None = None,
) -> None:
    execute(
        conn,
        """INSERT INTO usage_events (tenant_id, user_id, feature, model, input_tokens, output_tokens, predictions,
                                     status, request_id, elapsed_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (user.tenant_id, user.id, feature, model, input_tokens, output_tokens, predictions, status, request_id, elapsed_ms),
    )
