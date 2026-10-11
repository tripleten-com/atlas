"""A small discrete-event simulator of one facility's clinical activity.

The simulator runs in *clinical hours*. Each call to `advance()` plays one clinical hour and
returns the events that happened in it, stamped with wall-clock times inside the window you
pass. The seed script plays two years of hours back to back; the Meridian feed plays a short
backfill the same way and then keeps stepping in real time.

Events use the same envelope the Meridian feed publishes:

    {"type": "vital_signs.recorded", "occurred_at": datetime, "patient": {"mrn": ...},
     "encounter": {"visit_id": ...} | None, "data": {...}}
"""
from __future__ import annotations

import heapq
import itertools
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import catalog as C

HOURS_PER_YEAR = 24 * 365


@dataclass
class FacilityProfile:
    code: str
    name: str
    pool_size: int  # people the facility could ever see
    outpatient_per_year: float  # visits per person per year, before chronic burden
    ed_per_year: float
    direct_admit_per_year: float
    inpatient: bool = True  # clinics set this to False: ED visits and admissions never happen


@dataclass
class SimPatient:
    idx: int
    mrn: str
    first_name: str
    last_name: str
    sex: str
    date_of_birth: date
    conditions: tuple[str, ...]
    frailty: float
    baseline: dict[str, float]
    lab_factor: dict[str, float]
    registered: bool = False
    busy: bool = False  # currently in an encounter
    admissions: int = 0


@dataclass
class MedOrder:
    order_id: str
    code: str
    next_due: float | None  # clinical hour of the next scheduled dose, None for PRN


@dataclass
class ActiveEncounter:
    visit_id: str
    patient: SimPatient
    admission_type: str
    admit_source: str
    start: float
    end: float
    condition: str | None
    severity: float
    next_vitals: float
    next_labs: float
    lab_rounds: int = 0
    deteriorate_at: float | None = None
    expires: bool = False
    meds: list[MedOrder] = field(default_factory=list)
    ed_admit: str | None = None  # "inpatient" / "observation" when this ED visit ends in an admission
    abnormal_labs: int = 0


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


