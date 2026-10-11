import { ChevronRight, Code2, FlaskConical, LayoutDashboard, ShieldCheck, Sparkles, UserRound } from "lucide-react";
import { getFacilities } from "../api/client";
import { Card, PageHeader, PanelSkeleton, Retry } from "../components";
import { useApi, useDocumentTitle } from "../hooks";
import { formatNumber } from "../lib/format";

const PLACES = [
  {
    icon: UserRound,
    title: "Patient record",
    text: "Panels beside vitals, labs and medications, such as a risk score or a summary of the stay.",
  },
  {
    icon: LayoutDashboard,
    title: "Overview",
    text: "Organisation-wide signals on the dashboard, such as patients most likely to need attention.",
  },
  {
    icon: FlaskConical,
    title: "Results worklist",
    text: "Help with reviewing lab results, such as ranking abnormal results by urgency.",
  },
];

const TABLES = [
  { name: "patients", grain: "One row per patient", holds: "MRN, name, sex, date of birth, age, facility" },
  {
    name: "encounters",
    grain: "One row per visit",
    holds: "Admission type and source, admit and discharge times, discharge disposition",
  },
  { name: "vitals", grain: "One row per measurement", holds: "Heart rate, blood pressure, respiratory rate, temperature, SpO2" },
  { name: "labs", grain: "One row per test result", holds: "Test, LOINC code, value, unit, reference range, high or low flag" },
  { name: "medications", grain: "One row per order", holds: "Drug, class, dose, route, frequency, active or discontinued" },
  {
    name: "medication_administrations",
    grain: "One row per dose given",
    holds: "Which order, dose given, time given",
  },
];

const SEAMS = [
  {
    path: "backend/app/routers/",
    text: (
      <>
        Add a router for the feature, then register it in <Code>backend/app/main.py</Code>.
      </>
    ),
  },
  { path: "frontend/src/api/client.js", text: "Add one function per new endpoint. Components never build URLs themselves." },
  {
    path: "frontend/src/panels/",
    text: (
      <>
        Add a panel component and list it in <Code>frontend/src/pages/PatientDetail.jsx</Code> so it shows on the patient record.
      </>
    ),
  },
  {
    path: "backend/app/metering.py",
    text: (
      <>
        Call <Code>record_usage()</Code> on every model or LLM call so it shows on the Usage page.
      </>
    ),
  },
  {
    path: ".env",
    text: (
      <>
        Set <Code>MODEL_PATH</Code> for a trained model file and the <Code>LLM_*</Code> settings for a language model provider.
      </>
    ),
  },
];

function Code({ children }) {
  return <code className="rounded bg-ink-100 px-1.5 py-0.5 font-mono text-[12px] text-ink-800">{children}</code>;
}

function DataTable() {
  const { data, error, loading, reload } = useApi(() => getFacilities(), []);
  const count = (key) => (data ? data.reduce((sum, f) => sum + (f[key] || 0), 0) : null);
  const live = {
    patients: data && `${formatNumber(count("patient_count"))} patients`,
    encounters: data && `${formatNumber(count("active_encounters"))} active now`,
  };

  if (error) return <Retry error={error} onRetry={reload} />;
  if (loading && !data) return <PanelSkeleton rows={6} />;
  return (
    <>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Table</th>
              <th scope="col">Grain</th>
              <th scope="col">What it holds</th>
              <th scope="col" className="text-right">
                In your organisation
              </th>
            </tr>
          </thead>
          <tbody>
            {TABLES.map((t) => (
              <tr key={t.name}>
                <td className="whitespace-nowrap font-mono text-xs text-ink-900">{t.name}</td>
                <td className="whitespace-nowrap text-ink-600">{t.grain}</td>
                <td className="min-w-[16rem] text-ink-600">{t.holds}</td>
                <td className="num whitespace-nowrap text-right text-ink-800">{live[t.name] || <span className="text-xs text-ink-400">Not shown</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-start gap-2 border-t border-ink-100 px-5 py-3 text-xs text-ink-600">
        <ShieldCheck aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-brand-700" />
        <p>
          Every row carries a <Code>tenant_id</Code>. A feature must filter on the signed-in user&apos;s tenant and never
          read, train on or return another organisation&apos;s records.
        </p>
      </div>
    </>
  );
}

export default function AiFeatures() {
  useDocumentTitle("AI features");

  return (
    <div className="space-y-6">
      <PageHeader title="AI features" description="Predictive and generative features that run on your organisation's records." />

      <section className="card overflow-hidden">
        <div className="flex flex-col items-center gap-4 bg-gradient-to-b from-brand-50/70 to-white px-6 py-12 text-center">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-brand-700 shadow-card ring-1 ring-brand-100">
            <Sparkles aria-hidden="true" className="h-6 w-6" />
          </span>
          <div>
            <h2 className="text-lg font-semibold text-ink-900">No AI features connected yet</h2>
            <p className="mx-auto mt-1.5 max-w-lg text-sm text-ink-600">
              Once a feature is connected, it is listed here and its results appear where clinicians already work: on the
              patient record, the overview and the results worklist.
            </p>
          </div>
        </div>
      </section>

      <section aria-labelledby="where-heading">
        <h2 id="where-heading" className="mb-3 text-sm font-semibold text-ink-900">
          Where features appear
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          {PLACES.map((p) => (
            <div key={p.title} className="card p-5">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-brand-50 text-brand-700 ring-1 ring-brand-100">
                <p.icon aria-hidden="true" className="h-4 w-4" />
              </span>
              <h3 className="mt-3 text-sm font-semibold text-ink-900">{p.title}</h3>
              <p className="mt-1 text-sm text-ink-600">{p.text}</p>
            </div>
          ))}
        </div>
      </section>

      <Card
        title="Data available to features"
        subtitle="The clinical tables Atlas keeps for every facility, live and seeded."
        padded={false}
      >
        <DataTable />
      </Card>

      <details className="card group">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-3.5 [&::-webkit-details-marker]:hidden">
          <span className="flex items-center gap-2">
            <Code2 aria-hidden="true" className="h-4 w-4 text-ink-500" />
            <span className="text-sm font-semibold text-ink-900">For developers</span>
            <span className="text-xs text-ink-500">Where a new feature plugs in</span>
          </span>
          <ChevronRight aria-hidden="true" className="h-4 w-4 text-ink-400 transition-transform group-open:rotate-90" />
        </summary>
        <ul className="divide-y divide-ink-100 border-t border-ink-100">
          {SEAMS.map((s) => (
            <li key={s.path} className="flex flex-col gap-1 px-5 py-3 sm:flex-row sm:items-baseline sm:gap-4">
              <span className="shrink-0 sm:w-64">
                <Code>{s.path}</Code>
              </span>
              <p className="text-sm text-ink-600">{s.text}</p>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
