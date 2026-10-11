import { Link, useParams, useSearchParams } from "react-router-dom";
import { ChevronRight, Sparkles, UserX } from "lucide-react";
import { Empty, PanelBoundary, Retry, Skeleton, Tabs } from "../components";
import { useDocumentTitle, usePatient } from "../hooks";
import {
  ActiveMedicationsPanel,
  EncountersPanel,
  LabsPanel,
  LatestVitalsPanel,
  MedicationsPanel,
  PatientHeader,
  RecentActivityPanel,
  RecentLabsPanel,
  RiskCard,
  SummaryCard,
  TimelinePanel,
  VitalReadingsPanel,
  VitalsPanel,
} from "../panels";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "vitals", label: "Vitals" },
  { id: "labs", label: "Labs" },
  { id: "medications", label: "Medications" },
  { id: "visits", label: "Visits" },
  { id: "timeline", label: "Timeline" },
];

export default function PatientDetail() {
  const { patientId } = useParams();
  const { data: patient, error, loading, reload } = usePatient(patientId);
  useDocumentTitle(patient?.name || "Patient");

  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "overview";
  const setTab = (id) => {
    const next = new URLSearchParams(params);
    if (id === "overview") next.delete("tab");
    else next.set("tab", id);
    setParams(next, { replace: true });
  };

  // A different patient loaded into the same page: keep showing the skeleton, not the old record.
  const current = patient && String(patient.id) === String(patientId) ? patient : null;

  if (error && error.status === 404) {
    return (
      <div>
        <Breadcrumbs name="Not found" />
        <div className="card">
          <Empty
            icon={UserX}
            title="Patient not found"
            msg="This record does not exist or belongs to another organization."
            link={{ to: "/patients", label: "Back to patients" }}
          />
        </div>
      </div>
    );
  }

  if (error && !current) {
    return (
      <div>
        <Breadcrumbs name="Patient" />
        <div className="card">
          <Retry error={error} onRetry={reload} />
        </div>
      </div>
    );
  }

  if (!current) return <PatientDetailSkeleton />;

  return (
    <div aria-busy={loading}>
      <Breadcrumbs name={current.name} />

      <div className="space-y-5">
        <PanelBoundary name="Patient summary">
          <PatientHeader patient={current} />
        </PanelBoundary>

        <div className="-mx-1 overflow-x-auto px-1 pb-1">
          <Tabs
            tabs={TABS.map((t) => (t.id === "visits" ? { ...t, count: current.encounter_count } : t))}
            value={tab}
            onChange={setTab}
          />
        </div>

        <div role="tabpanel" aria-label={TABS.find((t) => t.id === tab).label} key={`${current.id}-${tab}`}>
          {tab === "overview" && (
            <div className="flex flex-col gap-5">
              {/* Patient-detail panels. Add a panel here, e.g. <RiskCard patientId={patient.id} /> */}
              <PanelBoundary name="Latest vital signs">
                <LatestVitalsPanel patient={patient} />
              </PanelBoundary>
              <AiInsights patientId={patient.id} />
              <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
                <div className="min-w-0 xl:col-span-8">
                  <PanelBoundary name="Vital sign trends">
                    <VitalsPanel patientId={patient.id} />
                  </PanelBoundary>
                </div>
                <div className="min-w-0 xl:col-span-4">
                  <PanelBoundary name="Active medications">
                    <ActiveMedicationsPanel patientId={patient.id} />
                  </PanelBoundary>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
                <div className="min-w-0 xl:col-span-7">
                  <PanelBoundary name="Recent lab results">
                    <RecentLabsPanel patientId={patient.id} />
                  </PanelBoundary>
                </div>
                <div className="min-w-0 xl:col-span-5">
                  <PanelBoundary name="Recent activity">
                    <RecentActivityPanel patientId={patient.id} />
                  </PanelBoundary>
                </div>
              </div>
            </div>
          )}

          {tab === "vitals" && (
            <div className="space-y-5">
              <div className="group expanded">
                <PanelBoundary name="Vital sign trends">
                  <VitalsPanel patientId={patient.id} />
                </PanelBoundary>
              </div>
              <PanelBoundary name="Recent readings">
                <VitalReadingsPanel patientId={patient.id} />
              </PanelBoundary>
            </div>
          )}

          {tab === "labs" && (
            <PanelBoundary name="Lab results">
              <LabsPanel patientId={patient.id} />
            </PanelBoundary>
          )}

          {tab === "medications" && (
            <PanelBoundary name="Medications">
              <MedicationsPanel patientId={patient.id} />
            </PanelBoundary>
          )}

          {tab === "visits" && (
            <PanelBoundary name="Visits">
              <EncountersPanel patientId={patient.id} />
            </PanelBoundary>
          )}

          {tab === "timeline" && (
            <PanelBoundary name="Timeline">
              <TimelinePanel patientId={patient.id} />
            </PanelBoundary>
          )}
        </div>
      </div>
    </div>
  );
}

