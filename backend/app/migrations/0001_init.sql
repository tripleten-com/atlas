-- Atlas schema, version 1.
-- Every clinical row carries tenant_id. Every query that reads clinical data filters on it.

CREATE TABLE tenants (
    id          SERIAL PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE facilities (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    code        TEXT NOT NULL UNIQUE,              -- the code the Meridian feed uses, e.g. RGH
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,                     -- hospital, clinic or demo
    timezone    TEXT NOT NULL DEFAULT 'America/New_York',
    data_source TEXT NOT NULL,                     -- seed or meridian
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX facilities_tenant_idx ON facilities (tenant_id);

CREATE TABLE users (
    id             SERIAL PRIMARY KEY,
    tenant_id      INTEGER NOT NULL REFERENCES tenants(id),
    email          TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('admin', 'clinician')),
    title          TEXT,
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);

CREATE TABLE patients (
    id             SERIAL PRIMARY KEY,
    tenant_id      INTEGER NOT NULL REFERENCES tenants(id),
    facility_id    INTEGER NOT NULL REFERENCES facilities(id),
    mrn            TEXT NOT NULL,                  -- medical record number, unique within a facility
    name           TEXT NOT NULL,                  -- display name, First Last
    first_name     TEXT NOT NULL,
    last_name      TEXT NOT NULL,
    sex            TEXT NOT NULL CHECK (sex IN ('female', 'male')),
    date_of_birth  DATE NOT NULL,
    age            INTEGER NOT NULL,               -- age in years when the patient was registered
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (facility_id, mrn)
);
CREATE INDEX patients_tenant_facility_idx ON patients (tenant_id, facility_id);
CREATE INDEX patients_name_idx ON patients (lower(name));

CREATE TABLE encounters (
    id                     SERIAL PRIMARY KEY,
    tenant_id              INTEGER NOT NULL REFERENCES tenants(id),
    facility_id            INTEGER NOT NULL REFERENCES facilities(id),
    patient_id             INTEGER NOT NULL REFERENCES patients(id),
    external_id            TEXT NOT NULL,          -- the visit id the source system uses
    admission_type         TEXT NOT NULL CHECK (admission_type IN ('inpatient', 'observation', 'emergency', 'outpatient')),
    admit_source           TEXT NOT NULL,
    admit_date             TIMESTAMPTZ NOT NULL,
    discharge_date         TIMESTAMPTZ,            -- NULL while the patient is still here
    discharge_disposition  TEXT,
    UNIQUE (facility_id, external_id)
);
CREATE INDEX encounters_patient_idx ON encounters (patient_id, admit_date);
CREATE INDEX encounters_tenant_facility_idx ON encounters (tenant_id, facility_id, admit_date);
CREATE INDEX encounters_open_idx ON encounters (tenant_id, facility_id) WHERE discharge_date IS NULL;

CREATE TABLE vitals (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        INTEGER NOT NULL REFERENCES tenants(id),
    facility_id      INTEGER NOT NULL REFERENCES facilities(id),
    patient_id       INTEGER NOT NULL REFERENCES patients(id),
    encounter_id     INTEGER REFERENCES encounters(id),
    measurement      TEXT NOT NULL,                -- heart_rate, systolic_bp, diastolic_bp, respiratory_rate, temperature, spo2
    value            DOUBLE PRECISION NOT NULL,
    unit             TEXT NOT NULL,
    recorded_at      TIMESTAMPTZ NOT NULL,
    source_event_id  BIGINT
);
CREATE INDEX vitals_patient_idx ON vitals (patient_id, measurement, recorded_at);
CREATE INDEX vitals_encounter_idx ON vitals (encounter_id);
CREATE INDEX vitals_facility_time_idx ON vitals (facility_id, recorded_at);

CREATE TABLE labs (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        INTEGER NOT NULL REFERENCES tenants(id),
    facility_id      INTEGER NOT NULL REFERENCES facilities(id),
    patient_id       INTEGER NOT NULL REFERENCES patients(id),
    encounter_id     INTEGER REFERENCES encounters(id),
    test_code        TEXT NOT NULL,                -- sodium, creatinine, wbc, lactate and so on
    loinc            TEXT NOT NULL,
    test_name        TEXT NOT NULL,
    panel            TEXT NOT NULL,                -- BMP, CBC, LACTATE, TROPONIN, BNP, HBA1C, CRP
    value            DOUBLE PRECISION NOT NULL,
    unit             TEXT NOT NULL,
    ref_low          DOUBLE PRECISION,
    ref_high         DOUBLE PRECISION,
    flag             TEXT NOT NULL CHECK (flag IN ('L', 'N', 'H')),
    collected_at     TIMESTAMPTZ NOT NULL,
    resulted_at      TIMESTAMPTZ NOT NULL,
    source_event_id  BIGINT
);
CREATE INDEX labs_patient_idx ON labs (patient_id, test_code, collected_at);
CREATE INDEX labs_encounter_idx ON labs (encounter_id);
CREATE INDEX labs_facility_time_idx ON labs (facility_id, resulted_at);
CREATE INDEX labs_abnormal_idx ON labs (facility_id, resulted_at) WHERE flag <> 'N';

CREATE TABLE medications (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    facility_id   INTEGER NOT NULL REFERENCES facilities(id),
    patient_id    INTEGER NOT NULL REFERENCES patients(id),
    encounter_id  INTEGER REFERENCES encounters(id),
    external_id   TEXT NOT NULL,                   -- the order id the source system uses
    drug_code     TEXT NOT NULL,
    drug_name     TEXT NOT NULL,
    drug_class    TEXT NOT NULL,
    dose          DOUBLE PRECISION NOT NULL,
    dose_unit     TEXT NOT NULL,
    route         TEXT NOT NULL,
    frequency     TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('active', 'discontinued')),
    ordered_at    TIMESTAMPTZ NOT NULL,
    ended_at      TIMESTAMPTZ,
    UNIQUE (tenant_id, external_id)
);
CREATE INDEX medications_patient_idx ON medications (patient_id, ordered_at);
CREATE INDEX medications_encounter_idx ON medications (encounter_id);

