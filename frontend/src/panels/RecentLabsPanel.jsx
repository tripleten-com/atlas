import { useMemo } from "react";
import { Link } from "react-router-dom";
import { FlaskConical } from "lucide-react";
import clsx from "clsx";
import { getLabs } from "../api/client";
import { Card, Empty, FlagBadge, PanelSkeleton, Retry } from "../components";
import { useApi, useLiveRefresh, useNow } from "../hooks";
import { formatRelative } from "../lib/format";
import { formatRange, formatValue, panelLabel } from "./common";

const MAX_ROWS = 10;

// The latest result of each test, abnormal results first.
export function RecentLabsPanel({ patientId }) {
  const now = useNow();
  const { data, error, loading, reload, refresh } = useApi(() => getLabs(patientId, { limit: 300 }), [patientId]);
  useLiveRefresh(refresh, { patientId });

  const latest = useMemo(() => {
    const seen = new Map();
    (data || []).forEach((lab) => {
      if (!seen.has(lab.test_code)) seen.set(lab.test_code, lab);
    });
    return [...seen.values()].sort(
      (a, b) => (a.flag === "N") - (b.flag === "N") || b.collected_at.localeCompare(a.collected_at),
    );
  }, [data]);
  const abnormal = latest.filter((l) => l.flag !== "N").length;

  return (
    <Card
      title="Recent lab results"
      subtitle={data ? `Latest result per test · ${abnormal} out of range` : "Latest result per test"}
      padded={false}
      actions={
        <Link to="?tab=labs" className="text-xs font-medium text-brand-700 hover:underline">
          All results
        </Link>
      }
    >
      {error ? (
        <Retry error={error} onRetry={reload} />
      ) : loading && !data ? (
        <PanelSkeleton rows={5} />
      ) : latest.length === 0 ? (
        <Empty icon={FlaskConical} title="No lab results" msg="Results appear here once the lab reports them." />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Test</th>
                <th scope="col" className="text-right">
                  Result
                </th>
                <th scope="col">Flag</th>
                <th scope="col" className="hidden sm:table-cell">
                  Reference
                </th>
                <th scope="col">Collected</th>
              </tr>
            </thead>
            <tbody>
              {latest.slice(0, MAX_ROWS).map((lab) => (
                <tr key={lab.test_code}>
                  <td>
                    <p className="font-medium text-ink-900">{lab.test_name}</p>
                    <p className="text-xs text-ink-500">{panelLabel(lab.panel)}</p>
                  </td>
                  <td className="num whitespace-nowrap text-right">
                    <span className={clsx(lab.flag !== "N" ? "font-semibold text-ink-900" : "text-ink-800")}>
                      {formatValue(lab.value)}
                    </span>{" "}
                    <span className="text-xs text-ink-500">{lab.unit}</span>
                  </td>
                  <td>
                    <FlagBadge flag={lab.flag} />
                  </td>
                  <td className="num hidden whitespace-nowrap text-ink-500 sm:table-cell">{formatRange(lab.ref_low, lab.ref_high)}</td>
                  <td className="num whitespace-nowrap text-ink-500">{formatRelative(lab.collected_at, now)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {latest.length > MAX_ROWS && (
            <p className="px-5 py-3 text-xs text-ink-500">
              Showing {MAX_ROWS} of {latest.length} tests.{" "}
              <Link to="?tab=labs" className="font-medium text-brand-700 hover:underline">
                See all results
              </Link>
            </p>
          )}
        </div>
      )}
    </Card>
  );
}
