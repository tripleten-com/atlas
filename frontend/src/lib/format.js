// Formatting and labels shared by every page.

const dateFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });
const shortDateFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const numberFmt = new Intl.NumberFormat();

const toDate = (value) => (value instanceof Date ? value : new Date(value));

export function formatDate(value) {
  return value ? dateFmt.format(toDate(value)) : "—";
}

export function formatShortDate(value) {
  return value ? shortDateFmt.format(toDate(value)) : "—";
}

export function formatTime(value) {
  return value ? timeFmt.format(toDate(value)) : "—";
}

export function formatDateTime(value) {
  return value ? dateTimeFmt.format(toDate(value)) : "—";
}

export function formatRelative(value, now = Date.now()) {
  if (!value) return "never";
  const seconds = Math.round((now - toDate(value).getTime()) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return formatDate(value);
}

export function formatNumber(value, { decimals } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (decimals !== undefined) return Number(value).toFixed(decimals);
  return numberFmt.format(value);
}

export function formatPercent(value, decimals = 1) {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(decimals)}%`;
}

export function formatDuration(hours) {
  if (hours === null || hours === undefined) return "—";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${hours.toFixed(hours < 10 ? 1 : 0)} h`;
  return `${(hours / 24).toFixed(1)} d`;
}

export function humanize(value) {
  if (!value) return "—";
  const text = String(value).replace(/[_.]/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function initials(name = "") {
  return name
    .replace(/^Dr\.?\s+/i, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

export const ENCOUNTER_TYPES = {
  inpatient: { label: "Inpatient", tone: "brand" },
  observation: { label: "Observation", tone: "warn" },
  emergency: { label: "Emergency", tone: "high" },
  outpatient: { label: "Outpatient", tone: "neutral" },
  not_admitted: { label: "Not admitted", tone: "neutral" },
};

export const EVENT_TYPES = {
  "patient.registered": { label: "Registration", tone: "neutral" },
  "encounter.admitted": { label: "Admission", tone: "brand" },
  "encounter.discharged": { label: "Discharge", tone: "brand" },
  "vital_signs.recorded": { label: "Vitals", tone: "ok" },
  "lab.resulted": { label: "Lab result", tone: "low" },
  "medication.ordered": { label: "Med order", tone: "warn" },
  "medication.administered": { label: "Med given", tone: "warn" },
  "medication.discontinued": { label: "Med stopped", tone: "neutral" },
};

export const INGEST_STATUS = {
  processed: { label: "Processed", tone: "ok" },
  pending: { label: "Pending", tone: "neutral" },
  deferred: { label: "Deferred", tone: "warn" },
  failed: { label: "Failed", tone: "high" },
  skipped: { label: "Skipped", tone: "neutral" },
};

export const VITAL_ORDER = ["heart_rate", "systolic_bp", "diastolic_bp", "respiratory_rate", "temperature", "spo2"];

export const VITAL_SHORT = {
  heart_rate: "Heart rate",
  systolic_bp: "Systolic BP",
  diastolic_bp: "Diastolic BP",
  respiratory_rate: "Resp. rate",
  temperature: "Temperature",
  spo2: "SpO₂",
};

export function patientSubtitle(patient) {
  if (!patient) return "";
  return `${patient.age} y · ${humanize(patient.sex)} · MRN ${patient.mrn}`;
}
