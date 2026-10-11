import { Building2, CalendarClock, FlaskConical, Pill } from "lucide-react";
import clsx from "clsx";
import { Avatar, Badge, EncounterBadge, StatusDot } from "../components";
import { useFacility } from "../context/FacilityContext";
import { useNow } from "../hooks";
import { formatDate, formatDateTime, formatDuration, formatNumber, formatRelative, humanize } from "../lib/format";

// Who the patient is, where they are now, and three headline counts.
export function PatientHeader({ patient }) {
  const now = useNow();
  const { facilities } = useFacility();
  const facility = facilities.find((f) => f.id === patient.facility_id);
  const encounter = patient.current_encounter;
  // Use the clock for time in hospital so it keeps counting between refreshes.
  const hoursHere = encounter ? Math.max(0, (now - new Date(encounter.admit_date).getTime()) / 3_600_000) : null;

  return (
    <section aria-label="Patient summary" className="card overflow-hidden">
      <div className="flex flex-col gap-5 p-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex min-w-0 items-start gap-4">
          <Avatar name={patient.name} size="lg" />
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold tracking-tight text-ink-900">{patient.name}</h1>
            <p className="num mt-0.5 text-sm text-ink-600">
              {patient.age} y · {humanize(patient.sex)} · MRN {patient.mrn} · Born {formatDate(patient.date_of_birth)}
            </p>
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <EncounterBadge type={patient.status} />
              <Badge tone="neutral">
                <Building2 aria-hidden="true" className="h-3 w-3" />
                {patient.facility_name}
              </Badge>
              {facility &&
                (facility.data_source === "meridian" ? (
                  <span className="inline-flex items-center rounded-full bg-success-50 px-2 py-0.5 ring-1 ring-inset ring-success-100">
                    <StatusDot tone="ok" pulse label="Live" />
                  </span>
                ) : (
                  <Badge tone="warn" title="This facility holds a fixed training data set.">
                    Test data
                  </Badge>
                ))}
            </div>
          </div>
        </div>

        <div
          className={clsx(
            "shrink-0 rounded-lg px-4 py-3 lg:min-w-[300px]",
            encounter ? "bg-brand-50/60 ring-1 ring-inset ring-brand-100" : "bg-ink-50 ring-1 ring-inset ring-ink-100",
          )}
        >
          {encounter ? (
            <>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-brand-800">
                Current {humanize(encounter.admission_type).toLowerCase()} visit
              </p>
              <dl className="mt-2 grid grid-cols-2 gap-x-5 gap-y-2 text-sm">
                <div>
                  <dt className="text-xs text-ink-500">Admitted</dt>
                  <dd className="num font-medium text-ink-900">{formatDateTime(encounter.admit_date)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-500">Time in hospital</dt>
                  <dd className="num font-medium text-ink-900">{formatDuration(hoursHere)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-500">Admit source</dt>
                  <dd className="font-medium text-ink-900">{humanize(encounter.admit_source)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-500">Visit ID</dt>
                  <dd className="truncate font-mono text-xs font-medium leading-5 text-ink-900">{encounter.external_id}</dd>
                </div>
              </dl>
            </>
          ) : (
            <>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-500">Not currently admitted</p>
              <p className="mt-2 text-sm text-ink-700">
                {patient.last_encounter_at ? (
                  <>
                    Last visit <span className="num font-medium text-ink-900">{formatDate(patient.last_encounter_at)}</span>
                    <span className="num text-ink-500"> · {formatRelative(patient.last_encounter_at, now)}</span>
                  </>
                ) : (
                  "No visits on record"
                )}
              </p>
            </>
          )}
        </div>
      </div>

      <dl className="grid grid-cols-1 divide-y divide-ink-100 border-t border-ink-100 bg-ink-50/40 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <Stat icon={CalendarClock} label="Total visits" value={formatNumber(patient.encounter_count)} />
        <Stat icon={Pill} label="Active medications" value={formatNumber(patient.active_medications)} />
        <Stat
          icon={FlaskConical}
          label="Abnormal labs (7 days)"
          value={formatNumber(patient.abnormal_labs_7d)}
          alert={patient.abnormal_labs_7d > 0}
        />
      </dl>
    </section>
  );
}

function Stat({ icon: Icon, label, value, alert = false }) {
  return (
    <div className="flex items-center gap-3 px-5 py-3">
      <Icon aria-hidden="true" className={clsx("h-4 w-4 shrink-0", alert ? "text-critical-600" : "text-ink-400")} />
      <dt className="text-xs text-ink-500">{label}</dt>
      <dd className={clsx("num ml-auto text-lg font-semibold", alert ? "text-critical-700" : "text-ink-900")}>{value}</dd>
    </div>
  );
}
