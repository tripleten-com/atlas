"""The Atlas API.

    uvicorn app.main:app --reload

Routers live in app/routers/. Each one declares full paths (for example
`@router.get("/api/patients/{patient_id}")`) and is registered below. To add a feature: write a
router, import it here, and include it. That is the whole seam.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import ml
from .config import settings
from .db import close_pool, open_pool
from .routers import (
    activity,
    dashboard,
    encounters,
    facilities,
    health,
    integrations,
    org,
    patients,
    results,
    risk,
    session,
    summary,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("atlas.main")

# The JWT_SECRET that .env.example and k8s/base/secret.example.env ship with. Anyone who reads
# the repository knows it, so a deployment still using it lets them forge session tokens.
DEV_JWT_SECRET = "dev-only-change-me"


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    if settings.jwt_secret == DEV_JWT_SECRET or len(settings.jwt_secret) < 16:
        log.warning("JWT_SECRET is the development default; set a fresh one (openssl rand -hex 32) before anyone else can reach this API")
    # Load the model now rather than on the first request: the first call is then fast, and on
    # Kubernetes the load happens inside the startup probe's window instead of a user's request.
    # A missing file is not fatal: the risk route answers 503 until one is deployed.
    ml.load_model()
    yield
    close_pool()


app = FastAPI(
    title="Atlas API",
    version="1.0.0",
    description=(
        "Patient-management platform by Atlas Health. Sign in with `POST /api/auth/login`, then send "
        "`Authorization: Bearer <access_token>` on every other call (the Authorize button does this for you)."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(session.router)
app.include_router(facilities.router)
app.include_router(patients.router)
app.include_router(encounters.router)
app.include_router(dashboard.router)
app.include_router(results.router)
app.include_router(activity.router)
app.include_router(integrations.router)
app.include_router(org.router)
app.include_router(risk.router)  # AI feature: 30-day readmission risk
app.include_router(summary.router)  # AI feature: generated stay summary
