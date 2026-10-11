"""Settings, read once from the environment. Every value has a local-development default so
`docker compose up` works from a fresh clone; production overrides them."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("DATABASE_URL", "postgresql://atlas:atlas@localhost:5432/atlas")
    jwt_secret: str = os.environ.get("JWT_SECRET", "dev-only-change-me")
    jwt_ttl_minutes: int = int(os.environ.get("JWT_TTL_MINUTES", "480"))
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"))

    # demo data
    seed_demo: bool = _bool("SEED_DEMO", "true")
    demo_password: str = os.environ.get("DEMO_PASSWORD", "atlas-demo")

    # the Meridian Clinical Exchange feed
    meridian_base_url: str = os.environ.get("MERIDIAN_BASE_URL", "http://localhost:8100")
    meridian_api_key: str = os.environ.get("MERIDIAN_API_KEY", "mcx-dev-key")
    meridian_callback_url: str = os.environ.get(
        "MERIDIAN_CALLBACK_URL", "http://localhost:8000/api/integrations/webhooks/meridian"
    )
    worker_poll_seconds: float = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))
    worker_batch_size: int = int(os.environ.get("WORKER_BATCH_SIZE", "500"))
    resync_check_seconds: float = float(os.environ.get("RESYNC_CHECK_SECONDS", "60"))

    # AI features. The model file is baked into the image (or mounted) at /app/models.
    model_path: str = os.environ.get("MODEL_PATH", "/app/models/readmission.joblib")
    # optional: the artifact_sha256 from training_run.json. When set, a file that does not
    # match is refused (503) instead of being unpickled.
    model_sha256: str = (os.environ.get("MODEL_SHA256") or "").strip().lower()

    # The language model behind generative features: any OpenAI-compatible endpoint.
    # `or` (not a .get default) so an empty `LLM_BASE_URL=` line in .env still falls back.
    llm_base_url: str = os.environ.get("LLM_BASE_URL") or "https://api.tokenfactory.nebius.com/v1/"
    llm_api_key: str = os.environ.get("LLM_API_KEY") or ""
    llm_model: str = os.environ.get("LLM_MODEL") or "Qwen/Qwen3-235B-A22B-Instruct-2507"
    # tokens (input + output) one tenant may spend per UTC day across generative features
    llm_daily_token_limit: int = int(os.environ.get("LLM_DAILY_TOKEN_LIMIT") or "200000")


settings = Settings()