class Simulator:
    def __init__(self, profile: FacilityProfile, seed: int, start: datetime, id_tag: str = ""):
        self.p = profile
        self.tag = id_tag  # keeps visit and order ids unique if a feed restarts from scratch
        self.rng = random.Random(seed)
        self.t = 0.0  # clinical hours since start
        self.now = start  # wall-clock start of the current clinical hour
        self._tie = itertools.count()
        self._visit_counter = itertools.count(1)
        self._order_counter = itertools.count(1)
        self.heap: list[tuple[float, int, str, int]] = []
        self.active: dict[str, ActiveEncounter] = {}
        self.pool = [self._make_patient(i, start.date()) for i in range(profile.pool_size)]
        for pt in self.pool:
            self._schedule_background(pt, 0.0)

    # ------------------------------------------------------------------ population
    def _make_patient(self, idx: int, today: date) -> SimPatient:
        r = self.rng
        sex = r.choice(("female", "male"))
        first = r.choice(C.FIRST_NAMES_F if sex == "female" else C.FIRST_NAMES_M)
        last = r.choice(C.LAST_NAMES)
        age = int(min(97, max(18, r.gauss(57, 18))))
        dob = today - timedelta(days=age * 365 + r.randint(0, 364))
        conditions = tuple(
            c.code for c in C.CONDITIONS.values()
            if c.code not in ("pneumonia", "sepsis") and r.random() < c.prevalence * (0.35 + age / 55)
        )
        frailty = min(1.0, max(0.0, r.gauss(0.15 + age / 160 + 0.06 * len(conditions), 0.12)))
        baseline = {}
        for v in C.VITALS.values():
            val = r.gauss(v.mean, v.sd * 0.55)
            for cond in conditions:
                shift = C.CONDITIONS[cond].vital_shift.get(v.code, 0.0)
                val += shift * (1.0 if cond == "hypertension" else 0.2)
            baseline[v.code] = val
        lab_factor = {}
        for lab in C.LABS.values():
            f = r.gauss(1.0, 0.04)
            for cond in conditions:
                shift = C.CONDITIONS[cond].lab_shift.get(lab.code)
                if shift:
                    f *= 1 + (shift - 1) * 0.45
            lab_factor[lab.code] = f
        return SimPatient(
            idx=idx, mrn=f"{self.p.code}-{100000 + idx}", first_name=first, last_name=last, sex=sex,
            date_of_birth=dob, conditions=conditions, frailty=frailty, baseline=baseline, lab_factor=lab_factor,
        )

    def _burden(self, pt: SimPatient) -> float:
        return len(pt.conditions)

    def _schedule(self, when: float, kind: str, pt: SimPatient) -> None:
        heapq.heappush(self.heap, (when, next(self._tie), kind, pt.idx))

    def _schedule_background(self, pt: SimPatient, after: float) -> None:
        r, p = self.rng, self.p
        b = self._burden(pt)
        rates = {
            "outpatient": p.outpatient_per_year * (0.6 + 0.35 * b),
            "ed": p.ed_per_year * (0.5 + 0.45 * b) * (1 + pt.frailty) if p.inpatient else 0.0,
            "direct": p.direct_admit_per_year * (0.2 + 0.5 * b) * (1 + pt.frailty) if p.inpatient else 0.0,
        }
        for kind, per_year in rates.items():
            if per_year > 0:
                self._schedule(after + r.expovariate(per_year / HOURS_PER_YEAR), kind, pt)

    # ------------------------------------------------------------------ helpers
    def _ts(self, clinical_hour: float, hour_len: timedelta) -> datetime:
        frac = min(0.999, max(0.0, clinical_hour - self.t))
        return self.now + frac * hour_len

    def _event(self, type_: str, at: datetime, pt: SimPatient, enc: ActiveEncounter | None, data: dict) -> dict:
        return {
            "type": type_,
            "occurred_at": at,
            "patient": {"mrn": pt.mrn},
            "encounter": {"visit_id": enc.visit_id} if enc else None,
            "data": data,
        }

    def _register(self, pt: SimPatient, at: datetime, out: list) -> None:
        if pt.registered:
            return
        pt.registered = True
        out.append(self._event("patient.registered", at, pt, None, {
            "mrn": pt.mrn,
            "first_name": pt.first_name,
            "last_name": pt.last_name,
            "sex": pt.sex,
            "date_of_birth": pt.date_of_birth.isoformat(),
        }))

    def _acute_level(self, enc: ActiveEncounter, h: float) -> float:
        """1.0 at admission, easing toward 0.3 by the planned end of the stay."""
        span = max(1.0, enc.end - enc.start)
        progress = min(1.0, max(0.0, (h - enc.start) / span))
        return enc.severity * (1.0 - 0.7 * progress)

    def _det_level(self, enc: ActiveEncounter, h: float) -> float:
        if enc.deteriorate_at is None or h < enc.deteriorate_at:
            return 0.0
        return min(1.0, (h - enc.deteriorate_at) / 24.0)

    # ------------------------------------------------------------------ measurements
    def _vitals(self, enc: ActiveEncounter, h: float, at: datetime) -> dict:
        r, pt = self.rng, enc.patient
        cond = C.CONDITIONS.get(enc.condition) if enc.condition else None
        acute = self._acute_level(enc, h) if enc.admission_type != "outpatient" else 0.0
        det = self._det_level(enc, h)
        det_shift = {"heart_rate": 28, "respiratory_rate": 9, "spo2": -9, "systolic_bp": -28, "diastolic_bp": -14, "temperature": 1.1}
        measurements = []
        for v in C.VITALS.values():
            if r.random() < (0.12 if (v.code == "temperature" and enc.admission_type == "outpatient") else 0.03):
                continue  # not every set is complete
            val = pt.baseline[v.code]
            if cond:
                val += cond.vital_shift.get(v.code, 0.0) * acute
            val += det_shift.get(v.code, 0.0) * det
            val += r.gauss(0, v.sd * 0.35)
            val = min(v.high, max(v.low, val))
            if v.code == "spo2":
                val = min(100, val)
            measurements.append({"code": v.code, "label": v.label, "value": round(val, v.decimals) if v.decimals else round(val), "unit": v.unit})
        return {"recorded_at": at.isoformat(), "measurements": measurements}

    def _lab_panel(self, enc: ActiveEncounter, panel: str, h: float, collected: datetime, resulted: datetime) -> dict:
        r, pt = self.rng, enc.patient
        cond = C.CONDITIONS.get(enc.condition) if enc.condition else None
        acute = self._acute_level(enc, h) if enc.admission_type != "outpatient" else 0.0
        det = self._det_level(enc, h)
        results = []
        abnormal = 0
        for code in C.PANELS[panel]:
            lab = C.LABS[code]
            mean = lab.mean * pt.lab_factor[code]
            if cond and code in cond.lab_shift:
                mean *= 1 + (cond.lab_shift[code] - 1) * acute
            if det:
                mean *= {"lactate": 1 + 2.5 * det, "wbc": 1 + 0.6 * det, "creatinine": 1 + 0.5 * det, "platelets": 1 - 0.35 * det}.get(code, 1.0)
            val = max(lab.floor, r.gauss(mean, lab.sd * (0.6 + 0.4 * mean / lab.mean)))
            val = round(val, lab.decimals) if lab.decimals else round(val)
            flag = "L" if val < lab.ref_low else "H" if val > lab.ref_high else "N"
            abnormal += flag != "N"
            results.append({
                "code": lab.code, "loinc": lab.loinc, "label": lab.label, "value": val, "unit": lab.unit,
                "ref_low": lab.ref_low, "ref_high": lab.ref_high, "flag": flag,
            })
        enc.abnormal_labs = abnormal
        return {
            "order_id": f"LAB{self.p.code}{self.tag}{next(self._order_counter):07d}",
            "panel": panel,
            "panel_name": C.PANEL_NAMES[panel],
            "collected_at": collected.isoformat(),
            "resulted_at": resulted.isoformat(),
            "results": results,
        }

    # ------------------------------------------------------------------ encounters
    def _start_encounter(self, pt: SimPatient, kind: str, h: float, hour_len: timedelta, out: list,
                         source: str | None = None, condition: str | None = None) -> None:
        r = self.rng
        pt.busy = True
        at = self._ts(h, hour_len)
        self._register(pt, at, out)
        visit_id = f"V{self.p.code}{self.tag}{next(self._visit_counter):07d}"
        acute_choices = list(pt.conditions) + ["pneumonia"] * 2 + ["sepsis"] + ["acs"]
        if kind == "outpatient":
            enc = ActiveEncounter(visit_id, pt, "outpatient", source or r.choice(("clinic", "physician_referral")),
                                  h, h + r.uniform(0.5, 1.5), None, 0.0, h + 0.15, h + 0.4)
        elif kind == "emergency":
            cond = condition or r.choice(acute_choices)
            severity = min(1.0, max(0.1, r.betavariate(2, 3) + 0.15 * pt.frailty))
            enc = ActiveEncounter(visit_id, pt, "emergency", source or r.choice(("self_referral", "self_referral", "transfer")),
                                  h, h + r.uniform(3, 9), cond, severity, h + 0.1, h + 0.6)
            weight = C.CONDITIONS[cond].admit_weight
            if r.random() < min(0.85, 0.12 * weight * (1 + pt.frailty) * (0.5 + severity)):
                enc.ed_admit = "observation" if r.random() < 0.18 else "inpatient"
        else:  # inpatient / observation
            cond = condition or r.choice(acute_choices)
            cdef = C.CONDITIONS[cond]
            severity = min(1.0, max(0.15, r.betavariate(2.2, 2.8) + 0.15 * pt.frailty))
            if kind == "observation":
                los = r.uniform(12, 36)
            else:
                mean = cdef.los_days * 24 * (0.55 + 0.5 * severity) * (1 + 0.25 * pt.frailty)
                los = max(18.0, r.gammavariate(3.0, mean / 3.0))
            enc = ActiveEncounter(visit_id, pt, kind, source or r.choice(("physician_referral", "physician_referral", "transfer")),
                                  h, h + los, cond, severity, h + 0.2, h + 0.7)
            pt.admissions += 1
            if kind == "inpatient" and cond in ("sepsis", "pneumonia", "heart_failure", "copd") and r.random() < 0.09 + 0.12 * pt.frailty:
                enc.deteriorate_at = h + r.uniform(10, min(60, los * 0.7))
                enc.end += r.uniform(30, 72)
                enc.expires = r.random() < 0.05 + 0.08 * pt.frailty
            self._order_admission_meds(enc, h, hour_len, out)
        self.active[visit_id] = enc
        out.append(self._event("encounter.admitted", at, pt, enc, {
            "visit_id": visit_id,
            "admission_type": enc.admission_type,
            "admit_source": enc.admit_source,
            "admitted_at": at.isoformat(),
        }))

    def _order_admission_meds(self, enc: ActiveEncounter, h: float, hour_len: timedelta, out: list) -> None:
        r = self.rng
        codes = list(C.CONDITIONS[enc.condition].meds) if enc.condition else []
        for cond in enc.patient.conditions:
            if cond != enc.condition and r.random() < 0.55:
                codes.append(r.choice(C.CONDITIONS[cond].meds))
        if enc.admission_type == "inpatient" and r.random() < 0.7:
            codes.append("enoxaparin")
        if r.random() < 0.6:
            codes.append("acetaminophen")
        if r.random() < 0.25:
            codes.append("ondansetron")
        seen = set()
        for code in codes:
            if code in seen:
                continue
            seen.add(code)
            self._order_med(enc, code, h + r.uniform(0.3, 1.5), hour_len, out)

    def _order_med(self, enc: ActiveEncounter, code: str, h: float, hour_len: timedelta, out: list) -> None:
        med = C.MEDS[code]
        order = MedOrder(f"RX{self.p.code}{self.tag}{next(self._order_counter):07d}", code,
                         None if med.interval_hours is None else h + 0.25)
        enc.meds.append(order)
        out.append(self._event("medication.ordered", self._ts(h, hour_len), enc.patient, enc, {
            "order_id": order.order_id, "code": med.code, "name": med.name, "drug_class": med.drug_class,
            "dose": med.dose, "dose_unit": med.dose_unit, "route": med.route, "frequency": med.frequency,
            "ordered_at": self._ts(h, hour_len).isoformat(),
        }))

    def _end_encounter(self, enc: ActiveEncounter, hour_len: timedelta, out: list) -> None:
        r, pt = self.rng, enc.patient
        at = self._ts(enc.end, hour_len)
        for order in enc.meds:
            out.append(self._event("medication.discontinued", at, pt, enc, {
                "order_id": order.order_id, "reason": "encounter_ended", "discontinued_at": at.isoformat(),
            }))
        if enc.admission_type == "emergency" and enc.ed_admit:
            disposition = "admitted"
        elif enc.admission_type in ("outpatient", "emergency"):
            disposition = "home" if r.random() < 0.97 else "left_against_advice"
        elif enc.expires:
            disposition = "expired"
        else:
            roll = r.random()
            snf = 0.05 + 0.25 * pt.frailty
            if roll < snf:
                disposition = "skilled_nursing"
            elif roll < snf + 0.12:
                disposition = "home_health"
            elif roll < snf + 0.15:
                disposition = "transfer"
            elif roll < snf + 0.17:
                disposition = "left_against_advice"
            else:
                disposition = "home"
        out.append(self._event("encounter.discharged", at, pt, enc, {
            "visit_id": enc.visit_id, "discharged_at": at.isoformat(), "disposition": disposition,
        }))
        del self.active[enc.visit_id]
        pt.busy = False
        if disposition == "expired":
            return  # nobody schedules another visit
        if enc.ed_admit:
            self._start_encounter(pt, enc.ed_admit, enc.end + 0.05, hour_len, out,
                                  source="emergency_department", condition=enc.condition)
            return
        if enc.admission_type in ("inpatient", "observation"):
            age = (self.now.date() - pt.date_of_birth).days / 365
            los = enc.end - enc.start
            # Readmission risk. Hidden frailty and chronic conditions matter, but so do things a
            # model can see in the record: age, earlier admissions, the stay, the discharge.
            logit = (-4.55 + 1.0 * pt.frailty
                     + sum(C.CONDITIONS[c].readmit_weight for c in pt.conditions) * 0.5
                     + 0.04 * (age - 60)
                     + 0.4 * min(pt.admissions - 1, 5)
                     + (0.45 if los < 36 else 0.0) + (0.4 if los > 8 * 24 else 0.0)
                     + (0.55 if enc.abnormal_labs >= 3 else 0.0)
                     + (0.45 if enc.admit_source == "emergency_department" else 0.0)
                     + (0.45 if disposition == "skilled_nursing" else 0.0)
                     + (0.8 if disposition == "left_against_advice" else 0.0)
                     + (0.3 if enc.condition in ("heart_failure", "copd") else 0.0))
            if r.random() < _sigmoid(logit):
                gap = min(29.0 * 24, max(20.0, r.expovariate(1 / (9 * 24))))
                self._schedule(enc.end + gap, r.choice(("ed", "ed", "direct")), pt)

    # ------------------------------------------------------------------ the clock
    def advance(self, hour_len: timedelta = timedelta(hours=1)) -> list[dict]:
        """Play one clinical hour and return its events, sorted by time."""
        r = self.rng
        out: list[dict] = []
        h_end = self.t + 1.0

        # arrivals
        while self.heap and self.heap[0][0] < h_end:
            when, _, kind, idx = heapq.heappop(self.heap)
            pt = self.pool[idx]
            if pt.busy:
                self._schedule(when + r.uniform(24, 24 * 14), kind, pt)
                continue
            if kind == "outpatient":
                local_hour = (self._ts(max(when, self.t), hour_len).hour - 5) % 24  # clinics keep office hours
                if not 8 <= local_hour < 17:
                    self._schedule(when + (8 - local_hour) % 24 + r.uniform(0, 9), "outpatient", pt)
                    continue
                self._start_encounter(pt, "outpatient", max(when, self.t), hour_len, out)
                self._schedule(when + r.expovariate(self.p.outpatient_per_year * (0.6 + 0.35 * self._burden(pt)) / HOURS_PER_YEAR), "outpatient", pt)
            elif kind == "ed":
                self._start_encounter(pt, "emergency", max(when, self.t), hour_len, out)
                self._schedule(when + r.expovariate(max(1e-3, self.p.ed_per_year * (0.5 + 0.45 * self._burden(pt)) * (1 + pt.frailty)) / HOURS_PER_YEAR), "ed", pt)
            else:
                self._start_encounter(pt, "inpatient", max(when, self.t), hour_len, out)
                self._schedule(when + r.expovariate(max(1e-3, self.p.direct_admit_per_year * (0.2 + 0.5 * self._burden(pt)) * (1 + pt.frailty)) / HOURS_PER_YEAR), "direct", pt)

        # everyone currently in an encounter
        for enc in list(self.active.values()):
            pt = enc.patient
            # vitals
            while enc.next_vitals < h_end and enc.next_vitals < enc.end:
                at = self._ts(enc.next_vitals, hour_len)
                out.append(self._event("vital_signs.recorded", at, pt, enc, self._vitals(enc, enc.next_vitals, at)))
                if enc.admission_type == "outpatient":
                    enc.next_vitals = float("inf")
                elif enc.admission_type == "emergency":
                    enc.next_vitals += 2.0
                else:
                    det = enc.deteriorate_at is not None and enc.next_vitals >= enc.deteriorate_at
                    enc.next_vitals += 2.0 if det else (4.0 if enc.next_vitals - enc.start < 24 else 8.0)
            # labs
            while enc.next_labs < h_end and enc.next_labs < enc.end:
                panels = self._panels_for(enc)
                collected = self._ts(enc.next_labs, hour_len)
                resulted = collected + timedelta(minutes=r.randint(35, 110)) * (hour_len / timedelta(hours=1))
                resulted = min(resulted, self.now + hour_len)  # never later than the hour being played
                for panel in panels:
                    out.append(self._event("lab.resulted", resulted, pt, enc,
                                           self._lab_panel(enc, panel, enc.next_labs, collected, resulted)))
                enc.lab_rounds += 1
                enc.next_labs = self._next_lab_time(enc)
            # medications
            for order in enc.meds:
                med = C.MEDS[order.code]
                if order.next_due is None:
                    if r.random() < 0.06:
                        at = self._ts(self.t + r.random(), hour_len)
                        out.append(self._administration(enc, order, at))
                    continue
                while order.next_due < h_end and order.next_due < enc.end:
                    at = self._ts(order.next_due, hour_len)
                    if r.random() > 0.03:  # a missed or held dose now and then
                        out.append(self._administration(enc, order, at))
                    order.next_due += max(1, med.interval_hours if med.code != "norepinephrine" else 4)
            # deterioration brings a vasopressor for septic patients
            if (enc.deteriorate_at is not None and enc.condition == "sepsis" and self.t <= enc.deteriorate_at + 8 < h_end
                    and not any(o.code == "norepinephrine" for o in enc.meds)):
                self._order_med(enc, "norepinephrine", enc.deteriorate_at + 8, hour_len, out)
            if enc.end < h_end:
                self._end_encounter(enc, hour_len, out)

        self.t = h_end
        self.now += hour_len
        out.sort(key=lambda e: e["occurred_at"])
        return out

    def _administration(self, enc: ActiveEncounter, order: MedOrder, at: datetime) -> dict:
        med = C.MEDS[order.code]
        return self._event("medication.administered", at, enc.patient, enc, {
            "order_id": order.order_id, "code": med.code, "dose": med.dose, "dose_unit": med.dose_unit,
            "route": med.route, "administered_at": at.isoformat(),
        })

    def _panels_for(self, enc: ActiveEncounter) -> list[str]:
        r = self.rng
        cond = C.CONDITIONS.get(enc.condition) if enc.condition else None
        if enc.admission_type == "outpatient":
            panels = []
            if r.random() < 0.35:
                panels.append("BMP")
            if "diabetes" in enc.patient.conditions and r.random() < 0.55:
                panels.append("HBA1C")
            if r.random() < 0.15:
                panels.append("CBC")
            return panels
        if enc.admission_type == "emergency" and r.random() > 0.72:
            return []
        if enc.lab_rounds == 0:
            return ["BMP", "CBC", *(cond.extra_panels if cond else ())]
        panels = ["BMP", "CBC"]
        if cond and "LACTATE" in cond.extra_panels and (enc.lab_rounds < 3 or enc.deteriorate_at is not None):
            panels.append("LACTATE")
        if cond and "TROPONIN" in cond.extra_panels and enc.lab_rounds < 3:
            panels.append("TROPONIN")
        if cond and "CRP" in cond.extra_panels and enc.lab_rounds % 2 == 0:
            panels.append("CRP")
        return panels

    def _next_lab_time(self, enc: ActiveEncounter) -> float:
        if enc.admission_type == "outpatient":
            return float("inf")
        if enc.admission_type == "emergency":
            return enc.start + 4.0 if enc.lab_rounds < 2 else float("inf")
        cond = enc.condition
        if (cond in ("sepsis", "acs") and enc.lab_rounds < 3) or (enc.deteriorate_at is not None and enc.next_labs >= enc.deteriorate_at):
            return enc.next_labs + 6.0
        # one routine draw a day, about 20 hours after admission and every 24 hours after that
        day = math.floor((enc.next_labs - enc.start - 20.0 + 12.0) / 24.0) + 1
        return enc.start + 20.0 + 24.0 * max(0, day) + self.rng.uniform(-1.0, 1.0)


def age_on(dob: date, when: date) -> int:
    return when.year - dob.year - ((when.month, when.day) < (dob.month, dob.day))
