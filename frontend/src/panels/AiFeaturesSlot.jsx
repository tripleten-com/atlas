import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";

// Where AI features for this patient will appear once they are built.
// eslint-disable-next-line no-unused-vars
export function AiFeaturesSlot({ patientId }) {
  return (
    <section
      aria-labelledby="ai-insights-title"
      className="flex flex-col gap-3 rounded-xl border border-dashed border-ink-300 bg-ink-50/50 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-center gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-ink-400 ring-1 ring-ink-200">
          <Sparkles aria-hidden="true" className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <h2 id="ai-insights-title" className="text-sm font-semibold text-ink-700">
            AI insights
          </h2>
          <p className="text-sm text-ink-500">No AI features are connected to this record yet.</p>
        </div>
      </div>
      <Link to="/ai" className="shrink-0 text-sm font-medium text-brand-700 hover:underline">
        About AI features
      </Link>
    </section>
  );
}
