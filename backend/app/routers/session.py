"""Login and the current session."""
from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from ..auth import CurrentUser, create_access_token, get_current_user, verify_password
from ..db import execute, fetch_one, get_db
from ..schemas import LoginRequest, TenantOut, TokenResponse, UserOut
from ..tenancy import audit

router = APIRouter(tags=["auth"])

USER_SQL = """
SELECT u.id, u.email, u.name, u.role, u.title, u.last_login_at, u.password_hash,
       t.id AS tenant_id, t.slug AS tenant_slug, t.name AS tenant_name
FROM users u JOIN tenants t ON t.id = u.tenant_id
"""


def _user_out(row: dict) -> UserOut:
    return UserOut(
        id=row["id"], email=row["email"], name=row["name"], role=row["role"], title=row["title"],
        last_login_at=row["last_login_at"],
        tenant=TenantOut(id=row["tenant_id"], slug=row["tenant_slug"], name=row["tenant_name"]),
    )


@router.post("/api/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, conn: Connection = Depends(get_db)) -> TokenResponse:
    row = fetch_one(conn, USER_SQL + " WHERE lower(u.email) = lower(%s)", (body.email.strip(),))
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token, expires = create_access_token(row["id"], row["tenant_id"], row["role"])
    execute(conn, "UPDATE users SET last_login_at = now() WHERE id = %s", (row["id"],))
    user = CurrentUser(id=row["id"], tenant_id=row["tenant_id"], email=row["email"], name=row["name"], role=row["role"])
    audit(conn, user, "login")
    return TokenResponse(access_token=token, expires_at=expires, user=_user_out(row))


@router.get("/api/auth/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)) -> UserOut:
    return _user_out(fetch_one(conn, USER_SQL + " WHERE u.id = %s", (user.id,)))
