import { Component } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Inbox, Loader2, RotateCw } from "lucide-react";
import clsx from "clsx";

// Loading, empty and error states. Every panel uses these, so they look and sit the same way
// everywhere: a skeleton while loading, Retry when a request failed, Empty when there is nothing.

export function Spinner({ label = "Loading", size = 18, className }) {
  return (
    <span role="status" aria-live="polite" className={clsx("inline-flex items-center gap-2 text-sm text-ink-500", className)}>
      <Loader2 aria-hidden="true" className="animate-spin text-brand-600" style={{ width: size, height: size }} />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function Skeleton({ className, lines = 1 }) {
  if (lines > 1) {
    return (
      <div className="space-y-2" aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <Skeleton key={i} className={clsx("h-3.5", i === lines - 1 ? "w-2/3" : "w-full", className)} />
        ))}
      </div>
    );
  }
  return (
    <div aria-hidden="true" className={clsx("relative overflow-hidden rounded-md bg-ink-100", className || "h-4 w-full")}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/60 to-transparent" />
    </div>
  );
}

export function PanelSkeleton({ rows = 4 }) {
  return (
    <div role="status" aria-busy="true" className="space-y-3 p-5">
      <span className="sr-only">Loading</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  );
}

export function Retry({ error, onRetry, compact = false }) {
  const message = error?.status === 404 ? "Not found." : error?.message || "Something went wrong.";
  return (
    <div
      role="alert"
      className={clsx("flex items-center gap-3 text-sm", compact ? "py-2" : "flex-col justify-center px-6 py-10 text-center")}
    >
      <AlertTriangle aria-hidden="true" className={clsx("text-critical-600", compact ? "h-4 w-4" : "h-6 w-6")} />
      <p className="text-ink-700">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn btn-secondary btn-sm">
          <RotateCw aria-hidden="true" className="h-3.5 w-3.5" /> Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ icon: Icon = Inbox, title, msg, link, action, className }) {
  return (
    <div className={clsx("flex flex-col items-center justify-center px-6 py-10 text-center", className)}>
      <div className="mb-3 rounded-full bg-ink-50 p-3 ring-1 ring-ink-100">
        <Icon aria-hidden="true" className="h-5 w-5 text-ink-400" />
      </div>
      {title && <p className="text-sm font-medium text-ink-800">{title}</p>}
      {msg && <p className="mt-1 max-w-sm text-sm text-ink-500">{msg}</p>}
      {link && (
        <Link to={link.to} className="mt-3 text-sm font-medium text-brand-700 hover:underline">
          {link.label}
        </Link>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// Wrap a panel so one broken card does not blank the whole page.
export class PanelBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Panel crashed:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card">
          <Retry
            error={{ message: `${this.props.name || "This panel"} failed to render: ${this.state.error.message}` }}
            onRetry={() => this.setState({ error: null })}
          />
        </div>
      );
    }
    return this.props.children;
  }
}
