import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, FileText, Gauge, Info, Sparkles } from "lucide-react";
import { postStaySummary } from "../api/client";
import { Button, Card, Empty, Retry } from "../components";
import { formatNumber } from "../lib/format";
import { EarlierOutputs } from "./EarlierOutputs";

// The generative AI feature: a short summary of the patient's latest stay, written by a language
// model from rows the backend fetched. Nothing happens until the clinician presses the button,
// because every call costs tokens and counts against the organisation's daily ceiling.
//
// The API's answers and what this card shows for each:
//   200  the summary, with a "Sources" toggle listing the rows the model was given
//   404  not applicable (no visit to summarise)
//   429  daily token limit reached
//   503  no model configured on the server
//   anything else: an error with Retry
// "Earlier outputs" under the card lists the last five summaries for this patient from the
// document store, so a clinician can see what was written before pressing the button again.
export function SummaryCard({ patientId }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // A different patient on the same page: forget the previous summary.
  useEffect(() => {
    setResult(null);
    setError(null);
    setLoading(false);
  }, [patientId]);

  const summarise = () => {
    setLoading(true);
    setError(null);
    postStaySummary(patientId)
      .then(setResult)
      .catch(setError)
      .finally(() => setLoading(false));
  };

  return (
    <Card
      title="Stay summary"
      subtitle="Written by a language model from this record only"
      className="h-full"
      actions={
        <Button variant={result ? "secondary" : "primary"} size="sm" icon={Sparkles} loading={loading} onClick={summarise}>
          {loading ? "Summarising" : result ? "Summarise again" : "Summarise this stay"}
        </Button>
      }
    >
      {loading ? (
        <p role="status" aria-live="polite" className="py-6 text-center text-sm text-ink-500">
          Reading the visit, vitals, labs and medications, then writing. This takes a few seconds.
        </p>
      ) : error ? (
        <SummaryError error={error} onRetry={summarise} />
      ) : result ? (
        <SummaryResult result={result} />
      ) : (
        <Empty
          icon={FileText}
          title="No summary yet"
          msg="Press the button to summarise the most recent visit in four to six sentences, with the rows it came from."
          className="py-6"
        />
      )}
      {!loading && (
        <div className="mt-3">
          <EarlierOutputs patientId={patientId} feature="stay-summary" refreshKey={result} />
        </div>
      )}
    </Card>
  );
}

function SummaryError({ error, onRetry }) {
  if (error.status === 503) {
    return (
      <Empty
        icon={Info}
        title="No model configured"
        msg="The server has no language-model key (LLM_API_KEY). Summaries resume once one is set."
        className="py-6"
      />
    );
  }
  if (error.status === 429) {
    return (
      <Empty
        icon={Gauge}
        title="Daily limit reached"
        msg="Your organisation has used today's token allowance for summaries. It resets at midnight UTC."
        className="py-6"
      />
    );
  }
  if (error.status === 404) {
    return <Empty icon={FileText} title="Not applicable" msg="This patient has no visit to summarise." className="py-6" />;
  }
  return <Retry error={error} onRetry={onRetry} />;
}

function SummaryResult({ result }) {
  const [showSources, setShowSources] = useState(false);
  const Chevron = showSources ? ChevronDown : ChevronRight;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm leading-relaxed text-ink-800">{result.summary}</p>

      <div>
        <button
          type="button"
          onClick={() => setShowSources((v) => !v)}
          aria-expanded={showSources}
          className="inline-flex items-center gap-1 text-xs font-medium text-brand-700 hover:underline"
        >
          <Chevron aria-hidden="true" className="h-3.5 w-3.5" />
          Sources ({result.sources.length} rows)
        </button>
        {showSources && (
          <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-ink-600 sm:grid-cols-3">
            {result.sources.map((s) => (
              <li key={`${s.table}-${s.id}`} className="num">
                {s.table} #{s.id}
              </li>
            ))}
          </ul>
        )}
      </div>

      <p className="border-t border-ink-100 pt-3 text-[11px] text-ink-500">
        {result.model} · {formatNumber(result.input_tokens)} in / {formatNumber(result.output_tokens)} out tokens ·{" "}
        {formatNumber(result.elapsed_ms)} ms · check every statement against the record before acting on it
        {result.stored === false && " · not saved to the AI history (document store unavailable)"}
      </p>
    </div>
  );
}
