import { Fragment, useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { AlertTriangle, ChevronRight, Clock, FileJson, Inbox, Pause, Play, PlugZap, RefreshCw, RotateCcw, Timer } from "lucide-react";
import clsx from "clsx";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getIntegration, getIntegrationEvents, getIntegrationVolume } from "../api/client";
import {
  Badge,
  Button,
  Card,
  Empty,
  EventTypeBadge,
  IngestStatusBadge,
  KeyValue,
  PageHeader,
  Pagination,
  PanelBoundary,
  PanelSkeleton,
  Retry,
  Select,
  Spinner,
  StatCard,
  StatusDot,
  Tabs,
} from "../components";
import { useAuth } from "../context/AuthContext";
import { useApi, useDocumentTitle, useLiveRefresh, useNow } from "../hooks";
import { EVENT_TYPES, formatDateTime, formatNumber, formatRelative, formatShortDate, formatTime } from "../lib/format";
import { VENDOR_NAME, connectionStatus, useConnectionActions } from "./Integrations";

// Stack order and colour for each event type. Medications share one hue in three shades.
const SERIES = [
  { type: "patient.registered", color: "#9199a8" },
  { type: "encounter.admitted", color: "#45b9a8" },
  { type: "encounter.discharged", color: "#7ad5c4" },
  { type: "vital_signs.recorded", color: "#197b71" },
  { type: "lab.resulted", color: "#2f63d8" },
  { type: "medication.ordered", color: "#8a5a00" },
  { type: "medication.administered", color: "#b0740a" },
  { type: "medication.discontinued", color: "#dcb466" },
];

const RANGES = [
  { id: "24", label: "24 h" },
  { id: "72", label: "72 h" },
  { id: "168", label: "7 days" },
];

const STATUS_TABS = [
  { id: "any", label: "All" },
  { id: "processed", label: "Processed" },
  { id: "deferred", label: "Deferred" },
  { id: "failed", label: "Failed" },
  { id: "skipped", label: "Skipped" },
  { id: "pending", label: "Pending" },
];

const TYPE_OPTIONS = [
  { value: "", label: "All event types" },
  ...Object.entries(EVENT_TYPES).map(([value, meta]) => ({ value, label: meta.label })),
];

const PAGE_SIZE = 50;
const HOUR = 3_600_000;
const label = (type) => EVENT_TYPES[type]?.label || type;

// One row per hour across the range, with a count per event type (zeros filled in).
function buildVolume(rows, hours) {
  const end = Math.floor(Date.now() / HOUR) * HOUR;
  let last = end;
  const byHour = new Map();
  rows.forEach((r) => {
    const t = new Date(r.hour).getTime();
    last = Math.max(last, t);
    if (!byHour.has(t)) byHour.set(t, {});
    byHour.get(t)[r.event_type] = (byHour.get(t)[r.event_type] || 0) + r.count;
  });
  const points = [];
  for (let t = end - (hours - 1) * HOUR; t <= last; t += HOUR) {
    const counts = byHour.get(t) || {};
    const point = { t };
    SERIES.forEach((s) => (point[s.type] = counts[s.type] || 0));
    points.push(point);
  }
  return points;
}

