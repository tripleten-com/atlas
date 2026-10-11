import { Link } from "react-router-dom";
import { History } from "lucide-react";
import clsx from "clsx";
import { getTimeline } from "../api/client";
import { Card, Empty, PanelSkeleton, Retry } from "../components";
import { useApi, useLiveRefresh, useNow } from "../hooks";
import { formatRelative } from "../lib/format";
import { TIMELINE_KINDS, timelineDetail } from "./common";

// The last few things that happened to the patient, in a compact list.
export function RecentActivityPanel({ patientId }) {
  const now = useNow();
  const { data, error, loading, reload, refresh } = useApi(() => getTimeline(patientId, { limit: 12 }), [patientId]);
  useLiveRefresh(refresh, { patientId });

  return (
    <Card
      title="Recent activity"
      padded={false}
      className="h-full"
      actions={
        <Link to="?tab=timeline" className="text-xs font-medium text-brand-700 hover:underline">
          Full timeline
        </Link>
      }
    >
      {error ? (
        <Retry error={error} onRetry={reload} />
      ) : loading && !data ? (
        <PanelSkeleton rows={5} />
      ) : data.length === 0 ? (
        <Empty icon={History} title="No activity yet" />
      ) : (
        <ul className="divide-y divide-ink-100">
          {data.map((item, i) => {
            const kind = TIMELINE_KINDS[item.kind] || TIMELINE_KINDS.vitals;
            const Icon = kind.icon;
            return (
              <li key={`${item.at}-${item.kind}-${i}`} className="flex gap-3 px-5 py-2.5">
                <span
                  className={clsx(
                    "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ring-1",
                    item.abnormal ? "bg-critical-50 text-critical-700 ring-critical-100" : kind.className,
                  )}
                >
                  <Icon aria-hidden="true" className="h-3.5 w-3.5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <p className="truncate text-sm text-ink-900">
                      {item.title}
                      {item.abnormal && <span className="ml-1.5 text-xs font-semibold text-critical-700">Abnormal</span>}
                    </p>
                    <span className="num shrink-0 text-[11px] text-ink-400">{formatRelative(item.at, now)}</span>
                  </div>
                  {item.detail && <p className="num truncate text-xs text-ink-500">{timelineDetail(item)}</p>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
