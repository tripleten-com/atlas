"""Display reference data: labels, units and adult normal ranges for vital signs.

Lab reference ranges travel with each result (ref_low / ref_high / flag), so only vitals need
a table here. The ranges are typical adult values for display, not clinical decision rules.
"""
from __future__ import annotations

from clinsim.catalog import LABS, MEDS, VITALS

VITAL_LABELS = {code: v.label for code, v in VITALS.items()}

VITAL_NORMAL = {
    "heart_rate": (60, 100),
    "systolic_bp": (90, 140),
    "diastolic_bp": (60, 90),
    "respiratory_rate": (12, 20),
    "temperature": (36.1, 38.0),
    "spo2": (94, 100),
}

LAB_LABELS = {code: lab.label for code, lab in LABS.items()}


def vital_status(measurement: str, value: float) -> str:
    low, high = VITAL_NORMAL.get(measurement, (float("-inf"), float("inf")))
    if value < low:
        return "low"
    if value > high:
        return "high"
    return "normal"


def reference_payload() -> dict:
    """Everything the frontend needs to label and colour clinical values."""
    return {
        "vitals": [
            {"code": code, "label": v.label, "unit": v.unit, "normal_low": VITAL_NORMAL[code][0], "normal_high": VITAL_NORMAL[code][1]}
            for code, v in VITALS.items()
        ],
        "labs": [
            {"code": code, "label": lab.label, "unit": lab.unit, "loinc": lab.loinc, "panel": lab.panel,
             "ref_low": lab.ref_low, "ref_high": lab.ref_high}
            for code, lab in LABS.items()
        ],
        "medications": [
            {"code": code, "name": m.name, "drug_class": m.drug_class, "route": m.route, "frequency": m.frequency}
            for code, m in MEDS.items()
        ],
    }
