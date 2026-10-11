import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ClipboardList } from "lucide-react";
import { getDashboardSummary, getEncounters } from "../api/client";
import {
  Badge,
  EncounterBadge,
  Empty,
  PageHeader,
  Pagination,
  PanelSkeleton,
  Retry,
  SearchInput,
  Select,
  Tabs,
} from "../components";
import { useFacility } from "../context/FacilityContext";
import { useApi, useDebounced, useDocumentTitle, useLiveRefresh } from "../hooks";
import { ENCOUNTER_TYPES, formatDateTime, formatDuration, formatNumber, humanize } from "../lib/format";

const STATE_TABS = [
  { id: "active", label: "Active" },
  { id: "discharged", label: "Discharged" },
  { id: "any", label: "All" },
];

const CENSUS_TYPES = ["inpatient", "observation", "emergency", "outpatient"];

const TYPES = [{ value: "", label: "All types" }, ...CENSUS_TYPES.map((t) => ({ value: t, label: ENCOUNTER_TYPES[t].label }))];

const CHIP_DOT = {
  inpatient: "bg-brand-500",
  observation: "bg-caution-600",
  emergency: "bg-critical-600",
  outpatient: "bg-ink-400",
};

const PAGE_SIZE = 25;

// Who is here right now, by visit type. Clicking a chip filters the table to that type.
function CensusChips({ facilityId, activeType, onPick, refreshKey }) {
  const { data, error, loading, refresh } = useApi(() => getDashboardSummary({ facility_id: facilityId }), [facilityId], {
    keepPrevious: true,
  });
  useEffect(() => {
    if (refreshKey) refresh();
  }, [refreshKey, refresh]);

  if (error) return null;
  return (
    <div role="group" className="mb-4 flex flex-wrap items-center gap-2" aria-label="Current census">
      <span className="mr-1 text-xs font-medium text-ink-500">Here now</span>
      {CENSUS_TYPES.map((type) => {
        const selected = activeType === type;
        return (
          <button
            key={type}
            type="button"
            aria-pressed={selected}
            onClick={() => onPick(selected ? "" : type)}
            className={
              "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs transition " +
              (selected
                ? "border-brand-300 bg-brand-50 text-brand-800"
                : "border-ink-200 bg-white text-ink-700 hover:border-ink-300 hover:bg-ink-50")
            }
          >
            <span aria-hidden="true" className={`h-2 w-2 rounded-full ${CHIP_DOT[type]}`} />
            {ENCOUNTER_TYPES[type].label}
            {loading && !data ? (
              <span className="inline-block h-3 w-4 animate-pulse rounded bg-ink-100" />
            ) : (
              <span className="num font-semibold text-ink-900">{formatNumber(data?.census?.[type] ?? 0)}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export default function Encounters() {
  useDocumentTitle("Encounters");
  const navigate = useNavigate();
  const { facilityId, scopeLabel } = useFacility();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const state = STATE_TABS.some((t) => t.id === params.get("state")) ? params.get("state") : "active";
  const type = CENSUS_TYPES.includes(params.get("type")) ? params.get("type") : "";
  const page = Number(params.get("page") || 1);
  const debouncedQ = useDebounced(q, 300);
  const [liveTick, setLiveTick] = useState(0);
  const showFacility = facilityId === null;

  const update = (changes) => {
    const next = new URLSearchParams(params);
    Object.entries(changes).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (!("page" in changes)) next.delete("page");
    setParams(next, { replace: true });
  };

  useEffect(() => {
    if ((params.get("q") || "") !== debouncedQ) update({ q: debouncedQ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  const { data, error, loading, reload, refresh } = useApi(
    () =>
      getEncounters({
        facility_id: facilityId,
        state,
        admission_type: type,
        q: debouncedQ,
        page,
        page_size: PAGE_SIZE,
      }),
    [facilityId, state, type, debouncedQ, page],
    { keepPrevious: true },
  );
  useLiveRefresh(
    () => {
      refresh();
      setLiveTick((n) => n + 1);
    },
    { facilityId, throttleMs: 8000 },
  );

  const open = (e) => navigate(`/patients/${e.patient_id}`);

  return (
    <div>
      <PageHeader
        title="Encounters"
        description={`Visits and stays at ${scopeLabel === "All facilities" ? "your facilities" : scopeLabel}, newest first.`}
      />

      <CensusChips facilityId={facilityId} activeType={type} onPick={(t) => update({ type: t })} refreshKey={liveTick} />

      <div className="card">
        <div className="flex flex-col gap-3 border-b border-ink-100 p-4 lg:flex-row lg:items-center lg:justify-between">
          <Tabs tabs={STATE_TABS} value={state} onChange={(id) => update({ state: id === "active" ? "" : id })} />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <SearchInput
              value={q}
              onChange={setQ}
              placeholder="Name, MRN or visit id"
              className="sm:w-64"
              label="Search encounters"
            />
            <Select
              label="Visit type"
              hideLabel
              value={type}
              onChange={(v) => update({ type: v })}
              options={TYPES}
              className="sm:w-44"
            />
          </div>
        </div>

        {error ? (
          <Retry error={error} onRetry={reload} />
        ) : loading && !data ? (
          <PanelSkeleton rows={8} />
        ) : data.items.length === 0 ? (
          <Empty icon={ClipboardList} title="No encounters match" msg="Try a different search, or clear the filters." />
        ) : (
          <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Patient</th>
                    <th scope="col">Visit</th>
                    <th scope="col">Type</th>
                    <th scope="col">Source</th>
                    <th scope="col">Admitted</th>
                    <th scope="col">Discharged</th>
                    <th scope="col" className="text-right">
                      Length of stay
                    </th>
                    {showFacility && <th scope="col">Facility</th>}
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((e) => (
                    <tr
                      key={e.id}
                      className="row-link"
                      onClick={() => open(e)}
                      onKeyDown={(ev) => ev.key === "Enter" && open(e)}
                      tabIndex={0}
                    >
                      <td>
                        <p className="truncate font-medium text-ink-900">{e.patient_name}</p>
                        <p className="truncate text-xs text-ink-500">MRN {e.patient_mrn}</p>
                      </td>
                      <td className="whitespace-nowrap font-mono text-xs text-ink-600">{e.external_id}</td>
                      <td>
                        <EncounterBadge type={e.admission_type} />
                      </td>
                      <td className="whitespace-nowrap text-ink-600">{humanize(e.admit_source)}</td>
                      <td className="whitespace-nowrap text-ink-600">{formatDateTime(e.admit_date)}</td>
                      <td className="whitespace-nowrap text-ink-600">
                        {e.discharge_date ? (
                          <>
                            {formatDateTime(e.discharge_date)}
                            <p className="text-xs text-ink-400">{humanize(e.discharge_disposition)}</p>
                          </>
                        ) : (
                          <Badge tone="ok">Still here</Badge>
                        )}
                      </td>
                      <td className="num whitespace-nowrap text-right">{formatDuration(e.length_of_stay_hours)}</td>
                      {showFacility && <td className="whitespace-nowrap text-ink-600">{e.facility_name}</td>}
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
