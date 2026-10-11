import { useState } from "react";
import { Stethoscope } from "lucide-react";
import clsx from "clsx";
import { getPatientEncounters } from "../api/client";
import { Badge, Card, Empty, EncounterBadge, PanelSkeleton, Retry } from "../components";
import { useApi, useLiveRefresh } from "../hooks";
import { formatDuration, humanize } from "../lib/format";
import { formatStamp } from "./common";
import { TimelinePanel } from "./TimelinePanel";

// Every visit for the patient. Selecting a row shows that visit's timeline below.
export function EncountersPanel({ patientId }) {
  const [selectedId, setSelectedId] = useState(null);
  const { data, error, loading, reload, refresh } = useApi(() => getPatientEncounters(patientId), [patientId]);
  useLiveRefresh(refresh, { patientId });

  const selected = data?.find((e) => e.id === selectedId) || data?.[0] || null;

  return (
    <div className="space-y-5">
      <Card
        title="Visits"
        subtitle={data ? `${data.length} ${data.length === 1 ? "visit" : "visits"}, newest first. Select a visit to see its timeline.` : "All visits"}
        padded={false}
      >
        {error ? (
          <Retry error={error} onRetry={reload} />
        ) : loading && !data ? (
          <PanelSkeleton rows={5} />
        ) : data.length === 0 ? (
          <Empty icon={Stethoscope} title="No visits recorded" />
        ) : (
          <div className="table-wrap max-h-[28rem] overflow-y-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Visit</th>
                  <th scope="col">Type</th>
                  <th scope="col">Source</th>
                  <th scope="col">Admitted</th>
                  <th scope="col">Discharged</th>
                  <th scope="col" className="text-right">
                    Length of stay
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.map((enc) => {
                  const isSelected = selected?.id === enc.id;
                  const select = () => setSelectedId(enc.id);
                  return (
                    <tr
                      key={enc.id}
                      className={clsx("row-link", isSelected && "bg-brand-50/60 hover:bg-brand-50/60")}
                      tabIndex={0}
                      aria-selected={isSelected}
                      onClick={select}
                      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), select())}
                    >
                      <td className="whitespace-nowrap">
                        <span className={clsx("font-mono text-xs", isSelected ? "font-semibold text-brand-800" : "text-ink-700")}>
                          {enc.external_id}
                        </span>
                        {isSelected && <span className="sr-only"> (selected)</span>}
                      </td>
                      <td>
                        <div className="flex items-center gap-1.5">
                          <EncounterBadge type={enc.admission_type} />
                          {enc.is_active && (
                            <Badge tone="ok" dot>
                              Current
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className="whitespace-nowrap text-ink-600">{humanize(enc.admit_source)}</td>
                      <td className="num whitespace-nowrap text-ink-600">{formatStamp(enc.admit_date)}</td>
                      <td className="whitespace-nowrap">
                        {enc.discharge_date ? (
                          <>
                            <p className="num text-ink-600">{formatStamp(enc.discharge_date)}</p>
                            <p
                              className={clsx(
                                "text-xs",
                                ["expired", "left_against_advice"].includes(enc.discharge_disposition)
                                  ? "font-medium text-critical-700"
                                  : "text-ink-500",
                              )}
                            >
                              {humanize(enc.discharge_disposition)}
                            </p>
                          </>
                        ) : (
                          <span className="text-ink-500">Still here</span>
                        )}
                      </td>
                      <td className="num whitespace-nowrap text-right">
                        {formatDuration(enc.length_of_stay_hours)}
                        {enc.is_active && <p className="text-xs text-ink-500">so far</p>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selected && (
        <TimelinePanel
          patientId={patientId}
          encounterId={selected.id}
          title={`Timeline for visit ${selected.external_id}`}
          subtitle={`${humanize(selected.admission_type)} visit from ${formatStamp(selected.admit_date)}${
            selected.discharge_date ? ` to ${formatStamp(selected.discharge_date)}` : ", still in progress"
          }`}
        />
      )}
    </div>
  );
}
