"""Reference data the simulator draws from: vital signs, lab tests, conditions, medications.

Everything here is synthetic but clinically plausible. Reference ranges are typical adult
ranges; they are good enough to make "abnormal" mean something, not good enough to treat
anyone.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VitalDef:
    code: str
    label: str
    unit: str
    mean: float
    sd: float
    low: float  # physiological floor, values are clipped here
    high: float  # physiological ceiling
    decimals: int = 0


VITALS: dict[str, VitalDef] = {
    v.code: v
    for v in [
        VitalDef("heart_rate", "Heart rate", "bpm", 78, 11, 30, 190),
        VitalDef("systolic_bp", "Systolic blood pressure", "mmHg", 124, 15, 60, 230),
        VitalDef("diastolic_bp", "Diastolic blood pressure", "mmHg", 76, 9, 30, 140),
        VitalDef("respiratory_rate", "Respiratory rate", "breaths/min", 16, 2.5, 6, 45),
        VitalDef("temperature", "Temperature", "°C", 36.8, 0.35, 33.0, 42.0, decimals=1),
        VitalDef("spo2", "Oxygen saturation", "%", 97, 1.4, 60, 100),
    ]
}


@dataclass(frozen=True)
class LabDef:
    code: str
    loinc: str
    label: str
    unit: str
    ref_low: float
    ref_high: float
    mean: float
    sd: float
    floor: float
    decimals: int
    panel: str


LABS: dict[str, LabDef] = {
    l.code: l
    for l in [
        # Basic metabolic panel
        LabDef("sodium", "2951-2", "Sodium", "mmol/L", 135, 145, 139, 2.5, 110, 0, "BMP"),
        LabDef("potassium", "2823-3", "Potassium", "mmol/L", 3.5, 5.1, 4.2, 0.35, 2.0, 1, "BMP"),
        LabDef("chloride", "2075-0", "Chloride", "mmol/L", 98, 107, 102, 2.5, 80, 0, "BMP"),
        LabDef("bicarbonate", "1963-8", "Bicarbonate", "mmol/L", 22, 29, 25, 2.0, 8, 0, "BMP"),
        LabDef("bun", "3094-0", "Blood urea nitrogen", "mg/dL", 7, 20, 14, 4.0, 2, 0, "BMP"),
        LabDef("creatinine", "2160-0", "Creatinine", "mg/dL", 0.6, 1.3, 0.95, 0.18, 0.3, 2, "BMP"),
        LabDef("glucose", "2345-7", "Glucose", "mg/dL", 70, 99, 96, 12, 35, 0, "BMP"),
        # Complete blood count
        LabDef("wbc", "6690-2", "White blood cells", "10^3/uL", 4.5, 11.0, 7.2, 1.6, 0.5, 1, "CBC"),
        LabDef("hemoglobin", "718-7", "Hemoglobin", "g/dL", 12.0, 17.5, 13.8, 1.3, 5.0, 1, "CBC"),
        LabDef("platelets", "777-3", "Platelets", "10^3/uL", 150, 400, 250, 50, 10, 0, "CBC"),
        # Condition-specific tests
        LabDef("lactate", "2524-7", "Lactate", "mmol/L", 0.5, 2.2, 1.2, 0.35, 0.3, 1, "LACTATE"),
        LabDef("troponin_i", "10839-9", "Troponin I", "ng/mL", 0.0, 0.04, 0.012, 0.006, 0.0, 3, "TROPONIN"),
        LabDef("bnp", "30934-4", "B-type natriuretic peptide", "pg/mL", 0, 100, 45, 20, 5, 0, "BNP"),
        LabDef("hba1c", "4548-4", "Hemoglobin A1c", "%", 4.0, 5.6, 5.3, 0.3, 3.5, 1, "HBA1C"),
        LabDef("crp", "1988-5", "C-reactive protein", "mg/L", 0, 10, 3.5, 2.0, 0.1, 1, "CRP"),
    ]
}

PANELS: dict[str, list[str]] = {}
for _lab in LABS.values():
    PANELS.setdefault(_lab.panel, []).append(_lab.code)

PANEL_NAMES = {
    "BMP": "Basic metabolic panel",
    "CBC": "Complete blood count",
    "LACTATE": "Lactate",
    "TROPONIN": "Troponin I",
    "BNP": "BNP",
    "HBA1C": "Hemoglobin A1c",
    "CRP": "C-reactive protein",
}


@dataclass(frozen=True)
class MedDef:
    code: str
    name: str
    drug_class: str
    dose: float
    dose_unit: str
    route: str
    frequency: str  # human-readable, e.g. "q8h"
    interval_hours: int | None  # None = PRN (given occasionally)


MEDS: dict[str, MedDef] = {
    m.code: m
    for m in [
        MedDef("furosemide", "Furosemide", "Loop diuretic", 40, "mg", "IV", "q12h", 12),
        MedDef("carvedilol", "Carvedilol", "Beta blocker", 6.25, "mg", "PO", "q12h", 12),
        MedDef("lisinopril", "Lisinopril", "ACE inhibitor", 10, "mg", "PO", "daily", 24),
        MedDef("albuterol", "Albuterol", "Bronchodilator", 2.5, "mg", "Nebulized", "q6h", 6),
        MedDef("prednisone", "Prednisone", "Corticosteroid", 40, "mg", "PO", "daily", 24),
        MedDef("tiotropium", "Tiotropium", "Anticholinergic bronchodilator", 18, "mcg", "Inhaled", "daily", 24),
        MedDef("insulin_glargine", "Insulin glargine", "Long-acting insulin", 20, "units", "SubQ", "daily", 24),
        MedDef("insulin_lispro", "Insulin lispro", "Rapid-acting insulin", 4, "units", "SubQ", "with meals", 8),
        MedDef("metformin", "Metformin", "Biguanide", 500, "mg", "PO", "q12h", 12),
        MedDef("sodium_bicarbonate", "Sodium bicarbonate", "Alkalinizing agent", 650, "mg", "PO", "q12h", 12),
        MedDef("ceftriaxone", "Ceftriaxone", "Cephalosporin antibiotic", 1, "g", "IV", "daily", 24),
        MedDef("azithromycin", "Azithromycin", "Macrolide antibiotic", 500, "mg", "IV", "daily", 24),
        MedDef("piperacillin_tazobactam", "Piperacillin-tazobactam", "Penicillin antibiotic", 4.5, "g", "IV", "q6h", 6),
        MedDef("vancomycin", "Vancomycin", "Glycopeptide antibiotic", 1250, "mg", "IV", "q12h", 12),
        MedDef("norepinephrine", "Norepinephrine", "Vasopressor", 5, "mcg/min", "IV infusion", "continuous", 1),
        MedDef("aspirin", "Aspirin", "Antiplatelet", 81, "mg", "PO", "daily", 24),
        MedDef("atorvastatin", "Atorvastatin", "Statin", 80, "mg", "PO", "daily", 24),
        MedDef("heparin", "Heparin", "Anticoagulant", 5000, "units", "SubQ", "q8h", 8),
        MedDef("amlodipine", "Amlodipine", "Calcium channel blocker", 5, "mg", "PO", "daily", 24),
        MedDef("enoxaparin", "Enoxaparin", "Anticoagulant", 40, "mg", "SubQ", "daily", 24),
        MedDef("acetaminophen", "Acetaminophen", "Analgesic", 650, "mg", "PO", "q6h PRN", None),
        MedDef("ondansetron", "Ondansetron", "Antiemetic", 4, "mg", "IV", "q8h PRN", None),
        MedDef("normal_saline", "Sodium chloride 0.9%", "IV fluid", 1000, "mL", "IV", "q8h", 8),
    ]
}


@dataclass(frozen=True)
class ConditionDef:
    code: str
    label: str
    prevalence: float  # share of the population carrying it
    readmit_weight: float  # adds to the readmission log-odds when present
    admit_weight: float  # multiplies the chance an ED visit turns into an admission
    vital_shift: dict[str, float] = field(default_factory=dict)  # added to baselines when acute
    lab_shift: dict[str, float] = field(default_factory=dict)  # multiplied onto the mean when acute
    extra_panels: tuple[str, ...] = ()  # tests ordered when this condition drives an admission
    meds: tuple[str, ...] = ()
    los_days: float = 4.0  # mean length of stay when this condition drives an admission


CONDITIONS: dict[str, ConditionDef] = {
    c.code: c
    for c in [
        ConditionDef(
            "heart_failure", "Heart failure", 0.12, 0.55, 1.6,
            vital_shift={"heart_rate": 8, "respiratory_rate": 3, "spo2": -3},
            lab_shift={"bnp": 14.0, "sodium": 0.97, "creatinine": 1.3, "bun": 1.5},
            extra_panels=("BNP",), meds=("furosemide", "carvedilol", "lisinopril"), los_days=5.0,
        ),
        ConditionDef(
            "copd", "Chronic obstructive pulmonary disease", 0.10, 0.45, 1.4,
            vital_shift={"respiratory_rate": 5, "spo2": -5, "heart_rate": 10},
            lab_shift={"bicarbonate": 1.15, "wbc": 1.2},
            meds=("albuterol", "prednisone", "tiotropium"), los_days=4.0,
        ),
        ConditionDef(
            "diabetes", "Type 2 diabetes", 0.20, 0.30, 1.1,
            vital_shift={},
            lab_shift={"glucose": 2.1, "hba1c": 1.55},
            extra_panels=("HBA1C",), meds=("insulin_glargine", "insulin_lispro", "metformin"), los_days=3.5,
        ),
        ConditionDef(
            "ckd", "Chronic kidney disease", 0.09, 0.50, 1.3,
            lab_shift={"creatinine": 2.6, "bun": 2.4, "potassium": 1.15, "bicarbonate": 0.85, "hemoglobin": 0.85},
            meds=("sodium_bicarbonate", "amlodipine"), los_days=4.5,
        ),
        ConditionDef(
            "pneumonia", "Pneumonia", 0.05, 0.25, 1.8,
            vital_shift={"temperature": 1.3, "heart_rate": 14, "respiratory_rate": 5, "spo2": -4},
            lab_shift={"wbc": 1.9, "crp": 18.0},
            extra_panels=("CRP",), meds=("ceftriaxone", "azithromycin"), los_days=4.5,
        ),
        ConditionDef(
            "sepsis", "Sepsis", 0.02, 0.60, 3.0,
            vital_shift={"temperature": 1.6, "heart_rate": 26, "respiratory_rate": 7, "systolic_bp": -22, "diastolic_bp": -12, "spo2": -3},
            lab_shift={"wbc": 2.3, "lactate": 2.6, "crp": 25.0, "creatinine": 1.5, "platelets": 0.7},
            extra_panels=("LACTATE", "CRP"), meds=("piperacillin_tazobactam", "vancomycin", "normal_saline"), los_days=7.0,
        ),
        ConditionDef(
            "acs", "Acute coronary syndrome", 0.04, 0.40, 2.2,
            vital_shift={"heart_rate": 12, "systolic_bp": 14},
            lab_shift={"troponin_i": 45.0},
            extra_panels=("TROPONIN",), meds=("aspirin", "atorvastatin", "heparin"), los_days=3.5,
        ),
        ConditionDef(
            "hypertension", "Hypertension", 0.32, 0.10, 0.9,
            vital_shift={"systolic_bp": 24, "diastolic_bp": 12},
            meds=("amlodipine", "lisinopril"), los_days=2.5,
        ),
    ]
}

ADMISSION_TYPES = ("inpatient", "observation", "emergency", "outpatient")
ADMIT_SOURCES = ("emergency_department", "physician_referral", "clinic", "transfer", "self_referral")
DISCHARGE_DISPOSITIONS = ("home", "home_health", "skilled_nursing", "transfer", "left_against_advice", "expired")

FIRST_NAMES_F = [
    "Ava", "Maya", "Zoe", "Lena", "Priya", "Grace", "Nora", "Iris", "Hana", "Leila", "Rosa", "Clara",
    "Amara", "Ines", "Tessa", "Yara", "Mei", "Freya", "Sofia", "Aisha", "Daria", "Elena", "June", "Kira",
    "Lucia", "Mira", "Naomi", "Olive", "Pilar", "Ruth", "Sana", "Tara", "Uma", "Vera", "Wren", "Ximena",
]
FIRST_NAMES_M = [
    "Liam", "Noah", "Omar", "Ravi", "Mateo", "Ezra", "Felix", "Hugo", "Ian", "Jonah", "Kenji", "Leo",
    "Milo", "Nikhil", "Oscar", "Pablo", "Quinn", "Rafael", "Samir", "Theo", "Umar", "Victor", "Wes", "Yusuf",
    "Andre", "Bruno", "Caleb", "Dmitri", "Elias", "Farid", "Gabe", "Hector", "Isaac", "Jalen", "Kofi", "Luca",
]
LAST_NAMES = [
    "Abbott", "Alvarez", "Banerjee", "Barros", "Castellan", "Chen", "Dalton", "Deschamps", "Ebert", "Farrow",
    "Fontaine", "Garza", "Haddad", "Holloway", "Ibarra", "Iverson", "Jansen", "Kaur", "Keller", "Lindqvist",
    "Lowe", "Mendez", "Moreau", "Nakamura", "Novak", "Okafor", "Oyelaran", "Pereira", "Quill", "Ramirez",
    "Rahman", "Sato", "Sorensen", "Tanaka", "Thornton", "Umeh", "Valdez", "Varga", "Whitfield", "Winslow",
    "Xu", "Yamamoto", "Yilmaz", "Zamora", "Zieliński", "Brennan", "Castillo", "Dubois", "Eriksen", "Fischer",
]
