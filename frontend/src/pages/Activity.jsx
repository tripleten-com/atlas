import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Activity as ActivityIcon, Database, Radio } from "lucide-react";
import clsx from "clsx";
import { getActivity } from "../api/client";
import { Badge, Button, Empty, EventTypeBadge, PageHeader, PanelSkeleton, Retry, StatusDot } from "../components";
import { useFacility } from "../context/FacilityContext";
import { useLive } from "../context/LiveContext";
import { useApi, useDocumentTitle, useLiveRefresh, useNow } from "../hooks";
import { formatDateTime, formatNumber, formatRelative } from "../lib/format";

const FILTERS = [
  { id: "all", label: "All", match: () => true },
  { id: "encounters", label: "Admissions and discharges", match: (t) => t.startsWith("encounter.") },
  { id: "vitals", label: "Vitals", match: (t) => t === "vital_signs.recorded" },
  { id: "labs", label: "Labs", match: (t) => t === "lab.resulted" },
  { id: "medications", label: "Medications", match: (t) => t.startsWith("medication.") },
  { id: "registrations", label: "Registrations", match: (t) => t === "patient.registered" },
];

const LIVE_STATUS = {
  open: { tone: "ok", pulse: true, label: "Live" },
  connecting: { tone: "neutral", pulse: false, label: "Connecting" },
  retrying: { tone: "warn", pulse: false, label: "Reconnecting" },
  idle: { tone: "neutral", pulse: false, label: "Offline" },
};

const HIGHLIGHT_MS = 4000;

// Counts live updates in the current scope from the moment the page opened.
function useUpdateCount(facilityId) {
  const { subscribe } = useLive();
  const [count, setCount] = useState(0);
  useEffect(
    () =>
      subscribe((event) => {
        if (facilityId !== null && event.facility_id !== facilityId) return;
        const n = Object.values(event.counts || {}).reduce((sum, v) => sum + v, 0);
        if (n) setCount((c) => c + n);
      }),
    [subscribe, facilityId],
  );
  return count;
}

// Remembers which rows have been shown and flags the ones that just arrived.
function useFreshIds(items) {
  const seen = useRef(null);
  const timers = useRef(new Set());
  const [fresh, setFresh] = useState(() => new Set());
  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);
  useEffect(() => {
    if (!items) return;
    if (seen.current === null) {
      seen.current = new Set(items.map((i) => i.id));
      return;
    }
    const arrived = items.filter((i) => !seen.current.has(i.id)).map((i) => i.id);
    if (!arrived.length) return;
    arrived.forEach((id) => seen.current.add(id));
    setFresh((prev) => new Set([...prev, ...arrived]));
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setFresh((prev) => {
        const next = new Set(prev);
        arrived.forEach((id) => next.delete(id));
        return next;
      });
    }, HIGHLIGHT_MS);
    timers.current.add(timer);
  }, [items]);
  const reset = () => {
    seen.current = null;
    setFresh(new Set());
  };
  return { fresh, reset };
}

