import { Activity, ChevronRight, Code2, Database, FlaskConical, HeartPulse, LayoutDashboard, MessageSquareText, ShieldCheck, Sparkles, UserRound } from "lucide-react";
import { Link } from "react-router-dom";
import { getFacilities } from "../api/client";
import { Card, PageHeader, PanelSkeleton, Retry } from "../components";
import { useApi, useDocumentTitle } from "../hooks";
import { formatNumber } from "../lib/format";

// This page is the map of Atlas's AI features: the three that ship with the reference platform,
// the clinical questions and generative features a team can build next, the data every
// feature reads, and the seams in the code where a new one plugs in.

const BUILT = [
  {
    icon: Activity,
    title: "Readmission risk",
    kind: "Predictive",
    text: "For a patient whose inpatient or observation stay has ended: the chance of another admission within 30 days, as a probability and a band (low, medium, high). A scikit-learn model trained on the Test Facility's history. It appears in the AI insights section of the patient record.",
    states: "Shows a loading state, a 'not applicable' state when the patient has no finished stay, a 'model unavailable' state, and a low-confidence note in the medium band.",
    route: "GET /api/patients/{id}/risk/readmission",
  },
  {
    icon: HeartPulse,
    title: "Early warning",
    kind: "Predictive",
    text: "For an inpatient who is admitted right now: the chance that their vitals cross the deterioration line (two or more of heart rate above 120, respiratory rate above 24, SpO2 below 90, systolic pressure below 90, temperature above 38.5 \u00b0C) within the next 12 hours. A PyTorch sequence model over the last 24 hours of vitals, trained on a GPU, exported to ONNX and served on CPU. The two cards cover the two halves of a stay: this one while the patient is here, the readmission card after discharge.",
    states: "Shows a loading state, 'not applicable' when no inpatient stay is open, 'not enough data yet' before 12 hours of vitals, 'already deteriorating' when the line has been crossed (no forecast), 'model unavailable', and a low-confidence note in the medium band.",
    route: "GET /api/patients/{id}/risk/deterioration",
  },
  {
    icon: MessageSquareText,
    title: "Stay summary",
    kind: "Generative",
    text: "Four to six sentences about the patient's latest visit, written by a language model from rows the backend fetched: the encounter, the last reading of each vital, up to twenty lab results and the stay's medication orders. Every summary lists the rows it came from.",
    states: "Runs only when the clinician presses the button. Shows loading, 'no model configured', 'daily limit reached' and error states, and records every call, including refusals, on the Usage page.",
    route: "POST /api/patients/{id}/summary",
  },
];

// The clinical questions a team may choose for a predictive feature. The Test Facility is the
// training set for all of them; the live facilities are where the feature is watched.
const PREDICTIVE_MENU = [
  { code: "A", question: "Readmission within 30 days", unit: "One finished inpatient or observation stay", when: "At discharge", note: "Built here" },
  { code: "B", question: "Prolonged stay", unit: "One inpatient stay", when: "24 hours after admission", note: "Longer than the Test Facility median for that admission type" },
  { code: "C", question: "Deterioration during the stay", unit: "One inpatient stay", when: "12 hours after admission", note: "Built here as the early-warning model (fixed for everyone); a vital sign crossing a threshold you define and write down" },
  { code: "D", question: "Discharge to somewhere other than home", unit: "One inpatient stay", when: "24 hours after admission", note: "Disposition not home or home health" },
  { code: "E", question: "Your own question", unit: "You define it", when: "You define it", note: "Approved by your reviewer first" },
];

