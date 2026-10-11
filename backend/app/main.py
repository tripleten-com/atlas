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
    session,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
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
