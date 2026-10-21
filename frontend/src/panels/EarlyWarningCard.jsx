import { Activity, AlertTriangle, BedDouble, Clock, Info } from "lucide-react";
import { getDeteriorationRisk } from "../api/client";
import { Badge, Card, Empty, Retry, Spinner } from "../components";
import { useApi } from "../hooks";
import { formatDate, formatDateTime, formatPercent } from "../lib/format";
import { EarlierOutputs } from "./EarlierOutputs";

// How each band reads. The text label carries the meaning; colour only reinforces it.
const BANDS = {
  low: { label: "Low risk", tone: "ok" },
  medium: { label: "Medium risk", tone: "warn" },
  high: { label: "High risk", tone: "high" },
};

// Below this score the number is not a calibrated chance. The reference model was trained with
// class weighting (pos_weight about 65), and its calibration table (training_run.json) shows
// observed rates of 0.7 to 5.4 percent in every bin from 0.2 to 0.9; only the top bin came close
// to its score. So the low-confidence note shows for every scored answer under this line, not
// just for the medium band.
const CALIBRATED_FROM = 0.9;

// The second predictive AI feature: for an inpatient who is admitted right now, a score for how
// likely their vitals are to cross the deterioration line within the next 12 hours. The
// readmission card answers for a discharged stay; this one answers for an open one. The API
// answers 404 when the patient
// has no open inpatient stay, 503 when no model file is installed, and 200 with one of three
// states: "scored" (a forecast), "insufficient_data" (the stay is younger than 12 hours of vitals,
// no forecast yet) and "deteriorating" (the onset rule already fired, so there is nothing left to
// forecast; the card shows the hour it fired). Each state gets its own words.
//
// The clock is the clinical one: `as_of` is the hour after the latest vitals measurement, so a
// Test Facility patient whose data stops on the seed day is scored as of that hour, not as of now.
//
// Under the result, "Earlier outputs" lists the last five answers for this patient from the
// document store; `stored: false` on the result means this one was not written there.
export function EarlyWarningCard({ patientId }) {
  const { data, error, loading, reload } = useApi(() => getDeteriorationRisk(patientId), [patientId]);

  return (
    <Card title="Early warning: deterioration" subtitle="Next 12 hours of the current inpatient stay" className="h-full">
      {loading && !data ? (
        <div className="flex justify-center py-6">
          <Spinner label="Reading the last 24 hours of vitals" />
        </div>
      ) : error ? (
        <EarlyWarningError error={error} onRetry={reload} />
      ) : (
        <>
          <EarlyWarningResult data={data} />
          <div className="mt-3">
            <EarlierOutputs patientId={patientId} feature="early-warning" refreshKey={data} />
          </div>
        </>
      )}
    </Card>
  );
}

function EarlyWarningError({ error, onRetry }) {
  if (error.status === 404) {
    // Not applicable: the model only looks at an open inpatient stay.
    return (
      <Empty
        icon={BedDouble}
        title="Not applicable"
        msg="This patient is not admitted as an inpatient right now. The early-warning score applies only during an open inpatient stay."
        className="py-6"
      />
    );
  }
  if (error.status === 503) {
    return (
      <Empty
        icon={Info}
        title="Model unavailable"
        msg="No early-warning model is installed on the server. Predictions resume once one is deployed."
        className="py-6"
      />
    );
  }
  return <Retry error={error} onRetry={onRetry} />;
}

function EarlyWarningResult({ data }) {
  if (data.state === "insufficient_data") {
    return (
      <>
        <Empty
          icon={Clock}
          title="Not enough data yet"
          msg={`The model needs 12 hours of the stay with vitals on file before it forecasts; this stay has ${data.hours_of_vitals} hour${data.hours_of_vitals === 1 ? "" : "s"} with a measurement so far. Live facilities start with a short history, so this card fills in after a day of feed.`}
          className="py-6"
        />
        <Footer data={data} />
      </>
    );
  }
  if (data.state === "deteriorating") {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <AlertTriangle aria-hidden="true" className="h-6 w-6 text-critical-700" />
          <Badge tone="high" dot>
            Already deteriorating
          </Badge>
        </div>
        <p className="text-sm text-ink-600">
          The vitals crossed the deterioration line at {formatDateTime(data.as_of)}. There is
          nothing left to forecast: the event this model predicts has already happened, so no probability is shown.
        </p>
        <Footer data={data} />
      </div>
    );
  }
  return <Scored data={data} />;
}

function Scored({ data }) {
  const band = BANDS[data.band] || { label: data.band, tone: "neutral" };
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <p className="num text-3xl font-semibold text-ink-900">{formatPercent(data.probability, 0)}</p>
        <Badge tone={band.tone} dot>
          {band.label}
        </Badge>
        {data.alert ? (
          <Badge tone="high">Alert: above threshold</Badge>
        ) : (
          <Badge tone="neutral">No alert</Badge>
        )}
      </div>
      <p className="text-sm text-ink-600">
        Model score for the vitals crossing the deterioration line (two or more of: heart rate above 120, respiratory rate above 24, SpO2
        below 90, systolic pressure below 90, temperature above 38.5 °C) within 12 hours of {formatDateTime(data.as_of)}, read from the 24
        hours before it. The score ranks patients; it is not the observed chance.
      </p>
      {data.probability < CALIBRATED_FROM && (
        <p className="flex items-start gap-2 rounded-lg bg-caution-50 px-3 py-2 text-xs text-caution-700 ring-1 ring-inset ring-caution-100">
          <Info aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Low confidence: on the test set, stays scored below 90% went on to deteriorate far less often than the score suggests (under 6% in
            every bin; the model's run record has the table). Treat the alert as a prompt to look, not as a probability.
          </span>
        </p>
      )}
      <Footer data={data} />
    </div>
  );
}

// The provenance line every state shares: which stay, which hour, which model.
function Footer({ data }) {
  return (
    <p className="flex items-start gap-1.5 border-t border-ink-100 pt-3 text-[11px] text-ink-500">
      <Activity aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
      <span>
        Hour {data.hours_admitted} of the stay, vitals in {data.hours_of_vitals} of them · as of {formatDateTime(data.as_of)} · model trained on
        Test Facility data{data.trained_at ? `, ${formatDate(data.trained_at)}` : ""} · {data.model_version} · probabilities run high
        (class weighting), so the alert threshold matters more than the number
        {data.stored === false && " · not saved to the AI history (document store unavailable)"}
      </span>
    </p>
  );
}