export default function Activity() {
  useDocumentTitle("Live activity");
  const { facilityId, facilities, isStatic, setFacilityId, scopeLabel } = useFacility();
  const { status } = useLive();
  const [filter, setFilter] = useState("all");
  const [abnormalOnly, setAbnormalOnly] = useState(false);
  const updates = useUpdateCount(facilityId);
  const now = useNow(15_000);

  const { data, error, loading, reload, refresh } = useApi(
    () => (isStatic ? Promise.resolve([]) : getActivity({ facility_id: facilityId, limit: 100 })),
    [facilityId, isStatic],
    { keepPrevious: true },
  );
  useLiveRefresh(refresh, { facilityId, throttleMs: 2000 });

  const { fresh, reset } = useFreshIds(data);
  // A new scope is a new list: don't flash every row as new.
  useEffect(() => {
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facilityId]);

  const counts = useMemo(() => {
    const base = (data || []).filter((i) => !abnormalOnly || i.abnormal);
    return Object.fromEntries(FILTERS.map((f) => [f.id, base.filter((i) => f.match(i.event_type)).length]));
  }, [data, abnormalOnly]);

  const rows = useMemo(() => {
    const match = FILTERS.find((f) => f.id === filter).match;
    return (data || []).filter((i) => match(i.event_type) && (!abnormalOnly || i.abnormal));
  }, [data, filter, abnormalOnly]);

  const live = LIVE_STATUS[status] || LIVE_STATUS.idle;
  const firstFeed = facilities.find((f) => f.data_source === "meridian");
  const showFacility = facilityId === null;

  return (
    <div>
      <PageHeader
        title="Live activity"
        description={`Clinical events from the Meridian feed as Atlas applies them, for ${scopeLabel === "All facilities" ? "all your facilities" : scopeLabel}.`}
        actions={
          !isStatic && (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <StatusDot tone={live.tone} pulse={live.pulse} label={live.label} />
              <span className="text-ink-500" aria-live="polite">
                <span className="num font-medium text-ink-800">{formatNumber(updates)}</span>{" "}
                {updates === 1 ? "update" : "updates"} received since you opened this page
              </span>
            </div>
          )
        }
      />

      {isStatic ? (
        <div className="card">
          <Empty
            icon={Database}
            title="No live feed for the Test Facility"
            msg="The Test Facility holds a fixed history loaded at setup. Nothing new arrives, so there is no activity to show."
            action={
              firstFeed && (
                <Button variant="primary" icon={Radio} onClick={() => setFacilityId(firstFeed.id)}>
                  Switch to {firstFeed.name}
                </Button>
              )
            }
          />
        </div>
      ) : (
        <div className="card">
          <div className="flex flex-col gap-3 border-b border-ink-100 p-4 lg:flex-row lg:items-center lg:justify-between">
            <div role="group" aria-label="Filter by event type" className="flex flex-wrap gap-1.5">
              {FILTERS.map((f) => {
                const active = f.id === filter;
                return (
                  <button
                    key={f.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setFilter(f.id)}
                    className={clsx(
                      "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition",
                      active
                        ? "border-brand-600 bg-brand-50 text-brand-800"
                        : "border-ink-200 bg-white text-ink-600 hover:border-ink-300 hover:text-ink-900",
                    )}
                  >
                    {f.label}
                    {data && <span className="num text-[11px] opacity-70">{counts[f.id]}</span>}
                  </button>
                );
              })}
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-ink-700">
              <input
                type="checkbox"
                checked={abnormalOnly}
                onChange={(e) => setAbnormalOnly(e.target.checked)}
                className="h-4 w-4 rounded border-ink-300 text-brand-700 focus:ring-brand-500"
              />
              Abnormal only
            </label>
          </div>

          {error ? (
            <Retry error={error} onRetry={reload} />
          ) : loading && !data ? (
            <PanelSkeleton rows={10} />
          ) : rows.length === 0 ? (
            <Empty
              icon={ActivityIcon}
              title={data.length === 0 ? "No activity yet" : "Nothing matches these filters"}
              msg={
                data.length === 0
                  ? "Events appear here as soon as the ingestion worker applies them."
                  : "Try another event type, or turn off Abnormal only."
              }
            />
          ) : (
            <ul className="divide-y divide-ink-100" aria-label="Recent clinical events">
              {rows.map((item) => (
                <li
                  key={item.id}
                  className={clsx(
                    "flex flex-col gap-2 border-l-2 px-4 py-3 transition-colors duration-700 sm:flex-row sm:items-center sm:gap-4",
                    item.abnormal ? "border-l-critical-600" : "border-l-transparent",
                    fresh.has(item.id) ? "bg-brand-50" : "bg-white",
                  )}
                >
                  <div className="flex shrink-0 items-center gap-2 sm:w-32">
                    <EventTypeBadge type={item.event_type} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-sm text-ink-900">
                      <span className="min-w-0 break-words">{item.summary}</span>
                      {item.abnormal && <Badge tone="high">Abnormal</Badge>}
                      {fresh.has(item.id) && <span className="sr-only">(new)</span>}
                    </p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-ink-500">
                      {item.patient_id ? (
                        <Link to={`/patients/${item.patient_id}`} className="font-medium text-brand-700 hover:underline">
                          {item.patient_name}
                        </Link>
                      ) : (
                        item.patient_name && <span>{item.patient_name}</span>
                      )}
                      {showFacility && (
                        <>
                          {item.patient_name && <span aria-hidden="true">·</span>}
                          <span>{item.facility_name}</span>
                        </>
                      )}
                    </p>
                  </div>
                  <time
                    dateTime={item.at}
                    title={formatDateTime(item.at)}
                    className="shrink-0 whitespace-nowrap text-xs text-ink-500 sm:text-right"
                  >
                    {formatRelative(item.at, now)}
                  </time>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
