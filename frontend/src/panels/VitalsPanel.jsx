import { useState, useEffect, useMemo } from "react";
import { CartesianGrid, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity } from "lucide-react";
import clsx from "clsx";
import { getVitals, getPatientEncounters } from "../api/client";
import { Card, Empty, Retry, Spinner, Tabs } from "../components";
import { useLiveRefresh } from "../hooks";
import { formatDate, formatDateTime, formatNumber } from "../lib/format";
import {
  CHART,
  ChartFrame,
  ChartTooltip,
  formatRange,
  formatValue,
  rangeStatus,
  timeTickFormatter,
  useReference,
  vitalRanges,
  niceScale,
} from "./common";

const DAY_MS = 24 * 60 * 60 * 1000;

// The charts drawn by this panel. Blood pressure shows systolic and diastolic together.
const CHARTS = [
  {
    id: "bp",
    label: "Blood pressure",
    unit: "mmHg",
    wide: true,
    series: [
      { code: "systolic_bp", name: "Systolic", color: CHART.brand },
      { code: "diastolic_bp", name: "Diastolic", color: CHART.info },
    ],
  },
  { id: "hr", label: "Heart rate", unit: "bpm", series: [{ code: "heart_rate", color: CHART.brand }] },
  { id: "spo2", label: "SpO₂", unit: "%", series: [{ code: "spo2", color: CHART.brand }] },
  { id: "rr", label: "Respiratory rate", unit: "breaths/min", series: [{ code: "respiratory_rate", color: CHART.brand }] },
  { id: "temp", label: "Temperature", unit: "°C", series: [{ code: "temperature", color: CHART.brand }] },
];

// What "This visit", "7 days" and "30 days" mean for this patient. For a patient who is not
// admitted, day ranges end at their most recent visit so older records still show a trend.
function useVisitWindow(patientId) {
  const [visits, setVisits] = useState(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let ignore = false;
    getPatientEncounters(patientId)
      .then((data) => !ignore && setVisits(data))
      .catch(() => !ignore && setVisits([]));
    return () => {
      ignore = true;
    };
  }, [patientId, version]);
  useLiveRefresh(() => setVersion((v) => v + 1), { patientId, throttleMs: 15000 });

  const active = visits?.find((v) => v.is_active) || null;
  const last = visits?.[0] || null;
  const anchor = active || !last ? null : new Date(last.discharge_date || last.admit_date).getTime();
  const loaded = visits !== null;
  const activeId = active?.id ?? null;
  return useMemo(() => (loaded ? { activeId, anchor } : null), [loaded, activeId, anchor]);
}

function queryFor(range, visitWindow) {
  if (range === "visit") return { encounter_id: visitWindow.activeId, limit: 3000 };
  if (range === "all") return { limit: 3000 };
  const days = range === "7d" ? 7 : 30;
  const end = visitWindow.anchor ?? Date.now();
  return { since: new Date(end - days * DAY_MS).toISOString(), limit: 3000 };
}

