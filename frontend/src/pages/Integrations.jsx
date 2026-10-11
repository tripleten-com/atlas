import { Fragment, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Cog,
  Database,
  Hospital,
  LayoutDashboard,
  Network,
  Pause,
  Play,
  PlugZap,
  RefreshCw,
  ShieldCheck,
  Table2,
} from "lucide-react";
import clsx from "clsx";
import {
  getIntegrations,
  pauseIntegration,
  resumeIntegration,
  resyncIntegration,
  retryFailedEvents,
} from "../api/client";
import { Badge, Button, Card, Empty, PageHeader, PanelSkeleton, Retry, StatusDot, useToast } from "../components";
import { useAuth } from "../context/AuthContext";
import { useFacility } from "../context/FacilityContext";
import { useApi, useDocumentTitle, useLiveRefresh, useNow } from "../hooks";
import { formatDateTime, formatNumber, formatRelative } from "../lib/format";

export const VENDOR_NAME = "Meridian Clinical Exchange";

// How a connection is doing, in words. Needs resync wins over the stored status.
export function connectionStatus(c) {
  if (c.status === "paused") return { tone: "neutral", label: "Paused", pulse: false };
  if (c.status === "error") return { tone: "high", label: "Error", pulse: false };
  if (c.needs_resync) return { tone: "warn", label: "Needs resync", pulse: false };
  return { tone: "ok", label: "Receiving", pulse: true };
}

// Admin actions on one connection. `busy` names the action in flight, if any.
export function useConnectionActions(onDone) {
  const toast = useToast();
  const [busy, setBusy] = useState(null);
  const run = useCallback(
    async (key, fn, success) => {
      setBusy(key);
      try {
        const result = await fn();
        toast(typeof success === "function" ? success(result) : success);
        await onDone?.();
      } catch (err) {
        toast(err.message || "That did not work. Try again.", { tone: "error" });
      } finally {
        setBusy(null);
      }
    },
    [toast, onDone],
  );
  return {
    busy,
    pause: (c) => run("pause", () => pauseIntegration(c.id), `Paused the feed for ${c.facility_name}.`),
    resume: (c) => run("resume", () => resumeIntegration(c.id), `Resumed the feed for ${c.facility_name}.`),
    resync: (c) =>
      run("resync", () => resyncIntegration(c.id), `Resync queued for ${c.facility_name}. The worker will pull any missed events.`),
    retryFailed: (c) =>
      run(
        "retry",
        () => retryFailedEvents(c.id),
        (r) => `Requeued ${formatNumber(r?.requeued ?? 0)} failed ${r?.requeued === 1 ? "event" : "events"}.`,
      ),
  };
}

const PIPELINE = [
  { icon: Hospital, title: "Hospital systems", text: "EHR, lab and pharmacy systems record care." },
  { icon: Network, title: VENDOR_NAME, text: "Collects events and numbers them in order." },
  { icon: ShieldCheck, title: "Signed webhook (HMAC)", text: "Atlas rejects any batch with a bad signature." },
  { icon: Database, title: "ingest_events", text: "Raw events stored once, keyed by event id." },
  { icon: Cog, title: "Ingestion worker", text: "Applies events in order and retries any that arrive early." },
  { icon: Table2, title: "Clinical tables", text: "Patients, encounters, vitals, labs and medications." },
  { icon: LayoutDashboard, title: "Dashboards update live", text: "Open pages refresh when a batch lands." },
];

function Metric({ label, value, tone, title }) {
  return (
    <div className="min-w-0" title={title}>
      <dt className="text-xs text-ink-500">{label}</dt>
      <dd className={clsx("num mt-0.5 truncate text-sm font-medium", tone === "high" ? "text-critical-700" : "text-ink-900")}>
        {value}
      </dd>
    </div>
  );
}

