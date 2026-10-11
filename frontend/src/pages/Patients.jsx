import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Users } from "lucide-react";
import { getPatients } from "../api/client";
import {
  Avatar,
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
import { formatDate, formatNumber, formatRelative, humanize } from "../lib/format";

const STATUS_TABS = [
  { id: "any", label: "All" },
  { id: "admitted", label: "Currently admitted" },
  { id: "inpatient", label: "Inpatient" },
  { id: "emergency", label: "Emergency" },
  { id: "not_admitted", label: "Not admitted" },
];

const SORTS = [
  { value: "recent", label: "Most recent visit" },
  { value: "name", label: "Name" },
  { value: "age", label: "Age (oldest first)" },
  { value: "mrn", label: "MRN" },
];

const PAGE_SIZE = 25;

export default function Patients() {
  useDocumentTitle("Patients");
  const navigate = useNavigate();
  const { facilityId, scopeLabel } = useFacility();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const status = params.get("status") || "any";
  const sort = params.get("sort") || "recent";
  const page = Number(params.get("page") || 1);
  const debouncedQ = useDebounced(q, 300);

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
    () => getPatients({ q: debouncedQ, facility_id: facilityId, status, sort, page, page_size: PAGE_SIZE }),
    [debouncedQ, facilityId, status, sort, page],
    { keepPrevious: true },
  );
  useLiveRefresh(refresh, { facilityId, throttleMs: 8000 });

  return (
    <div>
      <PageHeader
        title="Patients"
        description={`Everyone with a record at ${scopeLabel === "All facilities" ? "your facilities" : scopeLabel}.`}
      />

      <div className="card">
        <div className="flex flex-col gap-3 border-b border-ink-100 p-4 lg:flex-row lg:items-center lg:justify-between">
          <Tabs tabs={STATUS_TABS} value={status} onChange={(id) => update({ status: id === "any" ? "" : id })} />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <SearchInput value={q} onChange={setQ} placeholder="Name or MRN" className="sm:w-64" label="Search patients" />
            <Select
              label="Sort by"
              hideLabel
              value={sort}
              onChange={(v) => update({ sort: v === "recent" ? "" : v })}
              options={SORTS}
              className="sm:w-48"
            />
          </div>
        </div>

        {error ? (
          <Retry error={error} onRetry={reload} />
        ) : loading && !data ? (
          <PanelSkeleton rows={8} />
        ) : data.items.length === 0 ? (
          <Empty icon={Users} title="No patients match" msg="Try a different search, or clear the filters." />
        ) : (
          <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Patient</th>
                    <th scope="col">Age / sex</th>
                    <th scope="col">Status</th>
                    <th scope="col">Facility</th>
                    <th scope="col" className="text-right">
                      Visits
                    </th>
                    <th scope="col">Last visit</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((p) => (
                    <tr
                      key={p.id}
                      className="row-link"
                      onClick={() => navigate(`/patients/${p.id}`)}
                      onKeyDown={(e) => e.key === "Enter" && navigate(`/patients/${p.id}`)}
                      tabIndex={0}
                    >
                      <td>
                        <div className="flex items-center gap-3">
                          <Avatar name={p.name} size="sm" />
                          <div className="min-w-0">
                            <p className="truncate font-medium text-ink-900">{p.name}</p>
                            <p className="truncate text-xs text-ink-500">MRN {p.mrn}</p>
                          </div>
                        </div>
                      </td>
                      <td className="num whitespace-nowrap">
                        {p.age} · {humanize(p.sex)}
                        <p className="text-xs text-ink-500">Born {formatDate(p.date_of_birth)}</p>
                      </td>
                      <td>
                        <EncounterBadge type={p.status} />
                      </td>
                      <td className="whitespace-nowrap text-ink-600">{p.facility_name}</td>
                      <td className="num text-right">{formatNumber(p.encounter_count)}</td>
                      <td className="whitespace-nowrap text-ink-600">
                        {p.last_encounter_at ? formatDate(p.last_encounter_at) : "—"}
                        <p className="text-xs text-ink-400">{p.last_encounter_at ? formatRelative(p.last_encounter_at) : ""}</p>
                      </td>
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