export function VitalsPanel({ patientId }) {
  const [vitals, setVitals] = useState(null);      // 1. state starts empty
  const [error, setError] = useState(null);
  const [chosenRange, setChosenRange] = useState(null);
  const [loadedFor, setLoadedFor] = useState(null);
  const [version, setVersion] = useState(0);
  const visitWindow = useVisitWindow(patientId);
  const reference = useReference();
  const ranges = useMemo(() => vitalRanges(reference), [reference]);

  const defaultRange = visitWindow?.activeId ? "visit" : "30d";
  const range = chosenRange === "visit" && !visitWindow?.activeId ? defaultRange : chosenRange || defaultRange;
  const requestKey = `${patientId}:${range}`;

  useEffect(() => {                                // 2. request after the first render
    if (!visitWindow) return undefined; // wait until we know whether the patient is admitted
    let ignore = false;
    getVitals(patientId, queryFor(range, visitWindow))
      .then((data) => {
        if (ignore) return;
        setVitals(data);
        setLoadedFor(requestKey);
        setError(null);
      })
      .catch((err) => !ignore && setError(err));
    return () => {
      ignore = true;
    };
  }, [patientId, range, visitWindow, version, requestKey]); // 3. re-run when the patient changes

  // New readings for this patient arrive from the live feed: fetch again quietly.
  useLiveRefresh(() => setVersion((v) => v + 1), { patientId });

  const retry = () => {
    setError(null);
    setVersion((v) => v + 1);
  };

  if (error && !vitals) {
    return (
      <Card title="Vital sign trends">
        <Retry error={error} onRetry={retry} />
      </Card>
    );
  }

  if (!vitals) {                                   // 4. the not-yet-loaded case
    return (
      <Card title="Vital sign trends">
        <div className="flex h-40 items-center justify-center">
          <Spinner label="Loading vital signs" />
        </div>
      </Card>
    );
  }

  const rangeTabs = [
    ...(visitWindow?.activeId ? [{ id: "visit", label: "This visit" }] : []),
    { id: "7d", label: "7 days" },
    { id: "30d", label: "30 days" },
    { id: "all", label: "All" },
  ];
  const stale = loadedFor !== requestKey;
  const subtitle =
    range === "visit"
      ? "Current visit"
      : range === "all"
        ? "All recorded readings"
        : visitWindow?.anchor
          ? `${range === "7d" ? "7" : "30"} days up to the last visit on ${formatDate(visitWindow.anchor)}`
          : `Last ${range === "7d" ? "7" : "30"} days`;

  return (
    <section className="card">
      <header className="card-header flex-wrap">
        <div className="min-w-0">
          <h2 className="card-title">Vital sign trends</h2>
          <p className="card-subtitle mt-0.5">
            {subtitle} · {formatNumber(vitals.length)} readings. Shaded band is the normal range.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {stale && <Spinner size={14} label="Updating vital signs" />}
          <Tabs size="sm" tabs={rangeTabs} value={range} onChange={setChosenRange} />
        </div>
      </header>
      {error && <div className="border-b border-ink-100 px-5"><Retry compact error={error} onRetry={retry} /></div>}
      {vitals.length === 0 ? (
        <Empty
          icon={Activity}
          title="No vital signs in this range"
          msg={range === "all" ? "Nothing has been recorded for this patient yet." : "Try a longer range."}
          action={
            range !== "all" && (
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setChosenRange("all")}>
                Show all readings
              </button>
            )
          }
        />
      ) : (
        <div
          className={clsx(
            "card-body grid grid-cols-1 gap-x-6 gap-y-5 transition-opacity sm:grid-cols-2 group-[.expanded]:xl:grid-cols-3",
            stale && "opacity-60",
          )}
        >
          {CHARTS.map((chart) => (
            <VitalChart key={chart.id} chart={chart} vitals={vitals} ranges={ranges} />
          ))}
        </div>
      )}
    </section>
  );
}

