// Small pieces the patient-detail panels share: chart colours, the chart tooltip,
// an accessible chart frame, and cached reference ranges. No panel lives in this file.
import { useEffect, useState } from "react";
import { FlaskConical, HeartPulse, LogIn, LogOut, Pill, Syringe } from "lucide-react";
import { getReference } from "../api/client";
import { formatDateTime, formatNumber } from "../lib/format";

export const CHART = {
  brand: "#197b71",
  brandLight: "#45b9a8",
  info: "#2f63d8",
  critical: "#c42b2b",
  caution: "#b0740a",
  band: "#d4f4ec",
  bandOpacity: 0.5,
  tick: { fontSize: 11 },
};

// Reference data rarely changes, so every panel shares one request.
let referencePromise = null;
export function useReference() {
  const [reference, setReference] = useState(null);
  useEffect(() => {
    let cancelled = false;
    if (!referencePromise) {
      referencePromise = getReference().catch((err) => {
        referencePromise = null;
        throw err;
      });
    }
    referencePromise.then((data) => !cancelled && setReference(data)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  return reference;
}

export function vitalRanges(reference) {
  const ranges = {};
  (reference?.vitals || []).forEach((v) => {
    ranges[v.code] = v;
  });
  return ranges;
}

// "low" | "normal" | "high" for one reading, given { normal_low, normal_high }.
export function rangeStatus(value, low, high) {
  if (value === null || value === undefined) return "normal";
  if (low !== null && low !== undefined && value < low) return "low";
  if (high !== null && high !== undefined && value > high) return "high";
  return "normal";
}

export function formatValue(value) {
  if (value === null || value === undefined) return "—";
  const abs = Math.abs(value);
  if (abs !== 0 && abs < 1) return formatNumber(Number(value.toPrecision(3)));
  return formatNumber(Math.round(value * 10) / 10);
}

export function formatRange(low, high) {
  const hasLow = low !== null && low !== undefined;
  const hasHigh = high !== null && high !== undefined;
  if (hasLow && hasHigh) return `${formatValue(low)}–${formatValue(high)}`;
  if (hasHigh) return `≤ ${formatValue(high)}`;
  if (hasLow) return `≥ ${formatValue(low)}`;
  return "—";
}

export const PANEL_LABELS = {
  BMP: "BMP",
  CBC: "CBC",
  LACTATE: "Lactate",
  TROPONIN: "Troponin",
  BNP: "BNP",
  HBA1C: "HbA1c",
  CRP: "CRP",
};

export const panelLabel = (panel) => PANEL_LABELS[panel] || panel;

// Chart wrapper: screen readers get the text summary instead of the SVG.
export function ChartFrame({ label, summary, className, children }) {
  return (
    <figure className={className}>
      <div role="img" aria-label={`${label}. ${summary}`} className="h-full w-full">
        <div aria-hidden="true" className="h-full w-full">
          {children}
        </div>
      </div>
    </figure>
  );
}

// Custom Recharts tooltip: a small white card.
export function ChartTooltip({ active, payload, unit, labelFormatter = formatDateTime, statusOf }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-pop">
      <p className="mb-1 text-ink-500">{labelFormatter(point.t)}</p>
      {payload.map((entry) => {
        const status = statusOf ? statusOf(entry.dataKey, entry.value) : "normal";
        return (
          <p key={entry.dataKey} className="num flex items-center gap-2 text-ink-900">
            <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: entry.color }} />
            {entry.name && <span className="text-ink-500">{entry.name}</span>}
            <span className="font-semibold">
              {formatValue(entry.value)} {unit}
            </span>
            {status !== "normal" && (
              <span className="font-medium text-critical-700">{status === "high" ? "High" : "Low"}</span>
            )}
          </p>
        );
      })}
    </div>
  );
}

// Time axis tick: time of day for short spans, dates for long ones.
export function timeTickFormatter(spanMs) {
  const short = spanMs < 2 * 24 * 3600 * 1000;
  const fmt = new Intl.DateTimeFormat(undefined, short ? { hour: "numeric", minute: "2-digit" } : { month: "short", day: "numeric" });
  return (t) => fmt.format(new Date(t));
}

// Pretty-print the vitals detail string from the timeline ("heart_rate 91, spo2 97, ...").
const VITAL_ABBR = {
  heart_rate: "HR",
  respiratory_rate: "RR",
  temperature: "Temp",
  spo2: "SpO₂",
};
export function formatVitalsDetail(detail) {
  if (!detail) return detail;
  const values = {};
  detail.split(",").forEach((part) => {
    const [code, value] = part.trim().split(/\s+/);
    if (code && value) values[code] = value;
  });
  if (!Object.keys(values).length) return detail;
  const out = [];
  if (values.heart_rate) out.push(`HR ${values.heart_rate}`);
  if (values.systolic_bp || values.diastolic_bp) out.push(`BP ${values.systolic_bp ?? "—"}/${values.diastolic_bp ?? "—"}`);
  ["respiratory_rate", "temperature", "spo2"].forEach((code) => {
    if (values[code]) out.push(`${VITAL_ABBR[code]} ${values[code]}${code === "spo2" ? "%" : code === "temperature" ? " °C" : ""}`);
  });
  return out.join(" · ");
}

// How each timeline entry kind looks. Labels are shown as text so colour is never the only cue.
export const TIMELINE_KINDS = {
  admission: { label: "Admission", icon: LogIn, className: "bg-brand-50 text-brand-700 ring-brand-200" },
  discharge: { label: "Discharge", icon: LogOut, className: "bg-brand-50 text-brand-700 ring-brand-200" },
  vitals: { label: "Vitals", icon: HeartPulse, className: "bg-success-50 text-success-700 ring-success-100" },
  lab: { label: "Lab", icon: FlaskConical, className: "bg-info-50 text-info-700 ring-info-100" },
  medication_order: { label: "Medication order", icon: Pill, className: "bg-caution-50 text-caution-700 ring-caution-100" },
  medication_given: { label: "Medication given", icon: Syringe, className: "bg-caution-50 text-caution-700 ring-caution-100" },
};

export function timelineDetail(item) {
  return item.kind === "vitals" ? formatVitalsDetail(item.detail) : item.detail;
}

// Date and time, with the year added when it is not this year (records span several years).
const stampFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const stampYearFmt = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
export function formatStamp(value) {
  if (!value) return "—";
  const d = new Date(value);
  return (d.getFullYear() === new Date().getFullYear() ? stampFmt : stampYearFmt).format(d);
}

// Round axis bounds and evenly spaced ticks (1, 2, 2.5 or 5 times a power of ten).
export function niceScale(lo, hi, count = 4, { floor = -Infinity, ceil = Infinity } = {}) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  const min = Math.max(floor, Math.floor(lo / step) * step);
  const max = Math.min(ceil, Math.ceil(hi / step) * step);
  const ticks = [];
  for (let v = min; v <= max + step / 1e6; v += step) ticks.push(Number(v.toFixed(6)));
  return { domain: [min, max], ticks };
}
