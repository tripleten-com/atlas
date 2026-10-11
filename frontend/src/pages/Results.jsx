import { useNavigate, useSearchParams } from "react-router-dom";
import { FlaskConical } from "lucide-react";
import { getReference, getResults } from "../api/client";
import {
  Avatar,
  Badge,
  Empty,
  FlagBadge,
  PageHeader,
  Pagination,
  PanelSkeleton,
  Retry,
  Select,
  Tabs,
} from "../components";
import { useFacility } from "../context/FacilityContext";
import { useApi, useDocumentTitle, useLiveRefresh, useNow } from "../hooks";
import { formatDateTime, formatNumber, formatRelative } from "../lib/format";

const FLAG_TABS = [
  { id: "abnormal", label: "Abnormal" },
  { id: "H", label: "High" },
  { id: "L", label: "Low" },
  { id: "all", label: "All" },
];

const LOOKBACK = [
  { value: "24", label: "Last 24 hours" },
  { value: "72", label: "Last 72 hours" },
  { value: "168", label: "Last 7 days" },
  { value: "720", label: "Last 30 days" },
];

const PAGE_SIZE = 50;

// Values arrive as floats (133.0); show them the way a lab report would.
function formatValue(value) {
  if (value === null || value === undefined) return "—";
  return formatNumber(Number(value.toFixed(2)));
}

function formatRange(r) {
  if (r.ref_low == null && r.ref_high == null) return "—";
  if (r.ref_low == null) return `< ${formatValue(r.ref_high)} ${r.unit || ""}`;
  if (r.ref_high == null) return `> ${formatValue(r.ref_low)} ${r.unit || ""}`;
  return `${formatValue(r.ref_low)}–${formatValue(r.ref_high)} ${r.unit || ""}`;
}

export default function Results() {
  useDocumentTitle("Results");
  const navigate = useNavigate();
  const now = useNow();
  const { facilityId } = useFacility();
  const [params, setParams] = useSearchParams();
  const flag = FLAG_TABS.some((t) => t.id === params.get("flag")) ? params.get("flag") : "abnormal";
  const test = params.get("test") || "";
  const hours = LOOKBACK.some((o) => o.value === params.get("hours")) ? params.get("hours") : "72";
  const activeOnly = params.get("active") === "1";
  const page = Number(params.get("page") || 1);
  const showFacility = facilityId === null;

  const update = (changes) => {
    const next = new URLSearchParams(params);
    Object.entries(changes).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (!("page" in changes)) next.delete("page");
    setParams(next, { replace: true });
  };

  const reference = useApi(() => getReference(), []);
  const labs = reference.data?.labs || [];
  const testOptions = [
    { value: "", label: "All tests" },
    ...labs.map((l) => ({ value: l.code, label: l.label })),
    // Keep a test from the URL selectable even before the reference list loads.
    ...(test && !labs.some((l) => l.code === test) ? [{ value: test, label: test }] : []),
  ];

  const { data, error, loading, reload, refresh } = useApi(
    () =>
      getResults({
        facility_id: facilityId,
        flag,
        test_code: test,
        hours,
        active_only: activeOnly || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
    [facilityId, flag, test, hours, activeOnly, page],
    { keepPrevious: true },
  );
  useLiveRefresh(refresh, { facilityId, throttleMs: 8000 });

  const open = (r) => navigate(`/patients/${r.patient_id}`);

  return (
    <div>
      <PageHeader title="Results" description="Lab results outside their reference range, newest first." />

      <div className="card">
        <div className="flex flex-col gap-3 border-b border-ink-100 p-4 lg:flex-row lg:items-center lg:justify-between">
          <Tabs tabs={FLAG_TABS} value={flag} onChange={(id) => update({ flag: id === "abnormal" ? "" : id })} />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <Select
              label="Test"
              hideLabel
              value={test}
              onChange={(v) => update({ test: v })}
              options={testOptions}
              className="sm:w-52"
            />
            <Select
              label="Time window"
              hideLabel
              value={hours}
              onChange={(v) => update({ hours: v === "72" ? "" : v })}
              options={LOOKBACK}
              className="sm:w-40"
            />
            <label className="inline-flex items-center gap-2 whitespace-nowrap text-sm text-ink-700">
              <input
                type="checkbox"
                checked={activeOnly}
                onChange={(e) => update({ active: e.target.checked ? "1" : "" })}
                className="h-4 w-4 rounded border-ink-300 accent-brand-600"
              />
              Currently admitted only
            </label>
          </div>
        </div>

        {error ? (
          <Retry error={error} onRetry={reload} />
        ) : loading && !data ? (
          <PanelSkeleton rows={8} />
        ) : data.items.length === 0 ? (
          <Empty
            icon={FlaskConical}
            title="No results match"
            msg="Try a longer time window, another test, or clear the filters."
          />
        ) : (
          <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Patient</th>
                    <th scope="col">Test</th>
                    <th scope="col">Result</th>
                    <th scope="col">Reference range</th>
                    <th scope="col">Collected</th>
                    {showFacility && <th scope="col">Facility</th>}
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => (
                    <tr
                      key={r.id}
                      className="row-link"
                      onClick={() => open(r)}
                      onKeyDown={(e) => e.key === "Enter" && open(r)}
                      tabIndex={0}
                    >
                      <td>
                        <div className="flex items-center gap-3">
                          <Avatar name={r.patient_name} size="sm" />
                          <div className="min-w-0">
                            <p className="truncate font-medium text-ink-900">{r.patient_name}</p>
                            <p className="truncate text-xs text-ink-500">MRN {r.patient_mrn}</p>
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="whitespace-nowrap text-ink-900">{r.test_name}</span>
                          {r.panel && <Badge tone="neutral">{r.panel}</Badge>}
                        </div>
                      </td>
                      <td className="whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <span className="num font-semibold text-ink-900">
                            {formatValue(r.value)} <span className="font-normal text-ink-500">{r.unit}</span>
                          </span>
                          <FlagBadge flag={r.flag} />
                        </div>
                      </td>
                      <td className="num whitespace-nowrap text-ink-600">{formatRange(r)}</td>
                      <td className="whitespace-nowrap text-ink-600">
                        {formatDateTime(r.collected_at)}
                        <p className="text-xs text-ink-400">{formatRelative(r.collected_at, now)}</p>
                      </td>
                      {showFacility && <td className="whitespace-nowrap text-ink-600">{r.facility_name}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={(n) => update({ page: String(n) })} />
          </div>
        )}
      </div>
    </div>
  );
}
