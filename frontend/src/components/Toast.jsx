import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { CheckCircle2, AlertTriangle, X } from "lucide-react";
import clsx from "clsx";

const ToastContext = createContext(() => {});

// Short confirmations for actions: `const toast = useToast(); toast("Resync queued")`.
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), []);
  const toast = useCallback(
    (message, { tone = "ok", timeout = 4000 } = {}) => {
      const id = Math.random().toString(36).slice(2);
      setToasts((list) => [...list, { id, message, tone }]);
      setTimeout(() => dismiss(id), timeout);
    },
    [dismiss],
  );
  const value = useMemo(() => toast, [toast]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={clsx(
              "pointer-events-auto flex animate-fade-in items-start gap-2 rounded-lg border bg-white px-3 py-2.5 text-sm shadow-pop",
              t.tone === "error" ? "border-critical-100" : "border-ink-200",
            )}
          >
            {t.tone === "error" ? (
              <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-critical-600" />
            ) : (
              <CheckCircle2 aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-success-600" />
            )}
            <p className="flex-1 text-ink-800">{t.message}</p>
            <button type="button" aria-label="Dismiss" onClick={() => dismiss(t.id)} className="text-ink-400 hover:text-ink-700">
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
