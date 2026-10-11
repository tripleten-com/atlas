import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import clsx from "clsx";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { formatNumber } from "../lib/format";

export function SearchInput({ value, onChange, placeholder = "Search", className, autoFocus, label = "Search" }) {
  return (
    <label className={clsx("relative block", className)}>
      <span className="sr-only">{label}</span>
      <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoFocus={autoFocus}
        className="field pl-9 pr-8"
      />
      {value && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => onChange("")}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-ink-400 hover:text-ink-700"
        >
          <X aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
      )}
    </label>
  );
}

export function Select({ value, onChange, options, label, hideLabel = false, className }) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className={clsx("label", hideLabel && "sr-only")}>
        {label}
      </label>
      <div className="relative">
        <select
          id={id}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          className="field appearance-none pr-8"
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
      </div>
    </div>
  );
}

// Segmented tabs. Keyboard: arrow keys move between tabs.
export function Tabs({ tabs, value, onChange, className, size = "md" }) {
  const refs = useRef([]);
  const onKey = (e, index) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = (index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    refs.current[next]?.focus();
    onChange(tabs[next].id);
  };
  return (
    <div role="tablist" className={clsx("inline-flex rounded-lg bg-ink-100 p-1", className)}>
      {tabs.map((tab, i) => {
        const active = tab.id === value;
        return (
          <button
            key={tab.id}
            ref={(el) => (refs.current[i] = el)}
            role="tab"
            type="button"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(e) => onKey(e, i)}
            className={clsx(
              "inline-flex items-center gap-1.5 rounded-md font-medium transition",
              size === "sm" ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm",
              active ? "bg-white text-ink-900 shadow-sm" : "text-ink-600 hover:text-ink-900",
            )}
          >
            {tab.label}
            {tab.count !== undefined && tab.count !== null && (
              <span className={clsx("num rounded-full px-1.5 text-[11px]", active ? "bg-brand-50 text-brand-800" : "bg-ink-200/70 text-ink-600")}>
                {formatNumber(tab.count)}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage, className }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className={clsx("flex items-center justify-between gap-3 px-4 py-3 text-sm text-ink-600", className)}>
      <p className="num">
        {total === 0 ? "No results" : `${formatNumber(from)}–${formatNumber(to)} of ${formatNumber(total)}`}
      </p>
      <div className="flex items-center gap-1">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label="Previous page"
        >
          <ChevronLeft aria-hidden="true" className="h-4 w-4" />
        </button>
        <span className="num px-2 text-xs">
          Page {page} of {formatNumber(pages)}
        </span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          aria-label="Next page"
        >
          <ChevronRight aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

export function Sparkline({ data, dataKey = "value", color = "#197b71", height = 32, domain }) {
  if (!data || data.length < 2) return <div style={{ height }} />;
  return (
    <div style={{ height }} aria-hidden="true">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <YAxis hide domain={domain || ["dataMin - 2", "dataMax + 2"]} />
          <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.75} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// A small dropdown menu that closes on outside click and Escape.
export function Menu({ trigger, children, align = "right", className, panelClassName }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div ref={ref} className={clsx("relative", className)}>
      {trigger({ open, toggle: () => setOpen((v) => !v) })}
      {open && (
        <div
          className={clsx(
            "absolute z-40 mt-2 animate-fade-in rounded-xl border border-ink-200 bg-white p-1.5 shadow-pop",
            align === "right" ? "right-0" : "left-0",
            panelClassName,
          )}
        >
          {children({ close: () => setOpen(false) })}
        </div>
      )}
    </div>
  );
}
