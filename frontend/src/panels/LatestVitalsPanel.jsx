import { ArrowDownRight, ArrowRight, ArrowUpRight, HeartPulse } from "lucide-react";
import clsx from "clsx";
import { Badge, Card, Empty } from "../components";
import { useNow } from "../hooks";
import { formatDateTime, formatRelative, VITAL_ORDER, VITAL_SHORT } from "../lib/format";
import { formatRange, formatValue, useReference, vitalRanges } from "./common";

const STATUS = {
  low: { tone: "low", label: "Low" },
  high: { tone: "high", label: "High" },
  normal: { tone: "ok", label: "Normal" },
};

// The most recent reading of each vital sign, from the patient record itself.
export function LatestVitalsPanel({ patient }) {
  const now = useNow();
  const ranges = vitalRanges(useReference());
  const byCode = Object.fromEntries((patient.latest_vitals || []).map((v) => [v.measurement, v]));
  const newest = (patient.latest_vitals || []).reduce(
    (max, v) => (!max || v.recorded_at > max ? v.recorded_at : max),
    null,
  );

  return (
    <Card
      title="Latest vital signs"
      subtitle={newest ? `Last set recorded ${formatDateTime(newest)}` : "No readings yet"}
      padded={false}
    >
      {!newest ? (
        <Empty icon={HeartPulse} title="No vital signs recorded" msg="Readings appear here once a nurse or device records them." />
      ) : (
        <ul className="grid grid-cols-2 divide-ink-100 sm:grid-cols-3 xl:grid-cols-6 xl:divide-x">
          {VITAL_ORDER.map((code) => (
            <VitalTile key={code} code={code} vital={byCode[code]} range={ranges[code]} now={now} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function VitalTile({ code, vital, range, now }) {
  if (!vital) {
    return (
      <li className="border-b border-ink-100 px-5 py-4 xl:border-b-0">
        <p className="text-xs font-medium text-ink-500">{VITAL_SHORT[code]}</p>
        <p className="mt-2 text-sm text-ink-400">Not recorded</p>
      </li>
    );
  }
  const status = STATUS[vital.status] || STATUS.normal;
  const delta = vital.previous_value === null || vital.previous_value === undefined ? null : vital.value - vital.previous_value;
  const Trend = delta === null || delta === 0 ? ArrowRight : delta > 0 ? ArrowUpRight : ArrowDownRight;
  const trendText =
    delta === null
      ? "First reading"
      : delta === 0
        ? "No change"
        : `${delta > 0 ? "Up" : "Down"} ${formatValue(Math.abs(delta))} from ${formatValue(vital.previous_value)}`;

  return (
    <li className="border-b border-ink-100 px-5 py-4 xl:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-xs font-medium text-ink-500">{VITAL_SHORT[code]}</p>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <p className="mt-2 flex items-baseline gap-1">
        <span
          className={clsx(
            "num text-2xl font-semibold tracking-tight",
            vital.status === "normal" ? "text-ink-900" : vital.status === "high" ? "text-critical-700" : "text-info-700",
          )}
        >
          {formatValue(vital.value)}
        </span>
        <span className="text-xs text-ink-500">{vital.unit}</span>
      </p>
      <p className="mt-1 flex items-center gap-1 text-xs text-ink-500">
        <Trend aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-ink-400" />
        <span className="num truncate">{trendText}</span>
      </p>
      <p className="mt-1 text-[11px] text-ink-400">
        <span className="num">{formatRelative(vital.recorded_at, now)}</span>
        {range && <span className="num"> · Normal {formatRange(range.normal_low, range.normal_high)}</span>}
      </p>
    </li>
  );
}