// The generative and agentic features a team may choose. Each one is grounded in rows the
// backend fetched for the caller's organisation; the model never sees anything else.
const GENERATIVE_MENU = [
  { code: "G1", feature: "Stay summary", kind: "Generative", grounding: "Encounter, vitals, labs, medications for that visit", action: "None", note: "Built here" },
  { code: "G2", feature: "Discharge summary draft the clinician edits", kind: "Generative", grounding: "Same as G1 plus doses given", action: "Saves a draft", note: "" },
  { code: "G3", feature: "Results triage note for a flagged lab", kind: "Generative", grounding: "The result, the patient's other results on that panel, active medications", action: "None", note: "" },
  { code: "A1", feature: "Record assistant that answers questions about one patient", kind: "Agentic", grounding: "Read-only tools: vitals, labs, medications, encounters", action: "None", note: "" },
  { code: "A2", feature: "Medication check that flags one interaction class", kind: "Agentic", grounding: "Tools for active medications and recent labs", action: "One note the clinician approves", note: "" },
];

const PLACES = [
  {
    icon: UserRound,
    title: "Patient record",
    text: "The AI insights section beside vitals, labs and medications. All three built features live here, each with the last five outputs it stored for that patient.",
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
        One file per feature, registered in <Code>backend/app/main.py</Code>. <Code>risk.py</Code>, <Code>early_warning.py</Code> and{" "}
        <Code>summary.py</Code> are the three built ones; copy their shape.
      </>
    ),
  },
  {
    path: "backend/app/ml.py",
    text: "The model's feature SQL, shared by the training export and the route, plus load_model() and the band thresholds.",
  },
  {
    path: "backend/app/ml_deterioration.py",
    text: "The early-warning model: the ONNX session, the hourly grid and 24-hour window for one patient, and the onset rule behind the 'deteriorating' state, kept in step with training/deterioration.py.",
  },
  {
    path: "backend/app/ai_store.py",
    text: (
      <>
        The document store (MongoDB): <Code>record_prediction()</Code> and <Code>record_generation()</Code> write one document per output,
        scoped by tenant, under the same <Code>request_id</Code> as the metering row; <Code>history()</Code> feeds the &quot;Earlier outputs&quot;
        list on every card. A store that is down never breaks a route: the response says <Code>stored: false</Code>.
      </>
    ),
  },
  {
    path: "backend/app/llm.py",
    text: (
      <>
        The language-model client. Any OpenAI-compatible endpoint, chosen by <Code>LLM_BASE_URL</Code>, <Code>LLM_API_KEY</Code> and{" "}
        <Code>LLM_MODEL</Code>.
      </>
    ),
  },
  { path: "frontend/src/api/client.js", text: "One function per endpoint. Components never build URLs themselves." },
  {
    path: "frontend/src/panels/",
    text: (
      <>
        A card per feature, listed in the AI insights section of <Code>frontend/src/pages/PatientDetail.jsx</Code>. <Code>RiskCard.jsx</Code>,{" "}
        <Code>EarlyWarningCard.jsx</Code> and <Code>SummaryCard.jsx</Code> show every state a card must handle; <Code>EarlierOutputs.jsx</Code> is
        the shared history list.
      </>
    ),
  },
  {
    path: "backend/app/metering.py",
    text: (
      <>
        <Code>record_usage()</Code> on every model call, including refused and failed ones. The Usage page and the daily token ceiling
        count these rows.
      </>
    ),
  },
  {
    path: "training/, models/, evals/",
    text: "The export and training scripts with each run's evidence (a CPU run for the readmission model, a GPU run for the early-warning model), the trained artifacts the API serves (joblib, and ONNX with its json), and the golden set with its evaluators.",
  },
];

function Code({ children }) {
  return <code className="rounded bg-ink-100 px-1.5 py-0.5 font-mono text-[12px] text-ink-800">{children}</code>;
}

