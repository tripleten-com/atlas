import { useMemo, useState } from "react";
import { HeartPulse } from "lucide-react";
import { getVitals } from "../api/client";
import { Card, Empty, Pagination, PanelSkeleton, Retry } from "../components";
import { useApi, useLiveRefresh } from "../hooks";
import { formatStamp, formatValue, rangeStatus, useReference, vitalRanges } from "./common";

const PAGE_SIZE = 20;
const COLUMNS = [
  { code: "heart_rate", label: "HR", unit: "bpm" },
  { code: "bp", label: "BP", unit: "mmHg" },
  { code: "respiratory_rate", label: "RR", unit: "/min" },
  { code: "temperature", label: "Temp", unit: "°C" },
  { code: "spo2", label: "SpO₂", unit: "%" },
];

// Recent vital sign readings, one row per set recorded together.
export function VitalReadingsPanel({ patientId }) {
  const [page, setPage] = useState(1);
  const ranges = vitalRanges(useReference());
  const { data, error, loading, reload, refresh } = useApi(() => getVitals(patientId, { limit: 1200 }), [patientId]);
  useLiveRefresh(refresh, { patientId });

  const sets = useMemo(() => {
    const byTime = new Map();
    (data || []).forEach((v) => {
      const row = byTime.get(v.recorded_at) || { at: v.recorded_at, values: {} };
      row.values[v.measurement] = v.value;
      byTime.set(v.recorded_at, row);
    });
    return [...byTime.values()].sort((a, b) => b.at.localeCompare(a.at));
  }, [data]);

  const pages = Math.max(1, Math.ceil(sets.length / PAGE_SIZE));
  const current = Math.min(page, pages);
  const rows = sets.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  return (
    <Card
      title="Recent readings"
      subtitle={data ? `${sets.length} most recent sets. Values outside the normal range are bold and marked H (high) or L (low).` : "One row per set"}
      padded={false}
    >
      {error ? (
        <Retry error={error} onRetry={reload} />
      ) : loading && !data ? (
        <PanelSkeleton rows={8} />
      ) : sets.length === 0 ? (
        <Empty icon={HeartPulse} title="No vital signs recorded" />
      ) : (
        <>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Recorded</th>
                  {COLUMNS.map((c) => (
                    <th key={c.code} scope="col" className="text-right">
                      {c.label} <span className="font-normal normal-case text-ink-400">{c.unit}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((set) => (
                  <tr key={set.at}>
                    <td className="num whitespace-nowrap text-ink-600">{formatStamp(set.at)}</td>
                    {COLUMNS.map((c) => (
                      <td key={c.code} className="num whitespace-nowrap text-right">
                        {c.code === "bp" ? (
                          <>
                            <Reading value={set.values.systolic_bp} range={ranges.systolic_bp} />
                            <span className="text-ink-400">/</span>
                            <Reading value={set.values.diastolic_bp} range={ranges.diastolic_bp} />
                          </>
                        ) : (
                          <Reading value={set.values[c.code]} range={ranges[c.code]} />
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={current} pageSize={PAGE_SIZE} total={sets.length} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

function Reading({ value, range }) {
  if (value === undefined || value === null) return <span className="text-ink-300">—</span>;
  const status = rangeStatus(value, range?.normal_low, range?.normal_high);
  if (status === "normal") return <span>{formatValue(value)}</span>;
  return (
    <span className={status === "high" ? "font-semibold text-critical-700" : "font-semibold text-info-700"}>
      {formatValue(value)}
      <span aria-hidden="true" className="ml-0.5 text-[10px] font-semibold">
        {status === "high" ? "H" : "L"}
      </span>
      <span className="sr-only">{status === "high" ? " high" : " low"}</span>
    </span>
  );
}