function VolumeTooltip({ active, payload, label: t }) {
  if (!active || !payload?.length) return null;
  const items = payload.filter((p) => p.value > 0).reverse();
  const total = items.reduce((sum, p) => sum + p.value, 0);
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-pop">
      <p className="mb-1 font-medium text-ink-900">
        {formatDateTime(t)} <span className="font-normal text-ink-500">· {formatNumber(total)} events</span>
      </p>
      {items.length === 0 ? (
        <p className="text-ink-500">No events</p>
      ) : (
        <ul className="space-y-0.5">
          {items.map((p) => (
            <li key={p.dataKey} className="flex items-center justify-between gap-4">
              <span className="flex items-center gap-1.5 text-ink-600">
                <span aria-hidden="true" className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
                {label(p.dataKey)}
              </span>
              <span className="num font-medium text-ink-900">{formatNumber(p.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function VolumeChart({ connectionId, facilityId, version }) {
  const [range, setRange] = useState("24");
  const hours = Number(range);
  const { data, error, loading, reload, refresh } = useApi(
    () => getIntegrationVolume(connectionId, { hours }),
    [connectionId, hours, version],
    { keepPrevious: true },
  );
  useLiveRefresh(refresh, { facilityId, throttleMs: 5000 });
  const points = useMemo(() => (data ? buildVolume(data, hours) : []), [data, hours]);
  const total = data ? data.reduce((sum, r) => sum + r.count, 0) : 0;
  const present = new Set((data || []).map((r) => r.event_type));
  const ticks = useMemo(() => {
    const step = hours <= 24 ? 3 : hours <= 72 ? 12 : 24;
    return points.filter((p) => new Date(p.t).getHours() % step === 0).map((p) => p.t);
  }, [points, hours]);
  const tickFormat = (t) => {
    const d = new Date(t);
    if (hours <= 24) return formatTime(d);
    return d.getHours() === 0 ? formatShortDate(d) : formatTime(d);
  };

  return (
    <Card
      title="Events received"
      subtitle={data ? `${formatNumber(total)} events by the hour they happened` : "By the hour they happened"}
      actions={<Tabs tabs={RANGES} value={range} onChange={setRange} size="sm" />}
      padded={false}
    >
      {error ? (
        <Retry error={error} onRetry={reload} />
      ) : loading && !data ? (
        <PanelSkeleton rows={5} />
      ) : total === 0 ? (
        <Empty icon={Inbox} title="No events in this range" msg="Try a longer range, or check that the feed is running." />
      ) : (
        <div className={clsx("px-3 pb-4 pt-4", loading && "opacity-60")}>
          <div style={{ height: 260 }} role="img" aria-label={`Stacked bar chart of ${formatNumber(total)} events per hour by type`}>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: -12 }} barCategoryGap={hours > 72 ? 0 : "15%"}>
                <CartesianGrid vertical={false} />
                <XAxis
                  dataKey="t"
                  ticks={ticks}
                  tickFormatter={tickFormat}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                  minTickGap={16}
                />
                <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} width={40} />
                <Tooltip content={<VolumeTooltip />} cursor={{ fill: "rgba(145, 153, 168, 0.12)" }} />
                {SERIES.map((s) => (
                  <Bar key={s.type} dataKey={s.type} stackId="events" fill={s.color} isAnimationActive={false} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 px-3 text-xs text-ink-600" aria-label="Legend">
            {SERIES.filter((s) => present.has(s.type)).map((s) => (
              <li key={s.type} className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
                {label(s.type)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function IngestionLog({ connection, version }) {
  const connectionId = connection.id;
  const now = useNow(15_000);
  const [status, setStatus] = useState("any");
  const [eventType, setEventType] = useState("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState(() => new Set());

  const params = { status, event_type: eventType, page, page_size: PAGE_SIZE };
  const { data, error, loading, reload, refresh } = useApi(
    () => getIntegrationEvents(connectionId, params),
    [connectionId, status, eventType, page, version],
    { keepPrevious: true },
  );
  const payloads = useApi(
    () => getIntegrationEvents(connectionId, { ...params, include_payload: true }),
    [connectionId, status, eventType, page, version],
    { enabled: expanded.size > 0, keepPrevious: true },
  );
  // Follow live updates on the first page only, so rows don't shift under someone reading page 3.
  useLiveRefresh(
    () => {
      if (page === 1) refresh();
    },
    { facilityId: connection.facility_id, throttleMs: 3000 },
  );

  const changeFilter = (fn) => {
    fn();
    setPage(1);
    setExpanded(new Set());
  };
  const toggle = (id) => {
    const opening = !expanded.has(id);
    // Rows that arrived after the payloads were fetched need a fresh copy.
    if (opening && expanded.size > 0 && !payloads.loading && !payloads.data?.items.some((p) => p.id === id)) {
      payloads.refresh();
    }
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const tabs = STATUS_TABS.map((t) => ({
    ...t,
    count: ["pending", "deferred", "failed"].includes(t.id) ? connection[t.id] : undefined,
  }));

  return (
    <div className="card">
      <header className="card-header flex-wrap">
        <div>
          <h2 className="card-title">Ingestion log</h2>
          <p className="card-subtitle mt-0.5">Every event Meridian delivered, newest first, with what the worker did with it.</p>
        </div>
      </header>
      <div className="flex flex-col gap-3 border-b border-ink-100 p-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="overflow-x-auto">
          <Tabs tabs={tabs} value={status} onChange={(id) => changeFilter(() => setStatus(id))} size="sm" />
        </div>
        <Select
          label="Event type"
          hideLabel
          value={eventType}
          onChange={(v) => changeFilter(() => setEventType(v))}
          options={TYPE_OPTIONS}
          className="lg:w-52"
        />
      </div>

      {error ? (
        <Retry error={error} onRetry={reload} />
      ) : loading && !data ? (
        <PanelSkeleton rows={8} />
      ) : data.items.length === 0 ? (
        <Empty
          icon={Inbox}
          title="No events match"
          msg={status === "failed" ? "Nothing has failed. Good." : "Try another status or event type."}
        />
      ) : (
        <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col" className="w-10">
                    <span className="sr-only">Details</span>
                  </th>
                  <th scope="col" className="text-right">
                    Seq.
                  </th>
                  <th scope="col">Type</th>
                  <th scope="col">Summary</th>
                  <th scope="col">Occurred</th>
                  <th scope="col">Received via</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="text-right">
                    Attempts
                  </th>
                  <th scope="col">Error</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((ev) => {
                  const open = expanded.has(ev.id);
                  const payload = payloads.data?.items.find((p) => p.id === ev.id)?.payload;
                  return (
                    <Fragment key={ev.id}>
                      <tr className={clsx(open && "bg-ink-50/60")}>
                        <td className="!px-2">
                          <button
                            type="button"
                            aria-expanded={open}
                            aria-controls={`payload-${ev.id}`}
                            aria-label={`${open ? "Hide" : "Show"} payload for event ${ev.sequence}`}
                            onClick={() => toggle(ev.id)}
                            className="rounded p-1 text-ink-400 hover:bg-ink-100 hover:text-ink-800"
                          >
                            <ChevronRight aria-hidden="true" className={clsx("h-4 w-4 transition-transform", open && "rotate-90")} />
                          </button>
                        </td>
                        <td className="num text-right font-mono text-xs text-ink-600">{ev.sequence}</td>
                        <td>
                          <EventTypeBadge type={ev.event_type} />
                        </td>
                        <td className="min-w-[16rem] text-ink-900">{ev.summary}</td>
                        <td className="whitespace-nowrap text-ink-600" title={formatDateTime(ev.occurred_at)}>
                          {formatRelative(ev.occurred_at, now)}
                        </td>
                        <td>
                          <Badge tone={ev.received_via === "webhook" ? "neutral" : "brand"} title={`Received ${formatDateTime(ev.received_at)}`}>
                            {ev.received_via === "webhook" ? "Webhook" : ev.received_via === "resync" ? "Resync" : ev.received_via}
                          </Badge>
                        </td>
                        <td>
                          <IngestStatusBadge status={ev.status} />
                        </td>
                        <td className="num text-right">{ev.attempts}</td>
                        <td className="max-w-[14rem]">
                          {ev.error ? (
                            <span className="block truncate text-xs text-critical-700" title={ev.error}>
                              {ev.error}
                            </span>
                          ) : (
                            <span className="text-xs text-ink-400">None</span>
                          )}
                        </td>
                      </tr>
                      {open && (
                        <tr id={`payload-${ev.id}`}>
                          <td colSpan={9} className="bg-ink-50/60 !pt-0">
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pb-2 text-xs text-ink-500">
                              <span className="inline-flex items-center gap-1">
                                <FileJson aria-hidden="true" className="h-3.5 w-3.5" /> Payload
                              </span>
                              <span className="font-mono">{ev.external_id}</span>
                              <span>Received {formatDateTime(ev.received_at)}</span>
                              <span>Processed {ev.processed_at ? formatDateTime(ev.processed_at) : "not yet"}</span>
                            </div>
                            {payloads.error ? (
                              <Retry compact error={payloads.error} onRetry={payloads.reload} />
                            ) : payload === undefined ? (
                              <Spinner label="Loading payload" />
                            ) : (
                              <pre className="max-h-80 overflow-auto rounded-lg border border-ink-200 bg-white p-3 font-mono text-[11px] leading-relaxed text-ink-800">
                                {JSON.stringify(payload, null, 2)}
                              </pre>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={data.total}
            onPage={(n) => {
              setPage(n);
              setExpanded(new Set());
            }}
          />
        </div>
      )}
    </div>
  );
}

export default function IntegrationDetail() {
  const { connectionId } = useParams();
  const { isAdmin } = useAuth();
  const now = useNow(15_000);
  // Bumped after an admin action so the chart and log reload.
  const [version, setVersion] = useState(0);
  const { data: c, error, loading, reload, refresh } = useApi(() => getIntegration(connectionId), [connectionId], {
    keepPrevious: true,
  });
  useDocumentTitle(c ? `${c.facility_name} feed` : "Integration");

  const afterAction = useCallback(() => {
    refresh();
    setVersion((n) => n + 1);
  }, [refresh]);
  useLiveRefresh(refresh, { facilityId: c ? c.facility_id : -1, throttleMs: 3000 });
  const actions = useConnectionActions(afterAction);

  const crumbs = [{ label: "Integrations", to: "/integrations" }, { label: c ? c.facility_name : "Connection" }];

  if (error && (error.status === 404 || error.status === 422)) {
    return (
      <div>
        <PageHeader title="Connection not found" breadcrumbs={crumbs} />
        <div className="card">
          <Empty
            icon={PlugZap}
            title="This connection does not exist"
            msg="It may belong to another organisation, or the link is wrong."
            link={{ to: "/integrations", label: "Back to integrations" }}
          />
        </div>
      </div>
    );
  }
  if (error && !c) {
    return (
      <div>
        <PageHeader title="Integration" breadcrumbs={crumbs} />
        <div className="card">
          <Retry error={error} onRetry={reload} />
        </div>
      </div>
    );
  }
  if (loading && !c) {
    return (
      <div>
        <PageHeader title="Loading connection" breadcrumbs={crumbs} />
        <div className="card">
          <PanelSkeleton rows={6} />
        </div>
      </div>
    );
  }

  const st = connectionStatus(c);
  const busy = !!actions.busy;
  const headerActions = isAdmin && (
    <>
      {c.failed > 0 && (
        <Button variant="danger" icon={RotateCcw} loading={actions.busy === "retry"} disabled={busy} onClick={() => actions.retryFailed(c)}>
          Retry failed ({formatNumber(c.failed)})
        </Button>
      )}
      {c.status === "paused" ? (
        <Button icon={Play} loading={actions.busy === "resume"} disabled={busy} onClick={() => actions.resume(c)}>
          Resume
        </Button>
      ) : (
        <Button icon={Pause} loading={actions.busy === "pause"} disabled={busy} onClick={() => actions.pause(c)}>
          Pause
        </Button>
      )}
      <Button variant="primary" icon={RefreshCw} loading={actions.busy === "resync"} disabled={busy} onClick={() => actions.resync(c)}>
        Resync
      </Button>
    </>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={c.facility_name}
        description={`${VENDOR_NAME} feed for ${c.facility_name} (${c.facility_code}).`}
        breadcrumbs={crumbs}
        actions={headerActions}
      />

      {c.last_error && (
        <div role="alert" className="flex items-start gap-2 rounded-xl border border-critical-100 bg-critical-50 px-4 py-3 text-sm text-critical-700">
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="break-words">
            <span className="font-semibold">Last error: </span>
            {c.last_error}
          </p>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Events, last 24 h" value={formatNumber(c.events_24h)} icon={Clock} />
        <StatCard label="Pending" value={formatNumber(c.pending)} hint="Waiting for the worker" icon={Timer} tone="neutral" />
        <StatCard
          label="Deferred"
          value={formatNumber(c.deferred)}
          hint="Arrived before what they refer to"
          icon={RotateCcw}
          tone={c.deferred > 0 ? "warn" : "neutral"}
        />
        <StatCard
          label="Failed"
          value={formatNumber(c.failed)}
          hint={c.failed > 0 ? "Needs attention" : "None"}
          icon={AlertTriangle}
          tone={c.failed > 0 ? "high" : "neutral"}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card title="Connection" className="xl:col-span-1">
          <KeyValue
            items={[
              { label: "Status", value: <StatusDot tone={st.tone} pulse={st.pulse} label={st.label} /> },
              { label: "Subscription", value: <span className="font-mono text-xs">{c.subscription_id || "None"}</span> },
              { label: "Stream epoch", value: <span className="font-mono text-xs">{c.stream_epoch || "None"}</span> },
              { label: "High watermark", value: <span className="num">#{formatNumber(c.high_watermark)}</span> },
              { label: "Last event", value: <span title={formatDateTime(c.last_event_at)}>{formatRelative(c.last_event_at, now)}</span> },
              {
                label: "Last delivery",
                value: <span title={formatDateTime(c.last_delivery_at)}>{formatRelative(c.last_delivery_at, now)}</span>,
              },
              {
                label: "Last resync",
                value: c.last_resync_at ? <span title={formatDateTime(c.last_resync_at)}>{formatRelative(c.last_resync_at, now)}</span> : "Never",
              },
              { label: "Needs resync", value: c.needs_resync ? <Badge tone="warn">Yes, queued</Badge> : "No" },
            ]}
          />
          {!isAdmin && <p className="mt-4 text-xs text-ink-500">Admins can pause, resync or retry this feed.</p>}
        </Card>
        <div className="xl:col-span-2">
          <PanelBoundary name="Events received">
            <VolumeChart connectionId={c.id} facilityId={c.facility_id} version={version} />
          </PanelBoundary>
        </div>
      </div>

      <PanelBoundary name="Ingestion log">
        <IngestionLog connection={c} version={version} />
      </PanelBoundary>
    </div>
  );
}