function MenuTable({ columns, rows, caption }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.className}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.code}>
              {columns.map((c) => (
                <td key={c.key} className={c.cellClassName}>
                  {c.key === "note" && r.note === "Built here" ? (
                    <span className="badge badge-brand">Built here</span>
                  ) : (
                    r[c.key] || <span className="text-xs text-ink-400">&mdash;</span>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PREDICTIVE_COLUMNS = [
  { key: "code", label: "Code", className: "w-12", cellClassName: "font-mono text-xs text-ink-900" },
  { key: "question", label: "Question", cellClassName: "min-w-[12rem] text-ink-900" },
  { key: "unit", label: "One prediction per", cellClassName: "min-w-[12rem] text-ink-600" },
  { key: "when", label: "Predicted", cellClassName: "whitespace-nowrap text-ink-600" },
  { key: "note", label: "Notes", cellClassName: "min-w-[12rem] text-ink-600" },
];

const GENERATIVE_COLUMNS = [
  { key: "code", label: "Code", className: "w-12", cellClassName: "font-mono text-xs text-ink-900" },
  { key: "feature", label: "Feature", cellClassName: "min-w-[14rem] text-ink-900" },
  { key: "kind", label: "Kind", cellClassName: "whitespace-nowrap text-ink-600" },
  { key: "grounding", label: "Grounded in", cellClassName: "min-w-[14rem] text-ink-600" },
  { key: "action", label: "Writes anything?", cellClassName: "min-w-[8rem] text-ink-600" },
  { key: "note", label: "Notes", cellClassName: "text-ink-600" },
];

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
          read, train on or return another organisation&apos;s records. Train only on the Test Facility; its history never
          changes.
        </p>
      </div>
    </>
  );
}

export default function AiFeatures() {
  useDocumentTitle("AI features");

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI features"
        description="Three features ship with Atlas: two predictive, one generative. This page explains them, lists what a team can build next, and shows where a new feature plugs in."
      />

      <section aria-labelledby="built-heading">
        <h2 id="built-heading" className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink-900">
          <Sparkles aria-hidden="true" className="h-4 w-4 text-brand-600" />
          Built into the reference platform
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          {BUILT.map((f) => (
            <div key={f.title} className="card p-5">
              <div className="flex items-start justify-between gap-3">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-brand-50 text-brand-700 ring-1 ring-brand-100">
                  <f.icon aria-hidden="true" className="h-4 w-4" />
                </span>
                <span className="badge badge-neutral">{f.kind}</span>
              </div>
              <h3 className="mt-3 text-sm font-semibold text-ink-900">{f.title}</h3>
              <p className="mt-1 text-sm text-ink-600">{f.text}</p>
              <p className="mt-2 text-sm text-ink-600">{f.states}</p>
              <p className="mt-3">
                <Code>{f.route}</Code>
              </p>
            </div>
          ))}
        </div>
        <p className="mt-3 text-sm text-ink-600">
          Open any patient record to see the three cards. Both models were trained on the Test Facility; the summary needs a
          language-model key on the server, and says so when there is none.
        </p>
        <p className="mt-2 flex items-start gap-2 text-sm text-ink-600">
          <Database aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-brand-700" />
          <span>
            Every score and every summary is also written as a document to a separate store (MongoDB), scoped to your organisation,
            and each card lists the last five outputs it holds for that patient under &quot;Earlier outputs&quot;. Scores expire after 90
            days; generated text is kept. The clinical record itself stays in PostgreSQL, and the cards keep working, and say so, when the
            store is down.
          </span>
        </p>
      </section>

      <Card
        title="Predictive features you can build"
        subtitle="Pick one clinical question. Build the label from encounter dates and vitals yourself; use only data available at the prediction time."
        padded={false}
      >
        <MenuTable columns={PREDICTIVE_COLUMNS} rows={PREDICTIVE_MENU} caption="Clinical questions for a predictive feature" />
      </Card>

      <Card
        title="Generative and agentic features you can build"
        subtitle="Pick one. The model sees only rows the backend fetched for the signed-in organisation, and every call is metered."
        padded={false}
      >
        <MenuTable columns={GENERATIVE_COLUMNS} rows={GENERATIVE_MENU} caption="Generative and agentic feature options" />
      </Card>

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
        <p className="border-t border-ink-100 px-5 py-3 text-xs text-ink-600">
          The repository&apos;s <Code>docs/ADDING_A_FEATURE.md</Code> walks through the whole path, and{" "}
          <Link to="/usage" className="font-medium text-brand-700 hover:underline">
            Usage
          </Link>{" "}
          shows what every feature has cost so far.
        </p>
      </details>
    </div>
  );
}