CREATE TABLE medication_administrations (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        INTEGER NOT NULL REFERENCES tenants(id),
    facility_id      INTEGER NOT NULL REFERENCES facilities(id),
    medication_id    INTEGER NOT NULL REFERENCES medications(id),
    patient_id       INTEGER NOT NULL REFERENCES patients(id),
    encounter_id     INTEGER REFERENCES encounters(id),
    administered_at  TIMESTAMPTZ NOT NULL,
    dose             DOUBLE PRECISION NOT NULL,
    dose_unit        TEXT NOT NULL,
    source_event_id  BIGINT
);
CREATE INDEX med_admin_medication_idx ON medication_administrations (medication_id, administered_at);
CREATE INDEX med_admin_patient_idx ON medication_administrations (patient_id, administered_at);
CREATE INDEX med_admin_facility_time_idx ON medication_administrations (facility_id, administered_at);

-- ---------------------------------------------------------------- data ingestion

CREATE TABLE integration_connections (
    id                SERIAL PRIMARY KEY,
    tenant_id         INTEGER NOT NULL REFERENCES tenants(id),
    facility_id       INTEGER NOT NULL UNIQUE REFERENCES facilities(id),
    vendor            TEXT NOT NULL DEFAULT 'meridian',
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'error')),
    subscription_id   TEXT,                        -- assigned by Meridian when we subscribe
    webhook_secret    TEXT NOT NULL,               -- shared secret Meridian signs deliveries with
    stream_epoch      TEXT,                        -- changes when Meridian's event log is reset
    high_watermark    BIGINT NOT NULL DEFAULT 0,   -- highest sequence received with no gap below it
    last_event_at     TIMESTAMPTZ,
    last_delivery_at  TIMESTAMPTZ,
    last_resync_at    TIMESTAMPTZ,
    needs_resync      BOOLEAN NOT NULL DEFAULT false,
    last_error        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingest_events (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    connection_id   INTEGER NOT NULL REFERENCES integration_connections(id),
    external_id     TEXT NOT NULL,                 -- Meridian's event id, the idempotency key
    epoch           TEXT NOT NULL,                 -- the Meridian log this sequence belongs to
    sequence        BIGINT NOT NULL,
    event_type      TEXT NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    received_via    TEXT NOT NULL CHECK (received_via IN ('webhook', 'resync')),
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processed', 'deferred', 'failed', 'skipped')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ,
    error           TEXT,
    UNIQUE (connection_id, external_id)
);
CREATE INDEX ingest_events_work_idx ON ingest_events (next_attempt_at) WHERE status IN ('pending', 'deferred');
CREATE INDEX ingest_events_connection_idx ON ingest_events (connection_id, received_at DESC);
CREATE INDEX ingest_events_sequence_idx ON ingest_events (connection_id, epoch, sequence);

-- ---------------------------------------------------------------- SaaS plumbing

CREATE TABLE usage_events (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id      INTEGER NOT NULL REFERENCES tenants(id),
    user_id        INTEGER REFERENCES users(id),
    feature        TEXT NOT NULL,
    model          TEXT,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    predictions    INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL,                  -- ok, timeout, error or cache_hit
    request_id     TEXT,
    elapsed_ms     INTEGER
);
CREATE INDEX usage_events_tenant_ts_idx ON usage_events (tenant_id, ts);

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    user_id     INTEGER REFERENCES users(id),
    action      TEXT NOT NULL,                     -- login, patient.view, integration.resync and so on
    target      TEXT,
    detail      JSONB
);
CREATE INDEX audit_log_tenant_ts_idx ON audit_log (tenant_id, ts DESC);
