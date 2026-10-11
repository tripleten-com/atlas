import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { FlaskConical } from "lucide-react";
import clsx from "clsx";
import { getLabs } from "../api/client";
import { Card, Empty, FlagBadge, Pagination, PanelSkeleton, Retry, Select, Tabs } from "../components";
import { useApi, useLiveRefresh } from "../hooks";
import { formatDateTime } from "../lib/format";
import {
  CHART,
  ChartFrame,
  ChartTooltip,
  formatRange,
  formatValue,
  formatStamp,
  panelLabel,
  rangeStatus,
  timeTickFormatter,
  niceScale,
} from "./common";

const PAGE_SIZE = 25;
const PANEL_ORDER = ["BMP", "CBC"];

// Every lab result for the patient: filters, a trend for one test, and the results table.
export function LabsPanel({ patientId }) {
  const [panel, setPanel] = useState("all");
  const [chosenTest, setChosenTest] = useState("");
  const [abnormalOnly, setAbnormalOnly] = useState(false);
  const [page, setPage] = useState(1);

  const { data, error, loading, reload, refresh } = useApi(() => getLabs(patientId, { limit: 5000 }), [patientId]);
  useLiveRefresh(refresh, { patientId });
  const labs = useMemo(() => data || [], [data]);

  const panelTabs = useMemo(() => {
    const counts = {};
    labs.forEach((l) => {
      counts[l.panel] = (counts[l.panel] || 0) + 1;
    });
    const present = Object.keys(counts).sort((a, b) => {
      const ia = PANEL_ORDER.indexOf(a);
      const ib = PANEL_ORDER.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
    });
    return [
      { id: "all", label: "All", count: labs.length },
      ...present.map((p) => ({ id: p, label: panelLabel(p), count: counts[p] })),
    ];
  }, [labs]);

  const inPanel = useMemo(() => (panel === "all" ? labs : labs.filter((l) => l.panel === panel)), [labs, panel]);

  const tests = useMemo(() => {
    const map = new Map();
    inPanel.forEach((l) => {
      const entry = map.get(l.test_code) || { code: l.test_code, name: l.test_name, count: 0, abnormal: 0 };
      entry.count += 1;
      if (l.flag !== "N") entry.abnormal += 1;
      map.set(l.test_code, entry);
    });
    return [...map.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [inPanel]);

  // Default trend: the most recent abnormal result in view, or else the most recent result.
  const defaultTest = (inPanel.find((l) => l.flag !== "N") || inPanel[0])?.test_code || "";
  const testCode = tests.some((t) => t.code === chosenTest) ? chosenTest : defaultTest;

  const trendResults = useMemo(() => labs.filter((l) => l.test_code === testCode), [labs, testCode]);

  const rows = useMemo(
    () => (abnormalOnly ? inPanel.filter((l) => l.flag !== "N") : inPanel),
    [inPanel, abnormalOnly],
  );
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  const pageRows = rows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const changePanel = (id) => {
    setPanel(id);
    setPage(1);
  };

  if (error && !data) {
    return (
      <Card title="Lab results">
        <Retry error={error} onRetry={reload} />
      </Card>
    );
  }
  if (loading && !data) {
    return (
      <Card title="Lab results" padded={false}>
        <PanelSkeleton rows={8} />
      </Card>
    );
  }
  if (labs.length === 0) {
    return (
      <Card title="Lab results">
        <Empty icon={FlaskConical} title="No lab results" msg="Results appear here once the lab reports them." />
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <div className="card">
        <div className="flex flex-col gap-3 border-b border-ink-100 p-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="overflow-x-auto">
            <Tabs tabs={panelTabs} value={panel} onChange={changePanel} />
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <Select
              label="Test to chart"
              hideLabel
              value={testCode}
              onChange={setChosenTest}
              options={tests.map((t) => ({
                value: t.code,
                label: `${t.name} (${t.count}${t.abnormal ? `, ${t.abnormal} abnormal` : ""})`,
              }))}
              className="sm:w-72"
            />
            <label className="inline-flex items-center gap-2 whitespace-nowrap text-sm text-ink-700">
              <input
                type="checkbox"
                checked={abnormalOnly}
                onChange={(e) => {
                  setAbnormalOnly(e.target.checked);
                  setPage(1);
                }}
                className="h-4 w-4 rounded border-ink-300 text-brand-700 focus:ring-brand-500"
              />
              Abnormal only
            </label>
          </div>
        </div>
        <LabTrend results={trendResults} />
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <h2 className="card-title">Results</h2>
            <p className="card-subtitle mt-0.5">
              {rows.length} {abnormalOnly ? "abnormal " : ""}
              {rows.length === 1 ? "result" : "results"}
              {panel !== "all" ? ` in ${panelLabel(panel)}` : ""}, newest first
            </p>
          </div>
        </div>
        {rows.length === 0 ? (
          <Empty icon={FlaskConical} title="No abnormal results" msg="Every result in this view is within its reference range." />
        ) : (
          <div className={clsx("transition-opacity", loading && "opacity-60")}>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Collected</th>
                    <th scope="col">Test</th>
                    <th scope="col" className="text-right">
                      Result
                    </th>
                    <th scope="col">Flag</th>
                    <th scope="col">Reference range</th>
                    <th scope="col">Panel</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((lab) => (
                    <tr
                      key={lab.id}
                      className={clsx("row-link", lab.test_code === testCode && "bg-brand-50/30")}
                      tabIndex={0}
                      onClick={() => setChosenTest(lab.test_code)}
                      onKeyDown={(e) => e.key === "Enter" && setChosenTest(lab.test_code)}
                      title="Chart this test"
                    >
                      <td className="num whitespace-nowrap text-ink-600">{formatStamp(lab.collected_at)}</td>
                      <td className="whitespace-nowrap font-medium text-ink-900">{lab.test_name}</td>
                      <td className="num whitespace-nowrap text-right">
                        <span className={lab.flag !== "N" ? "font-semibold text-ink-900" : undefined}>{formatValue(lab.value)}</span>{" "}
                        <span className="text-xs text-ink-500">{lab.unit}</span>
                      </td>
                      <td>
                        <FlagBadge flag={lab.flag} />
                      </td>
                      <td className="num whitespace-nowrap text-ink-500">
                        {formatRange(lab.ref_low, lab.ref_high)} <span className="text-xs">{lab.unit}</span>
                      </td>
                      <td className="whitespace-nowrap text-ink-600">{panelLabel(lab.panel)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={currentPage} pageSize={PAGE_SIZE} total={rows.length} onPage={setPage} />
          </div>
        )}
      </div>
    </div>
  );
}

function LabTrend({ results }) {
  const chart = useMemo(() => {
    if (!results.length) return null;
    const points = results
      .map((l) => ({ t: new Date(l.collected_at).getTime(), value: l.value, flag: l.flag }))
      .sort((a, b) => a.t - b.t);
    const latest = results[0];
    const low = latest.ref_low;
    const high = latest.ref_high;
    const values = points.map((p) => p.value);
    const lo = Math.min(...values, ...(low !== null ? [low] : []));
    const hi = Math.max(...values, ...(high !== null ? [high] : []));
    const scale = niceScale(lo, hi, 4, { floor: 0 });
    const abnormal = points.filter((p) => p.flag !== "N").length;
    return {
      points,
      latest,
      low,
      high,
      domain: scale.domain,
      ticks: scale.ticks,
      span: points[points.length - 1].t - points[0].t,
      summary:
        `${points.length} results from ${formatDateTime(points[0].t)} to ${formatDateTime(points[points.length - 1].t)}. ` +
        `Latest ${formatValue(latest.value)} ${latest.unit}, flagged ${latest.flag === "H" ? "high" : latest.flag === "L" ? "low" : "normal"}. ` +
        `${abnormal} outside the reference range of ${formatRange(low, high)} ${latest.unit}.`,
      abnormal,
    };
  }, [results]);

  if (!chart) return <Empty icon={FlaskConical} title="Pick a test to chart" />;
  const { points, latest, low, high, domain, ticks, span, summary, abnormal } = chart;
  const statusOf = (_key, value) => rangeStatus(value, low, high);

  return (
    <div className="card-body">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="card-title">{latest.test_name} trend</h2>
          <p className="card-subtitle mt-0.5">
            {points.length} {points.length === 1 ? "result" : "results"} · reference {formatRange(low, high)} {latest.unit} (shaded)
            {abnormal ? ` · ${abnormal} outside range` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-xs text-ink-500">Latest</span>
          <span className="num font-semibold text-ink-900">
            {formatValue(latest.value)} <span className="text-xs font-normal text-ink-500">{latest.unit}</span>
          </span>
          <FlagBadge flag={latest.flag} />
        </div>
      </div>
      <ChartFrame label={`${latest.test_name} trend`} summary={summary} className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 6, right: 12, bottom: 0, left: -8 }}>
            <CartesianGrid vertical={false} />
            {(low !== null || high !== null) && (
              <ReferenceArea
                y1={low ?? domain[0]}
                y2={high ?? domain[1]}
                fill={CHART.band}
                fillOpacity={CHART.bandOpacity}
                stroke="none"
                ifOverflow="hidden"
              />
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
              minTickGap={48}
            />
            <YAxis
              domain={domain}
              tick={CHART.tick}
              tickLine={false}
              axisLine={false}
              width={52}
              ticks={ticks}
              interval={0}
              tickFormatter={formatValue}
            />
            <Tooltip content={<ChartTooltip unit={latest.unit} statusOf={statusOf} />} cursor={{ stroke: "#c1c6d1", strokeDasharray: "3 3" }} />
            <Line
              dataKey="value"
              name={latest.test_name}
              type="monotone"
              stroke={CHART.brand}
              strokeWidth={1.75}
              isAnimationActive={false}
              dot={LabDot}
              activeDot={{ r: 4, strokeWidth: 0 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </ChartFrame>
    </div>
  );
}

function LabDot({ cx, cy, payload, index }) {
  if (cx === undefined || cy === undefined) return <g key={index} />;
  const abnormal = payload.flag !== "N";
  return (
    <circle
      key={index}
      cx={cx}
      cy={cy}
      r={abnormal ? 3.5 : 2.25}
      fill={abnormal ? CHART.critical : CHART.brand}
      stroke="#fff"
      strokeWidth={abnormal ? 1.5 : 0.5}
    />
  );
}
