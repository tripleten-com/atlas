"""Authentication and the tenant boundary.

A user logs in with email and password and receives a signed token (a JWT). The frontend
sends it back on every request as `Authorization: Bearer <token>`. `get_current_user`
checks the signature and expiry, loads the user, and hands the route a `CurrentUser` whose
`tenant_id` came from the database, never from anything the client sent.

Use it on every route that touches patient data:

    from ..auth import get_current_user

    @router.get("/api/patients/{patient_id}/risk")
    def risk(patient_id: str, user: CurrentUser = Depends(get_current_user)):
        ...  # filter every query on user.tenant_id
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import Connection

from .config import settings
from .db import fetch_one, get_db

ALGORITHM = "HS256"
_PBKDF2_ROUNDS = 210_000

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    tenant_id: int
    email: str
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(rounds))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- tokens

def create_access_token(user_id: int, tenant_id: int, role: str) -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_ttl_minutes)
    payload = {"sub": str(user_id), "tid": tenant_id, "role": role, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), expires


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def user_from_token(token: str, conn: Connection) -> CurrentUser:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired") from None
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token") from None
    row = fetch_one(conn, "SELECT id, tenant_id, email, name, role FROM users WHERE id = %s", (int(claims["sub"]),))
    if row is None or row["tenant_id"] != claims.get("tid"):
        raise _unauthorized("Invalid token")
    return CurrentUser(**row)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    conn: Connection = Depends(get_db),
) -> CurrentUser:
    """Route dependency: 401 unless the request carries a valid, unexpired bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    return user_from_token(credentials.credentials, conn)


# Some lessons call the dependency `current_user`. Both names point at the same function.
current_user = get_current_user


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user
