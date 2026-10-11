import { Link } from "react-router-dom";
import { Pill } from "lucide-react";
import { getMedications } from "../api/client";
import { Card, Empty, PanelSkeleton, Retry } from "../components";
import { useApi, useLiveRefresh, useNow } from "../hooks";
import { formatNumber, formatRelative } from "../lib/format";
import { formatValue } from "./common";

// Medications the patient is on right now.
export function ActiveMedicationsPanel({ patientId }) {
  const now = useNow();
  const { data, error, loading, reload, refresh } = useApi(
    () => getMedications(patientId, { status: "active" }),
    [patientId],
  );
  useLiveRefresh(refresh, { patientId });

  return (
    <Card
      title="Active medications"
      subtitle={data ? `${data.length} active ${data.length === 1 ? "order" : "orders"}` : "Current orders"}
      padded={false}
      className="h-full"
      actions={
        <Link to="?tab=medications" className="text-xs font-medium text-brand-700 hover:underline">
          All orders
        </Link>
      }
    >
      {error ? (
        <Retry error={error} onRetry={reload} />
      ) : loading && !data ? (
        <PanelSkeleton rows={4} />
      ) : data.length === 0 ? (
        <Empty icon={Pill} title="No active medications" msg="Nothing is ordered for this patient right now." />
      ) : (
        <ul className="divide-y divide-ink-100">
          {data.map((med) => (
            <li key={med.id} className="px-5 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink-900">{med.drug_name}</p>
                  <p className="num truncate text-xs text-ink-600">
                    {formatValue(med.dose)} {med.dose_unit} · {med.route} · {med.frequency}
                  </p>
                </div>
                <span className="shrink-0 rounded-md bg-ink-50 px-1.5 py-0.5 text-[11px] text-ink-600 ring-1 ring-inset ring-ink-200">
                  {med.drug_class}
                </span>
              </div>
              <p className="num mt-1 text-[11px] text-ink-500">
                {med.last_administered_at
                  ? `Last given ${formatRelative(med.last_administered_at, now)} · ${formatNumber(med.administrations)} ${
                      med.administrations === 1 ? "dose" : "doses"
                    } given`
                  : "Not given yet"}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
