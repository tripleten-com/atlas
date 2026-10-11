import { useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Activity as ActivityIcon,
  ArrowRight,
  BedDouble,
  CalendarClock,
  FlaskConical,
  Info,
  LogIn,
  Radio,
  Repeat,
  Siren,
  Users,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getActivity, getDashboardBreakdown, getDashboardDaily, getDashboardSummary, getEncounters } from "../api/client";
import {
  Avatar,
  Button,
  Card,
  Empty,
  EventTypeBadge,
  PageHeader,
  PanelBoundary,
  PanelSkeleton,
  Retry,
  StatCard,
  Tabs,
} from "../components";
import { useFacility } from "../context/FacilityContext";
import { useApi, useDocumentTitle, useLiveRefresh, useNow } from "../hooks";
import {
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  formatRelative,
  formatShortDate,
} from "../lib/format";

const RANGES = [
  { id: "14", label: "14 days" },
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
];

const COLORS = {
  admissions: "#197b71",
  discharges: "#7ad5c4",
  emergency: "#c42b2b",
  outpatient: "#2f63d8",
};

const AXIS = { tickLine: false, axisLine: false, fontSize: 11 };
const CHART_HEIGHT = 240;

// "2026-09-14" is a calendar day, so build it in local time instead of letting Date read it as UTC.
function parseDay(day) {
  const [y, m, d] = String(day).split("-").map(Number);
  return new Date(y, m - 1, d);
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-pop">
      <p className="mb-1 font-medium text-ink-900">{formatShortDate(parseDay(label))}</p>
      <ul className="space-y-0.5">
        {payload.map((item) => (
          <li key={item.dataKey} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 text-ink-600">
              <span aria-hidden="true" className="h-2 w-2 rounded-sm" style={{ backgroundColor: item.color }} />
              {item.name}
            </span>
            <span className="num font-medium text-ink-900">{formatNumber(item.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CountTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const item = payload[0];
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-pop">
      <p className="font-medium text-ink-900">Age {item.payload.label}</p>
      <p className="num text-ink-600">{formatNumber(item.value)} patients</p>
    </div>
  );
}

const legendFormatter = (value) => <span className="text-xs text-ink-600">{value}</span>;

// A list of labelled horizontal bars. Plain divs, so it stays readable at any width.
function BarList({ items, linkTo, color = "bg-brand-500" }) {
  const max = Math.max(1, ...items.map((i) => i.count));
  return (
    <ul className="space-y-2.5">
      {items.map((item) => {
        const label = linkTo ? (
          <Link to={linkTo(item)} className="truncate text-ink-800 hover:text-brand-700 hover:underline">
            {item.label}
          </Link>
        ) : (
          <span className="truncate text-ink-800">{item.label}</span>
        );
        return (
          <li key={item.key}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              {label}
              <span className="num shrink-0 text-ink-600">{formatNumber(item.count)}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-100" aria-hidden="true">
              <div className={`h-full rounded-full ${color}`} style={{ width: `${(item.count / max) * 100}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function PanelState({ error, loading, data, onRetry, rows = 4, empty }) {
  if (error) return <Retry error={error} onRetry={onRetry} />;
  if (loading && !data) return <PanelSkeleton rows={rows} />;
  if (empty) return empty;
  return null;
}

function Summary({ summary }) {
  const { data, error, loading, reload } = summary;
  if (error) {
    return (
      <div className="card mb-6">
        <Retry error={error} onRetry={reload} />
      </div>
    );
  }
  const s = data;
  const wait = loading && !s;
  const census = s?.census || {};
  return (
    <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
      <StatCard
        label="Patients"
        icon={Users}
        loading={wait}
        value={formatNumber(s?.patients_total)}
        hint={s?.median_age != null ? `Median age ${formatNumber(s.median_age, { decimals: 0 })}` : undefined}
        to="/patients"
      />
      <StatCard
        label="Inpatients now"
        icon={BedDouble}
        loading={wait}
        value={formatNumber(census.inpatient)}
        hint={s ? `+${formatNumber(census.observation)} observation` : undefined}
        to="/encounters"
      />
      <StatCard label="In the ED now" icon={Siren} tone="high" loading={wait} value={formatNumber(census.emergency)} />
      <StatCard
        label="Admissions 24h"
        icon={LogIn}
        loading={wait}
        value={formatNumber(s?.admissions_24h)}
        hint={s ? `${formatNumber(s.discharges_24h)} discharges` : undefined}
      />
      <StatCard
        label="Abnormal labs 24h"
        icon={FlaskConical}
        tone="warn"
        loading={wait}
        value={formatNumber(s?.abnormal_labs_24h)}
        hint="Open the results worklist"
        to="/results"
      />
      <StatCard
        label="Avg length of stay 30d"
        icon={CalendarClock}
        tone="low"
        loading={wait}
        value={s?.avg_length_of_stay_days_30d != null ? `${formatNumber(s.avg_length_of_stay_days_30d, { decimals: 1 })} d` : "—"}
        hint="Inpatient stays, last 30 days"
      />
      <StatCard
        label="30-day readmission rate"
        icon={Repeat}
        tone="neutral"
        loading={wait}
        value={formatPercent(s?.readmission_rate_30d)}
        hint="Stays discharged 30 to 60 days ago"
      />
    </div>
  );
}

function DailyCharts({ daily, range, onRange }) {
  const { data, error, loading, reload } = daily;
  const tickFormatter = (d) => formatShortDate(parseDay(d));
  const interval = range === "14" ? 1 : range === "30" ? 4 : 14;
  const body = (render) =>
    PanelState({
      error,
      loading,
      data,
      onRetry: reload,
      rows: 5,
      empty: data && data.length === 0 ? <Empty title="No visits yet" msg="Nothing has been recorded in this range." /> : null,
    }) || <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>{render()}</div>;

  return (
    <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-5">
      <Card
        className="xl:col-span-3"
        title="Admissions and discharges"
        subtitle="Inpatient and observation stays per day"
        actions={
          <Tabs
            size="sm"
            tabs={RANGES}
            value={range}
            onChange={onRange}
          />
        }
      >
        {body(() => (
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -20 }} barGap={2}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="day" {...AXIS} tickFormatter={tickFormatter} interval={interval} minTickGap={8} />
              <YAxis {...AXIS} allowDecimals={false} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: "#eceef2", opacity: 0.6 }} />
              <Legend iconType="square" iconSize={8} formatter={legendFormatter} wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="admissions" name="Admissions" fill={COLORS.admissions} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="discharges" name="Discharges" fill={COLORS.discharges} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        ))}
      </Card>

      <Card className="xl:col-span-2" title="Visit mix" subtitle={`Emergency and outpatient visits, last ${range} days`}>
        {body(() => (
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <AreaChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -20 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="day" {...AXIS} tickFormatter={tickFormatter} interval={interval} minTickGap={8} />
              <YAxis {...AXIS} allowDecimals={false} />
              <Tooltip content={<ChartTooltip />} />
              <Legend iconType="square" iconSize={8} formatter={legendFormatter} wrapperStyle={{ fontSize: 11 }} />
              <Area
                type="monotone"
                dataKey="emergency_visits"
                name="Emergency"
                stackId="visits"
                stroke={COLORS.emergency}
                fill={COLORS.emergency}
                fillOpacity={0.15}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="outpatient_visits"
                name="Outpatient"
                stackId="visits"
                stroke={COLORS.outpatient}
                fill={COLORS.outpatient}
                fillOpacity={0.15}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        ))}
      </Card>
    </div>
  );
}

function Breakdown({ breakdown }) {
  const { data, error, loading, reload } = breakdown;
  const panel = (key, render, emptyMsg) => {
    const items = data?.[key];
    return (
      PanelState({
        error,
        loading,
        data,
        onRetry: reload,
        empty: items && items.length === 0 ? <Empty title="Nothing to show" msg={emptyMsg} className="py-6" /> : null,
      }) || render(items)
    );
  };

  return (
    <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Card title="Admission types" subtitle="Last 90 days">
        {panel("admission_types", (items) => <BarList items={items} />, "No visits in the last 90 days.")}
      </Card>
      <Card title="Age bands" subtitle="All patients">
        {panel(
          "age_bands",
          (items) => (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={items} margin={{ top: 4, right: 0, bottom: 0, left: -24 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="label" {...AXIS} />
                <YAxis {...AXIS} allowDecimals={false} />
                <Tooltip content={<CountTooltip />} cursor={{ fill: "#eceef2", opacity: 0.6 }} />
                <Bar dataKey="count" name="Patients" fill={COLORS.admissions} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          ),
          "No patients yet.",
        )}
      </Card>
      <Card title="Most frequent abnormal tests" subtitle="Last 7 days, select a test to see results">
        {panel(
          "abnormal_tests_7d",
          (items) => (
            <BarList items={items} color="bg-caution-600" linkTo={(item) => `/results?test=${encodeURIComponent(item.key)}&hours=168`} />
          ),
          "No abnormal results in the last 7 days.",
        )}
      </Card>
      <Card title="Discharge dispositions" subtitle="Inpatients, last 30 days">
        {panel("dispositions_30d", (items) => <BarList items={items} color="bg-info-600" />, "No discharges in the last 30 days.")}
      </Card>
    </div>
  );
}

function Admitted({ facilityId, showFacility }) {
  const { data, error, loading, reload, refresh } = useApi(
    () => getEncounters({ facility_id: facilityId, state: "active", admission_type: "inpatient", page_size: 8 }),
    [facilityId],
    { keepPrevious: true },
  );
  useLiveRefresh(refresh, { facilityId, throttleMs: 8000 });

  return (
    <Card
      title="Currently admitted"
      subtitle={data ? `${formatNumber(data.total)} inpatients, newest first` : "Inpatients, newest first"}
      padded={false}
    >
      {PanelState({
        error,
        loading,
        data,
        onRetry: reload,
        rows: 6,
        empty:
          data && data.items.length === 0 ? (
            <Empty icon={BedDouble} title="No one is admitted" msg="Inpatient stays show here while they are open." />
          ) : null,
      }) || (
        <ul className="divide-y divide-ink-100">
          {data.items.map((e) => (
            <li key={e.id} className="flex items-center gap-3 px-5 py-3">
              <Avatar name={e.patient_name} size="sm" />
              <div className="min-w-0 flex-1">
                <Link to={`/patients/${e.patient_id}`} className="block truncate text-sm font-medium text-ink-900 hover:text-brand-700 hover:underline">
                  {e.patient_name}
                </Link>
                <p className="truncate text-xs text-ink-500">
                  MRN {e.patient_mrn}
                  {showFacility && ` · ${e.facility_name}`}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p className="num text-sm text-ink-800">{formatDuration(e.length_of_stay_hours)}</p>
                <p className="text-xs text-ink-500">since admission</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <footer className="border-t border-ink-100 px-5 py-3">
        <Link to="/encounters" className="inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:underline">
          View all encounters <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </footer>
    </Card>
  );
}

function LatestActivity({ activity, isStatic, liveFacility, onSwitch, showFacility }) {
  const now = useNow();
  const { data, error, loading, reload } = activity;

  let body;
  if (isStatic) {
    body = (
      <Empty
        icon={Radio}
        title="No live feed for the Test Facility"
        msg="Its records were loaded once and never change. Switch to a live facility to watch events arrive."
        action={
          liveFacility && (
            <Button variant="primary" size="sm" onClick={() => onSwitch(liveFacility.id)}>
              Switch to {liveFacility.name}
            </Button>
          )
        }
      />
    );
  } else {
    body =
      PanelState({
        error,
        loading,
        data,
        onRetry: reload,
        rows: 6,
        empty:
          data && data.length === 0 ? (
            <Empty icon={ActivityIcon} title="No activity yet" msg="Events from the integration feed show here as they arrive." />
          ) : null,
      }) ||
      (
        <ul className="divide-y divide-ink-100">
          {data.map((a) => (
            <li key={a.id} className="flex items-start gap-3 px-5 py-3">
              <div className="w-24 shrink-0 pt-0.5">
                <EventTypeBadge type={a.event_type} />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-ink-800">{a.summary}</p>
                <p className="truncate text-xs text-ink-500">
                  {a.patient_id ? (
                    <Link to={`/patients/${a.patient_id}`} className="text-brand-700 hover:underline">
                      {a.patient_name}
                    </Link>
                  ) : (
                    "Unmatched patient"
                  )}
                  {showFacility && ` · ${a.facility_name}`}
                </p>
              </div>
              <time dateTime={a.at} title={formatDateTime(a.at)} className="num shrink-0 whitespace-nowrap text-xs text-ink-500">
                {formatRelative(a.at, now)}
              </time>
            </li>
          ))}
        </ul>
      );
  }

  return (
    <Card title="Latest activity" subtitle="Events applied from the integration feed" padded={false}>
      {body}
      <footer className="border-t border-ink-100 px-5 py-3">
        <Link to="/activity" className="inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:underline">
          Open the activity feed <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </footer>
    </Card>
  );
}

export default function Dashboard() {
  useDocumentTitle("Overview");
  const { facilityId, scopeLabel, isStatic, facilities, setFacilityId } = useFacility();
  const [params, setParams] = useSearchParams();
  const range = RANGES.some((r) => r.id === params.get("range")) ? params.get("range") : "30";

  const setRange = (id) => {
    const next = new URLSearchParams(params);
    if (id === "30") next.delete("range");
    else next.set("range", id);
    setParams(next, { replace: true });
  };

  const summary = useApi(() => getDashboardSummary({ facility_id: facilityId }), [facilityId], { keepPrevious: true });
  const daily = useApi(() => getDashboardDaily({ facility_id: facilityId, days: Number(range) }), [facilityId, range], {
    keepPrevious: true,
  });
  const breakdown = useApi(() => getDashboardBreakdown({ facility_id: facilityId }), [facilityId], { keepPrevious: true });
  const activity = useApi(() => getActivity({ facility_id: facilityId, limit: 8 }), [facilityId, isStatic], {
    enabled: !isStatic,
    keepPrevious: true,
  });

  const refreshSummary = summary.refresh;
  const refreshDaily = daily.refresh;
  const refreshActivity = activity.refresh;
  const refresh = useCallback(() => {
    if (isStatic) return;
    refreshSummary();
    refreshDaily();
    refreshActivity();
  }, [isStatic, refreshSummary, refreshDaily, refreshActivity]);
  useLiveRefresh(refresh, { facilityId });

  const liveFacility = facilities.find((f) => f.data_source === "meridian");
  const showFacility = facilityId === null;
  const asOf = summary.data?.as_of;

  return (
    <div>
      <PageHeader
        title="Overview"
        description={
          <>
            {scopeLabel}.{" "}
            {asOf ? `Data as of ${formatDateTime(asOf)}.` : <span className="inline-block h-3 w-40 animate-pulse rounded bg-ink-100 align-middle" />}
          </>
        }
      >
        {isStatic && (
          <p className="mt-3 flex max-w-3xl items-start gap-2 rounded-lg border border-caution-100 bg-caution-50 px-3 py-2 text-xs text-caution-700">
            <Info aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              The Test Facility is a fixed two-year history. Time windows such as 24 hours or 30 days count back from its last
              record, not from today.
            </span>
          </p>
        )}
      </PageHeader>

      <PanelBoundary name="Summary">
        <Summary summary={summary} />
      </PanelBoundary>

      <PanelBoundary name="Daily charts">
        <DailyCharts daily={daily} range={range} onRange={setRange} />
      </PanelBoundary>

      <PanelBoundary name="Breakdown">
        <Breakdown breakdown={breakdown} />
      </PanelBoundary>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <PanelBoundary name="Currently admitted">
          <Admitted facilityId={facilityId} showFacility={showFacility} />
        </PanelBoundary>
        <PanelBoundary name="Latest activity">
          <LatestActivity
            activity={activity}
            isStatic={isStatic}
            liveFacility={liveFacility}
            onSwitch={setFacilityId}
            showFacility={showFacility}
          />
        </PanelBoundary>
      </div>
    </div>
  );
}
