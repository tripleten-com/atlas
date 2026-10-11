import { Activity, BedDouble, Info } from "lucide-react";
import { getReadmissionRisk } from "../api/client";
import { Badge, Card, Empty, Retry, Spinner } from "../components";
import { useApi } from "../hooks";
import { formatDate, formatPercent } from "../lib/format";

// How each band reads. The text label carries the meaning; colour only reinforces it.
const BANDS = {
  low: { label: "Low risk", tone: "ok" },
  medium: { label: "Medium risk", tone: "warn" },
  high: { label: "High risk", tone: "high" },
};

// The predictive AI feature: chance that the patient comes back (an inpatient, observation or
// emergency encounter) within 30 days of their most recent discharge. The API answers 404 when
// the patient has never had an inpatient or observation stay, 404 "currently admitted" when a
// later stay is still open, and 503 when no model file is installed; each gets its own message.
//
// The score is only a forecast while `outcome` is "pending". For an older discharge the record
// already knows what happened, so the card shows that first and the score as what the model said
// at the time. Presenting a months-old score as a live risk would be dishonest.
export function RiskCard({ patientId }) {
  const { data, error, loading, reload } = useApi(() => getReadmissionRisk(patientId), [patientId]);

  return (
    <Card title="30-day readmission risk" subtitle="Predicted at the most recent discharge" className="h-full">
      {loading && !data ? (
        <div className="flex justify-center py-6">
          <Spinner label="Scoring this patient" />
        </div>
      ) : error ? (
        <RiskError error={error} onRetry={reload} />
      ) : (
        <RiskResult data={data} />
      )}
    </Card>
  );
}

function RiskError({ error, onRetry }) {
  if (error.status === 404 && /currently admitted/.test(error.message)) {
    return (
      <Empty
        icon={BedDouble}
        title="Not applicable while admitted"
        msg="This patient is in hospital now. The score is made at discharge, so it will apply once this stay ends."
        className="py-6"
      />
    );
  }
  if (error.status === 404) {
    // Not applicable: the model only scores discharged inpatient or observation stays.
    return (
      <Empty
        icon={Activity}
        title="Not applicable"
        msg="This patient has no discharged inpatient or observation stay to score."
        className="py-6"
      />
    );
  }
  if (error.status === 503) {
    return (
      <Empty
        icon={Info}
        title="Model unavailable"
        msg="No readmission model is installed on the server. Predictions resume once one is deployed."
        className="py-6"
      />
    );
  }
  return <Retry error={error} onRetry={onRetry} />;
}

// One sentence per outcome state, so the reader knows whether the number is a forecast or history.
function outcomeText(data) {
  if (data.outcome === "readmitted") {
    return `Outcome known: the patient came back on ${formatDate(data.readmitted_at)}, inside the 30-day window after the discharge on ${formatDate(data.as_of)}. The score is what the model said at that discharge.`;
  }
  if (data.outcome === "not_readmitted") {
    return `Outcome known: the 30-day window after the discharge on ${formatDate(data.as_of)} closed on ${formatDate(data.window_closes_at)} with no return recorded. The score is what the model said at that discharge.`;
  }
  return `Chance of coming back (an admission or an emergency visit) within 30 days of the discharge on ${formatDate(data.as_of)}. The window is open until ${formatDate(data.window_closes_at)}.`;
}

function RiskResult({ data }) {
  const band = BANDS[data.band] || { label: data.band, tone: "neutral" };
  const pending = data.outcome === "pending";
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <p className="num text-3xl font-semibold text-ink-900">{formatPercent(data.probability, 0)}</p>
        <Badge tone={band.tone} dot>
          {band.label}
        </Badge>
        {!pending && (
          <Badge tone="neutral">{data.outcome === "readmitted" ? "Came back" : "Window closed"}</Badge>
        )}
      </div>
      <p className="text-sm text-ink-600">{outcomeText(data)}</p>
      {pending && data.band === "medium" && (
        <p className="flex items-start gap-2 rounded-lg bg-caution-50 px-3 py-2 text-xs text-caution-700 ring-1 ring-inset ring-caution-100">
          <Info aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Low confidence: scores in the middle band are the least reliable. Weigh it alongside the clinical picture.
          </span>
        </p>
      )}
      <p className="border-t border-ink-100 pt-3 text-[11px] text-ink-500">
        Model trained on Test Facility data{data.trained_at ? `, ${formatDate(data.trained_at)}` : ""} · {data.model_version} ·{" "}
        {data.features_used.length} features · counts prior stays, so a facility with only days of history scores everyone as a first admission
      </p>
    </div>
  );
}
