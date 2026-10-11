import { useState } from "react";
import { ArrowDownToLine, ArrowUpFromLine, Brain, CircleDollarSign, Gauge, Sparkles } from "lucide-react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getUsage } from "../api/client";
import { Badge, Card, Empty, PageHeader, PanelSkeleton, Retry, StatCard, Tabs } from "../components";
import { useApi, useDocumentTitle } from "../hooks";
import { formatDateTime, formatNumber, formatShortDate, humanize } from "../lib/format";

const RANGES = [
  { id: "7", label: "7 days" },
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
];

const CALLS_COLOR = "#197b71";
const TOKENS_COLOR = "#2f63d8";

const usd = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

function compact(n) {
  if (n === null || n === undefined) return "";
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e3).toFixed(0)}k`;
  return formatNumber(n);
}

function statusTone(status) {
  if (status === "ok") return "ok";
  if (status === "cache_hit") return "brand";
  return "high";
}

// Postgres dates arrive as "YYYY-MM-DD"; read them as local dates, not UTC midnight.
const localDay = (day) => {
  const [y, m, d] = String(day).split("-").map(Number);
  return new Date(y, m - 1, d);
};

function DailyTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-pop">
      <p className="mb-1 font-medium text-ink-900">{formatShortDate(localDay(label))}</p>
      <p className="flex justify-between gap-6 text-ink-600">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2 w-2 rounded-sm" style={{ background: CALLS_COLOR }} /> Calls
        </span>
        <span className="num font-medium text-ink-900">{formatNumber(row.calls)}</span>
      </p>
      <p className="flex justify-between gap-6 text-ink-600">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-0.5 w-2.5" style={{ background: TOKENS_COLOR }} /> Tokens
        </span>
        <span className="num font-medium text-ink-900">{formatNumber(row.tokens)}</span>
      </p>
    </div>
  );
}

function DailyChart({ days }) {
  const data = days.map((d) => ({
    day: d.day,
    calls: d.calls,
    tokens: (d.input_tokens || 0) + (d.output_tokens || 0),
  }));
  return (
    <Card title="Daily activity" subtitle="Calls per day, with input and output tokens combined" padded={false}>
      {data.length === 0 ? (
        <Empty title="No calls in this range" />
      ) : (
        <div className="px-3 pb-4 pt-4">
          <div role="img" aria-label="Daily AI calls and tokens">
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -12 }}>
                <CartesianGrid vertical={false} />
                <XAxis
                  dataKey="day"
                  tickFormatter={(d) => formatShortDate(localDay(d))}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                  minTickGap={16}
                />
                <YAxis yAxisId="calls" allowDecimals={false} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} width={40} />
                <YAxis
                  yAxisId="tokens"
                  orientation="right"
                  tickFormatter={compact}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                  width={44}
                />
                <Tooltip content={<DailyTooltip />} cursor={{ fill: "rgba(145, 153, 168, 0.12)" }} />
                <Bar yAxisId="calls" dataKey="calls" fill={CALLS_COLOR} radius={[3, 3, 0, 0]} maxBarSize={28} isAnimationActive={false} />
                <Line
                  yAxisId="tokens"
                  dataKey="tokens"
                  stroke={TOKENS_COLOR}
                  strokeWidth={2}
                  dot={false}
                  type="monotone"
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <ul className="mt-3 flex gap-4 px-3 text-xs text-ink-600" aria-label="Legend">
            <li className="flex items-center gap-1.5">
              <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: CALLS_COLOR }} /> Calls (left axis)
            </li>
            <li className="flex items-center gap-1.5">
              <span aria-hidden="true" className="h-0.5 w-3" style={{ background: TOKENS_COLOR }} /> Tokens (right axis)
            </li>
          </ul>
        </div>
      )}
    </Card>
  );
}

function FeatureTable({ rows }) {
  return (
    <Card title="By feature" padded={false}>
      {rows.length === 0 ? (
        <Empty title="No features recorded calls in this range" />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Feature</th>
                <th scope="col" className="text-right">
                  Calls
                </th>
                <th scope="col" className="text-right">
                  Input tokens
                </th>
                <th scope="col" className="text-right">
                  Output tokens
                </th>
                <th scope="col" className="text-right">
                  Predictions
                </th>
                <th scope="col" className="text-right">
                  Avg latency
                </th>
                <th scope="col" className="text-right">
                  Errors
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.feature}>
                  <td className="font-medium text-ink-900">{f.feature}</td>
                  <td className="num text-right">{formatNumber(f.calls)}</td>
                  <td className="num text-right">{formatNumber(f.input_tokens ?? 0)}</td>
                  <td className="num text-right">{formatNumber(f.output_tokens ?? 0)}</td>
                  <td className="num text-right">{formatNumber(f.predictions ?? 0)}</td>
                  <td className="num whitespace-nowrap text-right">
                    {f.avg_ms === null || f.avg_ms === undefined ? "n/a" : `${formatNumber(Number(f.avg_ms))} ms`}
                  </td>
                  <td className={`num text-right ${f.errors > 0 ? "font-medium text-critical-700" : ""}`}>{formatNumber(f.errors)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function RecentTable({ rows }) {
  return (
    <Card title="Recent calls" subtitle="The 25 most recent in this range" padded={false}>
      {rows.length === 0 ? (
        <Empty title="No recent calls" />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Feature</th>
                <th scope="col">Model</th>
                <th scope="col">Status</th>
                <th scope="col" className="text-right">
                  Tokens in / out
                </th>
                <th scope="col">User</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.ts}-${i}`}>
                  <td className="whitespace-nowrap text-ink-600">{formatDateTime(r.ts)}</td>
                  <td className="font-medium text-ink-900">{r.feature}</td>
                  <td className="font-mono text-xs text-ink-600">{r.model || "Not set"}</td>
                  <td>
                    <Badge tone={statusTone(r.status)} dot>
                      {humanize(r.status)}
                    </Badge>
                  </td>
                  <td className="num whitespace-nowrap text-right">
                    {formatNumber(r.input_tokens ?? 0)} / {formatNumber(r.output_tokens ?? 0)}
                  </td>
                  <td className="whitespace-nowrap text-ink-600">{r.user_name || "System"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function Usage() {
  useDocumentTitle("Usage");
  const [range, setRange] = useState("30");
  const { data, error, loading, reload } = useApi(() => getUsage({ days: Number(range) }), [range], { keepPrevious: true });
  const pending = loading && !data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Usage"
        description="Calls, tokens and predictions recorded by AI features across your organisation."
        actions={<Tabs tabs={RANGES} value={range} onChange={setRange} />}
      />

      {error ? (
        <div className="card">
          <Retry error={error} onRetry={reload} />
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <StatCard label="AI calls" value={formatNumber(data?.total_events)} icon={Sparkles} loading={pending} />
            <StatCard label="Input tokens" value={formatNumber(data?.total_input_tokens)} icon={ArrowDownToLine} tone="low" loading={pending} />
            <StatCard label="Output tokens" value={formatNumber(data?.total_output_tokens)} icon={ArrowUpFromLine} tone="low" loading={pending} />
            <StatCard label="Predictions" value={formatNumber(data?.total_predictions)} icon={Brain} tone="ok" loading={pending} />
            <StatCard
              label="Estimated cost"
              value={data ? usd.format(data.estimated_cost_usd) : ""}
              hint="At illustrative list prices"
              icon={CircleDollarSign}
              tone="neutral"
              loading={pending}
            />
          </div>

          {pending ? (
            <div className="card">
              <PanelSkeleton rows={6} />
            </div>
          ) : data.total_events === 0 ? (
            <div className="card">
              <Empty
                icon={Gauge}
                title="No AI usage recorded yet"
                msg={
                  <>
                    No AI feature has recorded a call in the last {data.days} days. Features record each call with{" "}
                    <code className="rounded bg-ink-100 px-1 py-0.5 font-mono text-[12px] text-ink-800">record_usage()</code> from{" "}
                    <code className="rounded bg-ink-100 px-1 py-0.5 font-mono text-[12px] text-ink-800">backend/app/metering.py</code>.
                  </>
                }
              />
            </div>
          ) : (
            <div className={loading ? "space-y-6 opacity-60 transition-opacity" : "space-y-6 transition-opacity"}>
              <DailyChart days={data.by_day} />
              <FeatureTable rows={data.by_feature} />
              <RecentTable rows={data.recent} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
