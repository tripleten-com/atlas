import { History } from "lucide-react";
import { getAiHistory } from "../api/client";
import { Spinner } from "../components";
import { useApi } from "../hooks";
import { formatDateTime, formatPercent } from "../lib/format";

// The last few outputs an AI feature produced for this patient, read from the document store
// through GET /api/patients/{id}/ai/history. RiskCard and SummaryCard both show it under their
// result. Four states, each in words:
//   loading               a small spinner
//   history unavailable   the store could not be read (`stored: false`); say so rather than
//                         show an empty list as if nothing had ever been scored
//   empty                 nothing stored yet for this feature
//   list                  timestamp plus probability and band, or the start of a generated text
//
// `refreshKey` changes when the card gets a fresh result, so the list reloads and shows it.
export function EarlierOutputs({ patientId, feature, refreshKey, limit = 5 }) {
  const { data, error, loading } = useApi(
    () => getAiHistory(patientId, { feature, limit }),
    [patientId, feature, limit, refreshKey],
    { keepPrevious: true },
  );

  return (
    <div className="border-t border-ink-100 pt-3">
      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-ink-700">
        <History aria-hidden="true" className="h-3.5 w-3.5 text-ink-400" />
        Earlier outputs
      </p>
      <HistoryBody data={data} error={error} loading={loading} />
    </div>
  );
}

function HistoryBody({ data, error, loading }) {
  if (loading && !data) return <Spinner label="Loading earlier outputs" size={14} />;
  if (error || (data && !data.stored)) {
    return <p className="text-xs text-ink-500">History unavailable: the document store could not be reached.</p>;
  }
  if (!data || data.items.length === 0) {
    return <p className="text-xs text-ink-500">No earlier outputs stored for this patient.</p>;
  }
  return (
    <ul className="divide-y divide-ink-100 text-xs">
      {data.items.map((item) => (
        <li key={`${item.feature}-${item.created_at}`} className="flex items-baseline gap-3 py-1.5">
          <span className="num shrink-0 text-ink-500">{formatDateTime(item.created_at)}</span>
          <span className="min-w-0 flex-1 truncate text-ink-800">{describe(item)}</span>
        </li>
      ))}
    </ul>
  );
}

// One line per item: a score reads "18% medium", a text shows its first words.
function describe(item) {
  if (item.text_preview) return item.text_preview;
  if (item.probability === null || item.probability === undefined) return item.state || "no score";
  const band = item.band ? ` ${item.band}` : "";
  return `${formatPercent(item.probability, 0)}${band}`;
}
