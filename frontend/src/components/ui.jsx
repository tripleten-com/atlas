import { forwardRef } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Loader2 } from "lucide-react";
import clsx from "clsx";
import { ENCOUNTER_TYPES, EVENT_TYPES, INGEST_STATUS, initials } from "../lib/format";

export function Card({ title, subtitle, actions, children, className, bodyClassName, padded = true, as: As = "section", ...rest }) {
  return (
    <As className={clsx("card", className)} {...rest}>
      {(title || actions) && (
        <header className="card-header">
          <div className="min-w-0">
            {title && <h2 className="card-title truncate">{title}</h2>}
            {subtitle && <p className="card-subtitle mt-0.5">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx(padded && "card-body", bodyClassName)}>{children}</div>
    </As>
  );
}

export const Button = forwardRef(function Button(
  { variant = "secondary", size, icon: Icon, loading = false, children, className, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={clsx("btn", `btn-${variant}`, size === "sm" && "btn-sm", className)}
      disabled={loading || rest.disabled}
      {...rest}
    >
      {loading ? (
        <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
      ) : (
        Icon && <Icon aria-hidden="true" className={size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4"} />
      )}
      {children}
    </button>
  );
});

const TONES = {
  neutral: "badge-neutral",
  brand: "badge-brand",
  high: "badge-high",
  low: "badge-low",
  warn: "badge-warn",
  ok: "badge-ok",
};

export function Badge({ tone = "neutral", children, className, dot = false, title }) {
  return (
    <span className={clsx("badge", TONES[tone] || TONES.neutral, className)} title={title}>
      {dot && <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />}
      {children}
    </span>
  );
}

export function EncounterBadge({ type }) {
  const meta = ENCOUNTER_TYPES[type] || { label: type, tone: "neutral" };
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

export function EventTypeBadge({ type }) {
  const meta = EVENT_TYPES[type] || { label: type, tone: "neutral" };
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

export function IngestStatusBadge({ status }) {
  const meta = INGEST_STATUS[status] || { label: status, tone: "neutral" };
  return (
    <Badge tone={meta.tone} dot>
      {meta.label}
    </Badge>
  );
}

// A lab flag. The text label carries the meaning; colour only reinforces it.
export function FlagBadge({ flag }) {
  if (flag === "H") return <Badge tone="high">High</Badge>;
  if (flag === "L") return <Badge tone="low">Low</Badge>;
  return <Badge tone="neutral">Normal</Badge>;
}

export function StatusDot({ tone = "ok", pulse = false, label }) {
  const color = {
    ok: "bg-success-600",
    warn: "bg-caution-600",
    high: "bg-critical-600",
    neutral: "bg-ink-400",
    brand: "bg-brand-500",
  }[tone];
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="relative flex h-2 w-2">
        {pulse && <span className={clsx("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", color)} />}
        <span className={clsx("relative inline-flex h-2 w-2 rounded-full", color)} />
      </span>
      {label && <span className="text-xs text-ink-600">{label}</span>}
    </span>
  );
}

const AVATAR_COLORS = [
  "bg-brand-100 text-brand-800",
  "bg-info-100 text-info-700",
  "bg-caution-100 text-caution-700",
  "bg-success-100 text-success-700",
  "bg-critical-100 text-critical-700",
  "bg-ink-100 text-ink-700",
];

export function Avatar({ name, size = "md", className }) {
  const hash = [...(name || "")].reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
  const sizes = { sm: "h-7 w-7 text-[11px]", md: "h-9 w-9 text-xs", lg: "h-12 w-12 text-base" };
  return (
    <span
      aria-hidden="true"
      className={clsx(
        "inline-flex shrink-0 items-center justify-center rounded-full font-semibold",
        AVATAR_COLORS[hash % AVATAR_COLORS.length],
        sizes[size],
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}

export function PageHeader({ title, description, actions, breadcrumbs, children }) {
  return (
    <div className="mb-6">
      {breadcrumbs && (
        <nav aria-label="Breadcrumb" className="mb-2 flex items-center gap-1 text-xs text-ink-500">
          {breadcrumbs.map((crumb, i) => (
            <span key={crumb.label} className="flex items-center gap-1">
              {i > 0 && <ChevronRight aria-hidden="true" className="h-3 w-3" />}
              {crumb.to ? (
                <Link to={crumb.to} className="hover:text-ink-800">
                  {crumb.label}
                </Link>
              ) : (
                <span className="text-ink-700">{crumb.label}</span>
              )}
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-ink-900">{title}</h1>
          {description && <p className="mt-1 max-w-3xl text-sm text-ink-500">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

export function StatCard({ label, value, hint, icon: Icon, tone = "brand", loading = false, to }) {
  const iconTone = {
    brand: "bg-brand-50 text-brand-700 ring-brand-100",
    high: "bg-critical-50 text-critical-700 ring-critical-100",
    warn: "bg-caution-50 text-caution-700 ring-caution-100",
    low: "bg-info-50 text-info-700 ring-info-100",
    ok: "bg-success-50 text-success-700 ring-success-100",
    neutral: "bg-ink-50 text-ink-600 ring-ink-100",
  }[tone];
  const body = (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="text-xs font-medium text-ink-500">{label}</p>
        {loading ? (
          <div className="mt-2 h-7 w-20 animate-pulse rounded bg-ink-100" />
        ) : (
          <p className="num mt-1 text-2xl font-semibold tracking-tight text-ink-900">{value}</p>
        )}
        {hint && <p className="mt-1 truncate text-xs text-ink-500">{hint}</p>}
      </div>
      {Icon && (
        <span className={clsx("rounded-lg p-2 ring-1", iconTone)}>
          <Icon aria-hidden="true" className="h-4 w-4" />
        </span>
      )}
    </div>
  );
  if (to) {
    return (
      <Link to={to} className="card block p-4 transition hover:border-brand-300 hover:shadow-pop">
        {body}
      </Link>
    );
  }
  return <div className="card p-4">{body}</div>;
}

export function KeyValue({ items, className }) {
  return (
    <dl className={clsx("grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2", className)}>
      {items.map(({ label, value }) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-ink-500">{label}</dt>
          <dd className="mt-0.5 truncate text-sm font-medium text-ink-900">{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}