function ConnectionCard({ c, isAdmin, onChanged, now }) {
  const st = connectionStatus(c);
  const actions = useConnectionActions(onChanged);
  return (
    <article className="card flex flex-col">
      <header className="flex items-start justify-between gap-3 border-b border-ink-100 px-5 py-4">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-ink-900">
            <Link to={`/integrations/${c.id}`} className="hover:text-brand-700 hover:underline">
              {c.facility_name}
            </Link>{" "}
            <span className="font-mono text-xs font-normal text-ink-500">{c.facility_code}</span>
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">{VENDOR_NAME}</p>
        </div>
        <StatusDot tone={st.tone} pulse={st.pulse} label={st.label} />
      </header>

      <div className="flex-1 px-5 py-4">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          <Metric label="Last event" value={formatRelative(c.last_event_at, now)} title={formatDateTime(c.last_event_at)} />
          <Metric
            label="Last delivery"
            value={formatRelative(c.last_delivery_at, now)}
            title={formatDateTime(c.last_delivery_at)}
          />
          <Metric label="Events, last 24 h" value={formatNumber(c.events_24h)} />
          <Metric label="High watermark" value={`#${formatNumber(c.high_watermark)}`} title="Highest sequence number stored" />
          <Metric label="Pending / deferred" value={`${formatNumber(c.pending)} / ${formatNumber(c.deferred)}`} />
          <Metric label="Failed" value={formatNumber(c.failed)} tone={c.failed > 0 ? "high" : undefined} />
        </dl>
        {c.last_error && (
          <p role="alert" className="mt-4 break-words rounded-lg border border-critical-100 bg-critical-50 px-3 py-2 text-xs text-critical-700">
            <span className="font-semibold">Last error: </span>
            {c.last_error}
          </p>
        )}
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-ink-100 px-5 py-3">
        {isAdmin ? (
          <div className="flex flex-wrap gap-2">
            {c.status === "paused" ? (
              <Button size="sm" icon={Play} loading={actions.busy === "resume"} disabled={!!actions.busy} onClick={() => actions.resume(c)}>
                Resume
              </Button>
            ) : (
              <Button size="sm" icon={Pause} loading={actions.busy === "pause"} disabled={!!actions.busy} onClick={() => actions.pause(c)}>
                Pause
              </Button>
            )}
            <Button size="sm" icon={RefreshCw} loading={actions.busy === "resync"} disabled={!!actions.busy} onClick={() => actions.resync(c)}>
              Resync
            </Button>
          </div>
        ) : (
          <p className="text-xs text-ink-500">Admins can pause or resync this feed.</p>
        )}
        <Link to={`/integrations/${c.id}`} className="inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:underline">
          View details <span className="sr-only">for {c.facility_name}</span>
          <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </footer>
    </article>
  );
}

function SeededCard({ facility }) {
  return (
    <article className="card flex flex-col">
      <header className="flex items-start justify-between gap-3 border-b border-ink-100 px-5 py-4">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-ink-900">
            {facility.name} <span className="font-mono text-xs font-normal text-ink-500">{facility.code}</span>
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">No vendor connection</p>
        </div>
        <Badge>Seeded history</Badge>
      </header>
      <div className="flex flex-1 items-start gap-3 px-5 py-4">
        <span className="rounded-lg bg-ink-50 p-2 text-ink-500 ring-1 ring-ink-100">
          <Database aria-hidden="true" className="h-4 w-4" />
        </span>
        <p className="text-sm text-ink-600">
          Seeded history, no feed. This facility holds {formatNumber(facility.patient_count)} patients loaded once at setup.
          Its records never change, which makes it a stable place to test features.
        </p>
      </div>
    </article>
  );
}

function Pipeline() {
  return (
    <ol className="flex flex-wrap items-stretch gap-y-3">
      {PIPELINE.map((step, i) => (
        <Fragment key={step.title}>
          {i > 0 && (
            <li aria-hidden="true" className="flex items-center px-1.5 text-ink-300">
              <ArrowRight className="h-4 w-4" />
            </li>
          )}
          <li className="flex w-40 flex-col rounded-lg border border-ink-200 bg-ink-50/60 p-3">
            <span className="mb-2 inline-flex h-8 w-8 items-center justify-center rounded-lg bg-white text-brand-700 ring-1 ring-ink-200">
              <step.icon aria-hidden="true" className="h-4 w-4" />
            </span>
            <p className={clsx("text-xs font-semibold text-ink-900", step.title === "ingest_events" && "font-mono")}>
              {step.title}
            </p>
            <p className="mt-1 text-xs leading-snug text-ink-500">{step.text}</p>
          </li>
        </Fragment>
      ))}
    </ol>
  );
}

export default function Integrations() {
  useDocumentTitle("Integrations");
  const { isAdmin } = useAuth();
  const { facilities } = useFacility();
  const now = useNow(15_000);
  const { data, error, loading, reload, refresh } = useApi(() => getIntegrations(), [], { keepPrevious: true });
  useLiveRefresh(refresh, { throttleMs: 3000 });
  useEffect(() => {
    const timer = setInterval(refresh, 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const seeded = facilities.filter((f) => f.data_source === "seed");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Integrations"
        description={`Clinical data arrives from ${VENDOR_NAME}, which signs each batch and delivers it to Atlas as it happens.`}
      />

      {error && !data ? (
        <div className="card">
          <Retry error={error} onRetry={reload} />
        </div>
      ) : loading && !data ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="card">
            <PanelSkeleton rows={4} />
          </div>
          <div className="card">
            <PanelSkeleton rows={4} />
          </div>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {data.length === 0 && (
            <div className="card lg:col-span-2">
              <Empty
                icon={PlugZap}
                title="No connections yet"
                msg="The ingestion worker creates a Meridian subscription for each live facility when it starts. Check that it is running."
              />
            </div>
          )}
          {data.map((c) => (
            <ConnectionCard key={c.id} c={c} isAdmin={isAdmin} onChanged={refresh} now={now} />
          ))}
          {seeded.map((f) => (
            <SeededCard key={f.id} facility={f} />
          ))}
        </div>
      )}

      <Card title="How data reaches Atlas" subtitle="Every clinical record on the live facilities takes this path.">
        <Pipeline />
      </Card>
    </div>
  );
}
