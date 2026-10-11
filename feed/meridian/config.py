"""Meridian feed settings. All from the environment, all with working local defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass

from clinsim import FacilityProfile

# Facility profiles Meridian can simulate, keyed by the code customers subscribe with.
PROFILES = {
    "RGH": FacilityProfile("RGH", "Riverside General Hospital", pool_size=5000,
                           outpatient_per_year=1.2, ed_per_year=0.45, direct_admit_per_year=0.38),
    "RNC": FacilityProfile("RNC", "Northgate Clinic", pool_size=3000,
                           outpatient_per_year=3.2, ed_per_year=0.0, direct_admit_per_year=0.0, inpatient=False),
    "HMC": FacilityProfile("HMC", "Harborview Medical Center", pool_size=3600,
                           outpatient_per_year=1.0, ed_per_year=0.55, direct_admit_per_year=0.35),
}


def _float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    api_key: str = os.environ.get("MERIDIAN_API_KEY", "mcx-dev-key")
    facilities: tuple[str, ...] = tuple(
        code.strip() for code in os.environ.get("FEED_FACILITIES", "RGH,RNC,HMC").split(",") if code.strip()
    )
    data_dir: str = os.environ.get("FEED_DATA_DIR", "./data")
    random_seed: int = int(os.environ.get("FEED_SEED", "314"))
    backfill_days: float = _float("FEED_BACKFILL_DAYS", "14")
    # How many clinical hours pass per real hour once the backfill is done. The default, 1, is real
    # time: a few dozen events an hour per hospital, and daily counts look like a real one. Raise it
    # for a fast demo (30 = a clinical day every 48 minutes), knowing the daily charts will spike.
    cadence_scale: float = _float("FEED_CADENCE_SCALE", "1")
    batch_size: int = int(os.environ.get("FEED_BATCH_SIZE", "250"))
    # Imperfections a real feed has, so the consumer has to cope with them.
    duplicate_rate: float = _float("FEED_DUPLICATE_RATE", "0.03")  # chance a delivery repeats an event
    reorder_rate: float = _float("FEED_REORDER_RATE", "0.05")  # chance a clinical hour publishes a registration late
    late_rate: float = _float("FEED_LATE_RATE", "0.01")  # chance a measurement is published 10 to 60 minutes late


settings = Settings()