function VitalChart({ chart, vitals, ranges }) {
  const { data, span, domain, ticks, summary, latest, outside } = useMemo(() => {
    const byTime = new Map();
    const codes = chart.series.map((s) => s.code);
    vitals.forEach((v) => {
      if (!codes.includes(v.measurement)) return;
      const t = new Date(v.recorded_at).getTime();
      const row = byTime.get(t) || { t };
      row[v.measurement] = v.value;
      byTime.set(t, row);
    });
    const rows = [...byTime.values()].sort((a, b) => a.t - b.t);
    const values = rows.flatMap((r) => codes.map((c) => r[c]).filter((x) => x !== undefined));
    const bounds = codes.flatMap((c) => [ranges[c]?.normal_low, ranges[c]?.normal_high]).filter((x) => x !== undefined);
    const lo = Math.min(...values, ...bounds);
    const hi = Math.max(...values, ...bounds);
    const scale = niceScale(lo, hi, 3, { floor: 0, ceil: codes.includes("spo2") ? 100 : Infinity });

    const last = rows[rows.length - 1];
    let outsideCount = 0;
    rows.forEach((r) =>
      codes.forEach((c) => {
        if (r[c] !== undefined && rangeStatus(r[c], ranges[c]?.normal_low, ranges[c]?.normal_high) !== "normal") outsideCount += 1;
      }),
    );
    const first = rows[0];
    const latestText = chart.series
      .map((s) => (last?.[s.code] !== undefined ? formatValue(last[s.code]) : "—"))
      .join("/");
    const rangeText = chart.series.map((s) => formatRange(ranges[s.code]?.normal_low, ranges[s.code]?.normal_high)).join(" and ");
    return {
      data: rows,
      span: rows.length ? last.t - first.t : 0,
      domain: scale.domain,
      ticks: scale.ticks,
      latest: last ? { text: latestText, row: last } : null,
      outside: outsideCount,
      summary: rows.length
        ? `${rows.length} readings from ${formatDateTime(first.t)} to ${formatDateTime(last.t)}. Latest ${latestText} ${chart.unit}. ` +
          `${outsideCount} ${outsideCount === 1 ? "reading" : "readings"} outside the normal range${rangeText ? ` of ${rangeText} ${chart.unit}` : ""}.`
        : "No readings in this range.",
    };
  }, [chart, vitals, ranges]);

  const latestStatus = latest
    ? chart.series
        .map((s) => rangeStatus(latest.row[s.code], ranges[s.code]?.normal_low, ranges[s.code]?.normal_high))
        .find((st) => st !== "normal") || "normal"
    : "normal";
  const statusOf = (code, value) => rangeStatus(value, ranges[code]?.normal_low, ranges[code]?.normal_high);

  return (
    <div className={clsx("min-w-0", chart.wide && "sm:col-span-2")}>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate text-xs font-medium text-ink-600">{chart.label}</h3>
          {chart.series.length > 1 &&
            chart.series.map((s) => (
              <span key={s.code} className="hidden items-center gap-1 text-[11px] text-ink-500 sm:inline-flex">
                <span aria-hidden="true" className="h-0.5 w-3 rounded" style={{ background: s.color }} />
                {s.name}
              </span>
            ))}
        </div>
        <p className="num whitespace-nowrap text-sm">
          {latest ? (
            <>
              <span className={clsx("font-semibold", latestStatus === "normal" ? "text-ink-900" : "text-critical-700")}>
                {latest.text}
              </span>{" "}
              <span className="text-xs text-ink-500">{chart.unit}</span>
              {latestStatus !== "normal" && (
                <span className="ml-1.5 text-xs font-medium text-critical-700">{latestStatus === "high" ? "High" : "Low"}</span>
              )}
            </>
          ) : (
            <span className="text-xs text-ink-400">No readings</span>
          )}
        </p>
      </div>
      <ChartFrame
        label={`${chart.label} trend`}
        summary={summary}
        className="h-32 group-[.expanded]:h-48"
      >
        {data.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-lg bg-ink-50 text-xs text-ink-400">No readings</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} />
              {chart.series.map((s, i) =>
                ranges[s.code] ? (
                  <ReferenceArea
                    key={s.code}
                    y1={ranges[s.code].normal_low}
                    y2={ranges[s.code].normal_high}
                    fill={CHART.band}
                    fillOpacity={i === 0 ? CHART.bandOpacity : CHART.bandOpacity * 0.6}
                    stroke="none"
                    ifOverflow="hidden"
                  />
                ) : null,
              )}
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={timeTickFormatter(span)}
                tick={CHART.tick}
                tickLine={false}
                axisLine={false}
                minTickGap={40}
              />
              <YAxis
                domain={domain}
                tick={CHART.tick}
                tickLine={false}
                axisLine={false}
                width={40}
                ticks={ticks}
                interval={0}
                tickFormatter={formatValue}
              />
              <Tooltip
                content={<ChartTooltip unit={chart.unit} statusOf={statusOf} />}
                cursor={{ stroke: "#c1c6d1", strokeDasharray: "3 3" }}
              />
              {chart.series.map((s) => (
                <Line
                  key={s.code}
                  name={s.name}
                  dataKey={s.code}
                  type="monotone"
                  stroke={s.color}
                  strokeWidth={1.75}
                  connectNulls
                  isAnimationActive={false}
                  dot={dotFor(ranges[s.code], data.length === 1 ? s.color : null)}
                  activeDot={{ r: 3.5, strokeWidth: 0 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </ChartFrame>
      {outside > 0 && (
        <p className="mt-1 text-[11px] text-ink-500">
          <span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-full bg-critical-600 align-middle" />
          {outside} outside normal range
        </p>
      )}
    </div>
  );
}

// Highlight readings outside the normal range; draw a plain dot when there is only one point.
function dotFor(range, singleColor) {
  return function VitalDot({ cx, cy, value, index }) {
    if (cx === undefined || cy === undefined || value === undefined || value === null) return <g key={index} />;
    if (rangeStatus(value, range?.normal_low, range?.normal_high) !== "normal") {
      return <circle key={index} cx={cx} cy={cy} r={3.5} fill={CHART.critical} stroke="#fff" strokeWidth={1.5} />;
    }
    if (singleColor) return <circle key={index} cx={cx} cy={cy} r={3} fill={singleColor} />;
    return <g key={index} />;
  };
}