// The AI insights area: one card per AI feature. Add your next card to the grid below
// (`AiFeaturesSlot` in ../panels is the empty placeholder this grid replaced).
function AiInsights({ patientId }) {
  return (
    <section aria-labelledby="ai-insights-title" className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 id="ai-insights-title" className="flex items-center gap-2 text-sm font-semibold text-ink-700">
          <Sparkles aria-hidden="true" className="h-4 w-4 text-brand-600" />
          AI insights
        </h2>
        <Link to="/ai" className="shrink-0 text-sm font-medium text-brand-700 hover:underline">
          About AI features
        </Link>
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <PanelBoundary name="Readmission risk">
          <RiskCard patientId={patientId} />
        </PanelBoundary>
        <PanelBoundary name="Stay summary">
          <SummaryCard patientId={patientId} />
        </PanelBoundary>
        {/* Your next AI card goes here */}
      </div>
    </section>
  );
}

function Breadcrumbs({ name }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-4 flex items-center gap-1 text-xs text-ink-500">
      <Link to="/patients" className="hover:text-ink-800">
        Patients
      </Link>
      <ChevronRight aria-hidden="true" className="h-3 w-3" />
      <span aria-current="page" className="truncate text-ink-700">
        {name}
      </span>
    </nav>
  );
}

// Loading state shaped like the page: header card, tabs, vitals strip, two columns.
function PatientDetailSkeleton() {
  return (
    <div role="status" aria-busy="true">
      <span className="sr-only">Loading patient record</span>
      <Skeleton className="mb-4 h-3 w-40" />
      <div className="space-y-5">
        <div className="card">
          <div className="flex flex-col gap-5 p-5 lg:flex-row lg:justify-between">
            <div className="flex items-start gap-4">
              <Skeleton className="h-12 w-12 rounded-full" />
              <div className="space-y-2.5">
                <Skeleton className="h-7 w-56" />
                <Skeleton className="h-4 w-72" />
                <div className="flex gap-2">
                  <Skeleton className="h-5 w-20 rounded-full" />
                  <Skeleton className="h-5 w-32 rounded-full" />
                  <Skeleton className="h-5 w-14 rounded-full" />
                </div>
              </div>
            </div>
            <Skeleton className="h-24 w-full rounded-lg lg:w-[300px]" />
          </div>
          <div className="grid grid-cols-1 gap-4 border-t border-ink-100 px-5 py-3 sm:grid-cols-3">
            <Skeleton className="h-6" />
            <Skeleton className="h-6" />
            <Skeleton className="h-6" />
          </div>
        </div>
        <Skeleton className="h-10 w-full max-w-xl rounded-lg" />
        <div className="card grid grid-cols-2 gap-4 p-5 sm:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-8 w-16" />
              <Skeleton className="h-3 w-24" />
            </div>
          ))}
        </div>
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
          <Skeleton className="h-80 rounded-xl xl:col-span-8" />
          <Skeleton className="h-80 rounded-xl xl:col-span-4" />
        </div>
      </div>
    </div>
  );
}
