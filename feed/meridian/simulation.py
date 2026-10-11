"""Runs one facility's simulator and publishes what happens into Meridian's event log.

On first start the facility plays FEED_BACKFILL_DAYS of history at full speed, so a new
subscriber has something to receive. After that it keeps pace with the wall clock: the next
clinical hour is simulated in advance and each event is published when its own time arrives,
so a live facility trickles events continuously and never publishes anything from the future.
FEED_CADENCE_SCALE > 1 compresses clinical time for demos.

The simulator's state is pickled to the data volume, so restarting the container resumes the
same patients and visits instead of starting a new hospital.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import pickle
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clinsim import FacilityProfile, Simulator

from .config import Settings
from .store import Store, now_iso

log = logging.getLogger("meridian.sim")

LATE_TYPES = {"vital_signs.recorded", "lab.resulted", "medication.administered"}


class FacilityFeed:
    def __init__(self, store: Store, profile: FacilityProfile, settings: Settings):
        self.store = store
        self.profile = profile
        self.settings = settings
        self.code = profile.code
        self.state_path = Path(settings.data_dir) / f"sim-{self.code}.pkl"
        self.chaos = random.Random(settings.random_seed * 7 + sum(map(ord, self.code)))
        self._tie = itertools.count()
        self.pending: list[tuple[float, int, dict]] = []  # (release time, tie-breaker, event)
        self.sim = self._load() or self._fresh()
        self.status = "starting"

    # ------------------------------------------------------------ state
    def _load(self) -> Simulator | None:
        if not self.state_path.exists():
            return None
        try:
            with self.state_path.open("rb") as fh:
                state = pickle.load(fh)  # our own file on our own volume
            if state.get("epoch") != self.store.epoch:
                return None
            self.pending = state.get("pending", [])
            self._tie = itertools.count(len(self.pending) + 1)
            log.info("%s: resumed simulation at %s", self.code, state["sim"].now.isoformat())
            return state["sim"]
        except Exception as exc:  # a corrupt state file should not stop the feed
            log.warning("%s: could not load saved state (%s), starting fresh", self.code, exc)
            return None

    def _fresh(self) -> Simulator:
        runs_key = f"runs:{self.code}"
        with self.store.lock:
            row = self.store.db.execute("SELECT value FROM meta WHERE key = ?", (runs_key,)).fetchone()
            run = int(row["value"]) + 1 if row else 1
            self.store.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (runs_key, str(run)))
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=self.settings.backfill_days)
        tag = f"{self.store.epoch[:3].upper()}{run}"
        log.info("%s: new simulation from %s (id tag %s)", self.code, start.isoformat(), tag)
        return Simulator(self.profile, seed=self.settings.random_seed + sum(map(ord, self.code)), start=start, id_tag=tag)

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump({"epoch": self.store.epoch, "sim": self.sim, "pending": self.pending}, fh)
        tmp.replace(self.state_path)

    # ------------------------------------------------------------ publishing
    def _envelope(self, ev: dict) -> dict:
        return {
            "id": "evt_" + uuid.uuid4().hex,
            "type": ev["type"],
            "schema_version": 1,
            "facility": self.code,
            "epoch": self.store.epoch,
            "occurred_at": ev["occurred_at"].isoformat(),
            "published_at": now_iso(),
            "patient": ev["patient"],
            "encounter": ev["encounter"],
            "data": ev["data"],
        }

    def _schedule(self, raw: list[dict]) -> None:
        """Queue a window's events, each with the moment it may be published.

        Normally that is the moment it happened. Real feeds are untidy, so a few measurements are
        held back a while, and now and then a new patient's registration is published only after
        their first visit events.
        """
        scale = max(self.settings.cadence_scale, 0.01)
        entries = []
        for ev in raw:
            env = self._envelope(ev)
            release = ev["occurred_at"]
            if env["type"] in LATE_TYPES and self.chaos.random() < self.settings.late_rate:
                release += timedelta(minutes=self.chaos.uniform(10, 60)) / scale
            entries.append([release, env])
        if self.chaos.random() < self.settings.reorder_rate:
            regs = [e for e in entries if e[1]["type"] == "patient.registered"]
            if regs:
                reg = regs[0]
                later = [e[0] for e in entries if e[1]["patient"] == reg[1]["patient"] and e is not reg]
                if later:
                    reg[0] = min(later) + timedelta(seconds=20) / scale  # the first visit event goes out first
        for release, env in entries:
            heapq.heappush(self.pending, (release.timestamp(), next(self._tie), env))

    def flush(self) -> int:
        """Publish every queued event whose release time has passed, in release order."""
        now = datetime.now(timezone.utc).timestamp()
        due = []
        while self.pending and self.pending[0][0] <= now:
            due.append(heapq.heappop(self.pending)[2])
        if due:
            stamp = now_iso()
            for env in due:
                env["published_at"] = stamp
            self.store.append(self.code, due)
        return len(due)

    # ------------------------------------------------------------ the loop
    async def run(self) -> None:
        """Backfill at full speed, then keep one clinical hour of events queued ahead of the clock."""
        live_hour = timedelta(hours=1) / max(self.settings.cadence_scale, 0.01)
        steps_since_save = 0
        while True:
            now = datetime.now(timezone.utc)
            if now - self.sim.now >= timedelta(hours=1):
                self.status = "backfilling"
                self._schedule(self.sim.advance(timedelta(hours=1)))
                self.flush()
                steps_since_save += 1
                if steps_since_save >= 24:
                    self.save()
                    steps_since_save = 0
                    await asyncio.sleep(0)
                continue
            self.status = "live"
            if self.sim.now < now + live_hour:
                self._schedule(self.sim.advance(live_hour))
                steps_since_save += 1
            published = self.flush()
            if published:
                log.info("%s: published %d events (%d queued)", self.code, published, len(self.pending))
            if published or steps_since_save:
                self.save()
                steps_since_save = 0
            wait = self.pending[0][0] - datetime.now(timezone.utc).timestamp() if self.pending else 5.0
            await asyncio.sleep(min(5.0, max(0.25, wait)))

    def describe(self) -> dict:
        return {
            "code": self.code,
            "name": self.profile.name,
            "kind": "hospital" if self.profile.inpatient else "clinic",
            "status": self.status,
            "epoch": self.store.epoch,
            "latest_sequence": self.store.latest_sequence(self.code),
            "simulated_until": self.sim.now.isoformat(),
            "active_encounters": len(self.sim.active),
            "queued_events": len(self.pending),
            "event_counts": self.store.counts(self.code),
        }
