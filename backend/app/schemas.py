"""Request and response shapes. These are what /docs shows and what the frontend relies on."""
from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------- identity

class LoginRequest(BaseModel):
    email: str = Field(..., examples=["clinician@riverside.example"])
    password: str = Field(..., examples=["atlas-demo"])


class TenantOut(BaseModel):
    id: int
    slug: str
    name: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: Literal["admin", "clinician"]
    title: str | None = None
    tenant: TenantOut
    last_login_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserOut


# ---------------------------------------------------------------- facilities

class FacilityOut(BaseModel):
    id: int
    code: str
    name: str
    kind: Literal["hospital", "clinic", "demo"]
    timezone: str
    data_source: Literal["seed", "meridian"]
    patient_count: int
    active_encounters: int
    feed_status: str | None = Field(None, description="Status of the Meridian connection; null for seeded facilities")
    last_event_at: datetime | None = None


# ---------------------------------------------------------------- clinical

EncounterType = Literal["inpatient", "observation", "emergency", "outpatient"]


class EncounterOut(BaseModel):
    id: int
    patient_id: int
    facility_id: int
    external_id: str
    admission_type: EncounterType
    admit_source: str
    admit_date: datetime
    discharge_date: datetime | None
    discharge_disposition: str | None
    is_active: bool
    length_of_stay_hours: float | None = Field(None, description="Hours from admission to discharge, or to now while active")


class EncounterListItem(EncounterOut):
    patient_name: str
    patient_mrn: str
    facility_name: str


class PatientSummary(BaseModel):
    id: int
    mrn: str
    name: str
    first_name: str
    last_name: str
    sex: Literal["female", "male"]
    age: int
    date_of_birth: date
    facility_id: int
    facility_name: str
    status: Literal["inpatient", "observation", "emergency", "outpatient", "not_admitted"] = Field(
        ..., description="Type of the encounter the patient is in right now, or not_admitted"
    )
    current_encounter_id: int | None
    last_encounter_at: datetime | None
    encounter_count: int


class VitalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    encounter_id: int | None
    measurement: str
    label: str
    value: float
    unit: str
    recorded_at: datetime


class LatestVital(VitalOut):
    previous_value: float | None = None
    status: Literal["low", "normal", "high"] = "normal"


class LabOut(BaseModel):
    id: int
    encounter_id: int | None
    test_code: str
    test_name: str
    loinc: str
    panel: str
    value: float
    unit: str
    ref_low: float | None
    ref_high: float | None
    flag: Literal["L", "N", "H"]
    collected_at: datetime
    resulted_at: datetime


class MedicationOut(BaseModel):
    id: int
    encounter_id: int | None
    external_id: str
    drug_code: str
    drug_name: str
    drug_class: str
    dose: float
    dose_unit: str
    route: str
    frequency: str
    status: Literal["active", "discontinued"]
    ordered_at: datetime
    ended_at: datetime | None
    administrations: int
    last_administered_at: datetime | None


class PatientDetail(PatientSummary):
    current_encounter: EncounterOut | None
    latest_vitals: list[LatestVital]
    active_medications: int
    abnormal_labs_7d: int


class TimelineItem(BaseModel):
    at: datetime
    kind: Literal["admission", "discharge", "vitals", "lab", "medication_order", "medication_given"]
    title: str
    detail: str | None = None
    encounter_id: int | None = None
    abnormal: bool = False


class LabResultRow(LabOut):
    patient_id: int
    patient_name: str
    patient_mrn: str
    facility_name: str


# ---------------------------------------------------------------- dashboard

class Census(BaseModel):
    inpatient: int
    observation: int
    emergency: int
    outpatient: int


class DashboardSummary(BaseModel):
    as_of: datetime = Field(..., description="The newest data point in scope; KPIs are computed relative to it")
    patients_total: int
    census: Census
    admissions_24h: int
    discharges_24h: int
    abnormal_labs_24h: int
    vitals_24h: int
    avg_length_of_stay_days_30d: float | None
    readmission_rate_30d: float | None = Field(
        None, description="Share of inpatient stays discharged 30-60 days ago followed by another inpatient stay within 30 days"
    )
    median_age: float | None


class DailyPoint(BaseModel):
    day: date
    admissions: int
    discharges: int
    emergency_visits: int
    outpatient_visits: int


class CountItem(BaseModel):
    key: str
    label: str
    count: int


class DashboardBreakdown(BaseModel):
    admission_types: list[CountItem]
    admit_sources: list[CountItem]
    age_bands: list[CountItem]
    abnormal_tests_7d: list[CountItem]
    dispositions_30d: list[CountItem]


# ---------------------------------------------------------------- integrations

class ConnectionOut(BaseModel):
    id: int
    facility_id: int
    facility_code: str
    facility_name: str
    vendor: str
    status: Literal["active", "paused", "error"]
    subscription_id: str | None
    stream_epoch: str | None
    high_watermark: int
    last_event_at: datetime | None
    last_delivery_at: datetime | None
    last_resync_at: datetime | None
    needs_resync: bool
    last_error: str | None
    events_24h: int
    pending: int
    deferred: int
    failed: int


class IngestEventOut(BaseModel):
    id: int
    external_id: str
    sequence: int
    event_type: str
    occurred_at: datetime
    received_at: datetime
    received_via: str
    status: str
    attempts: int
    processed_at: datetime | None
    error: str | None
    summary: str
    payload: dict | None = None


class HourlyVolume(BaseModel):
    hour: datetime
    event_type: str
    count: int


class ActivityItem(BaseModel):
    id: int
    at: datetime
    event_type: str
    summary: str
    facility_id: int
    facility_name: str
    patient_id: int | None
    patient_name: str | None
    abnormal: bool = False


# ---------------------------------------------------------------- usage and org

class UsageSummary(BaseModel):
    days: int
    total_events: int
    total_input_tokens: int
    total_output_tokens: int
    total_predictions: int
    estimated_cost_usd: float
    by_feature: list[dict]
    by_day: list[dict]
    recent: list[dict]


class OrgOut(BaseModel):
    tenant: TenantOut
    facilities: list[FacilityOut]
    users: list[UserOut]


class AuditItem(BaseModel):
    id: int
    ts: datetime
    user_name: str | None
    action: str
    target: str | None
    detail: dict | None
