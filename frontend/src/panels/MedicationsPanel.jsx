import { useMemo, useState } from "react";
import { Pill } from "lucide-react";
import { getMedications } from "../api/client";
import { Badge, Card, Empty, PanelSkeleton, Retry, Tabs } from "../components";
import { useApi, useLiveRefresh, useNow } from "../hooks";
import { formatNumber, formatRelative } from "../lib/format";
import { formatStamp, formatValue } from "./common";

const FILTERS = {
  active: (m) => m.status === "active",
  discontinued: (m) => m.status === "discontinued",
  any: () => true,
};

// Every medication order for the patient, active ones first.
export function MedicationsPanel({ patientId }) {
  const now = useNow();
  const [status, setStatus] = useState("active");
  const { data, error, loading, reload, refresh } = useApi(() => getMedications(patientId, { status: "any" }), [patientId]);
  useLiveRefresh(refresh, { patientId });

  const meds = useMemo(() => data || [], [data]);
  const rows = useMemo(() => meds.filter(FILTERS[status]), [meds, status]);
  const tabs = [
    { id: "active", label: "Active", count: data ? meds.filter(FILTERS.active).length : undefined },
    { id: "discontinued", label: "Stopped", count: data ? meds.filter(FILTERS.discontinued).length : undefined },
    { id: "any", label: "All", count: data ? meds.length : undefined },
  ];

  return (
    <Card
      title="Medication orders"
      subtitle="Dose, route and frequency as ordered. Doses given come from administration records."
      padded={false}
      actions={<Tabs size="sm" tabs={tabs} value={status} onChange={setStatus} />}
    >
      {error ? (
        <Retry error={error} onRetry={reload} />
      ) : loading && !data ? (
        <PanelSkeleton rows={6} />
      ) : rows.length === 0 ? (
        <Empty
          icon={Pill}
          title={status === "active" ? "No active medications" : status === "discontinued" ? "No stopped medications" : "No medication orders"}
          msg={status === "active" && meds.length ? "Earlier orders are under Stopped." : undefined}
        />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Drug</th>
                <th scope="col">Dose / route / frequency</th>
                <th scope="col">Status</th>
                <th scope="col">Ordered</th>
                <th scope="col">Ended</th>
                <th scope="col" className="text-right">
                  Doses given
                </th>
                <th scope="col">Last given</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((med) => (
                <tr key={med.id}>
                  <td>
                    <p className="whitespace-nowrap font-medium text-ink-900">{med.drug_name}</p>
                    <p className="whitespace-nowrap text-xs text-ink-500">{med.drug_class}</p>
                  </td>
                  <td className="num whitespace-nowrap">
                    {formatValue(med.dose)} {med.dose_unit} · {med.route} · {med.frequency}
                  </td>
                  <td>
                    {med.status === "active" ? (
                      <Badge tone="ok" dot>
                        Active
                      </Badge>
                    ) : (
                      <Badge tone="neutral">Stopped</Badge>
                    )}
                  </td>
                  <td className="num whitespace-nowrap text-ink-600">{formatStamp(med.ordered_at)}</td>
                  <td className="num whitespace-nowrap text-ink-600">{med.ended_at ? formatStamp(med.ended_at) : "—"}</td>
                  <td className="num text-right">{formatNumber(med.administrations)}</td>
                  <td className="num whitespace-nowrap text-ink-600">
                    {med.last_administered_at ? (
                      <span title={formatStamp(med.last_administered_at)}>{formatRelative(med.last_administered_at, now)}</span>
                    ) : (
                      <span className="text-ink-400">Not given</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
