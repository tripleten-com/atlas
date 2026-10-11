import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, History } from "lucide-react";
import clsx from "clsx";
import { getTimeline } from "../api/client";
import { Button, Card, Empty, PanelSkeleton, Retry } from "../components";
import { useApi, useLiveRefresh } from "../hooks";
import { formatTime } from "../lib/format";
import { TIMELINE_KINDS, timelineDetail } from "./common";

const PAGE = 50;
const MAX = 500; // the API returns at most 500 entries

const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
const dayKey = (iso) => {
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
};

// Everything that happened to the patient, newest first, grouped by day.
// Pass `encounterId` to show one visit only.
export function TimelinePanel({ patientId, encounterId = null, title = "Timeline", subtitle }) {
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => setLimit(PAGE), [patientId, encounterId]);

  const { data, error, loading, reload, refresh } = useApi(
    () => getTimeline(patientId, { encounter_id: encounterId, limit }),
    [patientId, encounterId, limit],
    { keepPrevious: true },
  );
  useLiveRefresh(refresh, { patientId });

  const days = useMemo(() => {
    const groups = [];
    (data || []).forEach((item) => {
      const key = dayKey(item.at);
      let group = groups[groups.length - 1];
      if (!group || group.key !== key) {
        group = { key, label: dayFmt.format(new Date(item.at)), items: [] };
        groups.push(group);
      }
      group.items.push(item);
    });
    return groups;
  }, [data]);

  const hasMore = data && data.length >= limit && limit < MAX;

  return (
    <Card
      title={title}
      subtitle={subtitle ?? (data ? `${data.length} most recent entries` : "Visits, vital signs, lab results and orders")}
      padded={false}
    >
      {error && !data ? (
        <Retry error={error} onRetry={reload} />
      ) : !data ? (
        <PanelSkeleton rows={6} />
      ) : data.length === 0 ? (
        <Empty icon={History} title="Nothing recorded yet" msg="Visits, readings, results and orders appear here as they happen." />
      ) : (
        <div className={clsx("transition-opacity", loading && "opacity-60")}>
          {error && (
            <div className="px-5">
              <Retry compact error={error} onRetry={reload} />
            </div>
          )}
          {days.map((day) => (
            <section key={day.key} aria-label={day.label}>
              <h3 className="sticky top-16 z-[2] border-b border-ink-100 bg-white/95 px-5 py-2 text-xs font-semibold text-ink-600 backdrop-blur">
                {day.label}
              </h3>
              <ol className="relative px-5 py-3">
                <span aria-hidden="true" className="absolute bottom-3 left-[35px] top-3 w-px bg-ink-100" />
                {day.items.map((item, i) => (
                  <TimelineEntry key={`${item.at}-${item.kind}-${item.title}-${i}`} item={item} />
                ))}
              </ol>
            </section>
          ))}
          <div className="flex items-center justify-between gap-3 border-t border-ink-100 px-5 py-3 text-xs text-ink-500">
            <span className="num">Showing {data.length} entries</span>
            {hasMore ? (
              <Button size="sm" loading={loading} onClick={() => setLimit((n) => Math.min(MAX, n + PAGE))}>
                Load more
              </Button>
            ) : (
              <span>{limit >= MAX && data.length >= MAX ? "Showing the most recent 500 entries" : "Start of record"}</span>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function TimelineEntry({ item }) {
  const kind = TIMELINE_KINDS[item.kind] || TIMELINE_KINDS.vitals;
  const Icon = kind.icon;
  return (
    <li className="relative flex gap-3 py-2">
      <span
        className={clsx(
          "relative z-[1] flex h-8 w-8 shrink-0 items-center justify-center rounded-full ring-1",
          item.abnormal ? "bg-critical-50 text-critical-700 ring-critical-100" : kind.className,
        )}
      >
        <Icon aria-hidden="true" className="h-4 w-4" />
      </span>
      <div
        className={clsx(
          "min-w-0 flex-1 rounded-lg px-3 py-1.5",
          item.abnormal && "border-l-2 border-critical-600 bg-critical-50/60",
        )}
      >
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <p className="text-sm font-medium text-ink-900">{item.title}</p>
          <span className="sr-only">({kind.label})</span>
          {item.abnormal && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-critical-700">
              <AlertTriangle aria-hidden="true" className="h-3 w-3" />
              Abnormal
            </span>
          )}
          <time dateTime={item.at} className="num ml-auto text-xs text-ink-500">
            {formatTime(item.at)}
          </time>
        </div>
        {item.detail && <p className="num mt-0.5 break-words text-xs text-ink-600">{timelineDetail(item)}</p>}
      </div>
    </li>
  );
}
