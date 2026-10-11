import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import clsx from "clsx";
import {
  Activity,
  BarChart3,
  Building2,
  Check,
  ChevronsUpDown,
  ClipboardList,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Menu as MenuIcon,
  PlugZap,
  Sparkles,
  Stethoscope,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { getPatients } from "../api/client";
import { Avatar, Badge, Menu, StatusDot } from "../components";
import { useAuth } from "../context/AuthContext";
import { useFacility } from "../context/FacilityContext";
import { useLive } from "../context/LiveContext";
import { useDebounced } from "../hooks";
import { formatNumber, humanize } from "../lib/format";

// The navigation. To add a page: add a route in App.jsx and an entry here.
const NAV = [
  {
    heading: "Clinical",
    items: [
      { to: "/dashboard", label: "Overview", icon: LayoutDashboard },
      { to: "/patients", label: "Patients", icon: Users },
      { to: "/encounters", label: "Encounters", icon: Stethoscope },
      { to: "/results", label: "Results", icon: FlaskConical },
    ],
  },
  {
    heading: "Data",
    items: [
      { to: "/activity", label: "Live activity", icon: Activity },
      { to: "/integrations", label: "Integrations", icon: PlugZap },
    ],
  },
  {
    heading: "Intelligence",
    items: [{ to: "/ai", label: "AI features", icon: Sparkles }],
  },
  {
    heading: "Administration",
    items: [
      { to: "/usage", label: "Usage", icon: BarChart3, admin: true },
      { to: "/settings", label: "Organization", icon: Building2 },
    ],
  },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <img src="/atlas.svg" alt="" className="h-8 w-8" />
      <div className="leading-tight">
        <p className="text-[15px] font-semibold tracking-tight text-white">Atlas</p>
        <p className="text-[11px] text-brand-200/80">by Atlas Health</p>
      </div>
    </div>
  );
}

function Sidebar({ onNavigate }) {
  const { isAdmin, user } = useAuth();
  return (
    <div className="flex h-full flex-col bg-ink-950 bg-[radial-gradient(120%_60%_at_0%_0%,rgba(37,153,139,0.18),transparent_60%)]">
      <div className="px-5 pb-6 pt-5">
        <Logo />
      </div>
      <nav aria-label="Main" className="flex-1 space-y-6 overflow-y-auto px-3 pb-6">
        {NAV.map((section) => {
          const items = section.items.filter((item) => !item.admin || isAdmin);
          if (!items.length) return null;
          return (
            <div key={section.heading}>
              <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-500">{section.heading}</p>
              <ul className="space-y-0.5">
                {items.map(({ to, label, icon: Icon }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      onClick={onNavigate}
                      className={({ isActive }) =>
                        clsx(
                          "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
                          isActive ? "bg-white/10 text-white" : "text-ink-300 hover:bg-white/5 hover:text-white",
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          <Icon
                            aria-hidden="true"
                            className={clsx("h-4 w-4", isActive ? "text-brand-300" : "text-ink-500 group-hover:text-ink-300")}
                          />
                          {label}
                        </>
                      )}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </nav>
      <div className="border-t border-white/10 px-5 py-4">
        <p className="truncate text-xs font-medium text-ink-300">{user?.tenant.name}</p>
        <p className="mt-0.5 text-[11px] text-ink-500">Synthetic data only. No real patients.</p>
      </div>
    </div>
  );
}

function FacilitySwitcher() {
  const { facilities, facilityId, facility, setFacilityId } = useFacility();
  const options = [{ id: null, name: "All facilities", hint: `${facilities.length} facilities` }, ...facilities];
  return (
    <Menu
      panelClassName="w-80"
      trigger={({ open, toggle }) => (
        <button
          type="button"
          onClick={toggle}
          aria-haspopup="listbox"
          aria-expanded={open}
          className="flex max-w-[260px] items-center gap-2.5 rounded-lg border border-ink-200 bg-white py-1.5 pl-2 pr-2.5 text-left shadow-sm transition hover:border-ink-300"
        >
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-700 ring-1 ring-brand-100">
            <Building2 aria-hidden="true" className="h-4 w-4" />
          </span>
          <span className="min-w-0">
            <span className="block text-[10px] font-medium uppercase tracking-wide text-ink-400">Facility</span>
            <span className="block truncate text-sm font-medium text-ink-900">{facility ? facility.name : "All facilities"}</span>
          </span>
          <ChevronsUpDown aria-hidden="true" className="ml-1 h-4 w-4 shrink-0 text-ink-400" />
        </button>
      )}
    >
      {({ close }) => (
        <ul role="listbox" aria-label="Facilities">
          {options.map((f) => {
            const selected = f.id === facilityId;
            return (
              <li key={f.id ?? "all"}>
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    setFacilityId(f.id);
                    close();
                  }}
                  className={clsx(
                    "flex w-full items-start gap-3 rounded-lg px-2.5 py-2 text-left transition",
                    selected ? "bg-brand-50" : "hover:bg-ink-50",
                  )}
                >
                  <span className="mt-0.5 w-4">{selected && <Check aria-hidden="true" className="h-4 w-4 text-brand-700" />}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-ink-900">{f.name}</span>
                      {f.kind === "demo" && <Badge tone="warn">Test</Badge>}
                    </span>
                    <span className="mt-0.5 flex items-center gap-2 text-xs text-ink-500">
                      {f.id === null ? (
                        f.hint
                      ) : (
                        <>
                          <span>{humanize(f.kind === "demo" ? "seeded history" : f.kind)}</span>
                          <span aria-hidden="true">·</span>
                          <span className="num">{formatNumber(f.patient_count)} patients</span>
                          {f.data_source === "meridian" && (
                            <StatusDot tone={f.feed_status === "active" ? "ok" : "warn"} label={f.feed_status === "active" ? "Live" : humanize(f.feed_status)} />
                          )}
                        </>
                      )}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Menu>
  );
}

function LiveIndicator() {
  const { status } = useLive();
  const { isStatic } = useFacility();
  if (isStatic) {
    return (
      <span className="hidden items-center gap-1.5 rounded-full border border-caution-100 bg-caution-50 px-2.5 py-1 text-xs font-medium text-caution-700 lg:inline-flex">
        Static test data
      </span>
    );
  }
  const map = {
    open: { tone: "ok", label: "Live", pulse: true },
    connecting: { tone: "neutral", label: "Connecting" },
    retrying: { tone: "warn", label: "Reconnecting" },
    idle: { tone: "neutral", label: "Offline" },
  }[status];
  return (
    <span
      className="hidden items-center rounded-full border border-ink-200 bg-white px-2.5 py-1 lg:inline-flex"
      title="Updates from the Meridian data feed appear as they arrive"
    >
      <StatusDot tone={map.tone} pulse={map.pulse} label={map.label} />
    </span>
  );
}

function PatientQuickSearch() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const debounced = useDebounced(q, 250);
  const { facilityId } = useFacility();
  const navigate = useNavigate();

  useEffect(() => {
    if (debounced.trim().length < 2) {
      setResults([]);
      return undefined;
    }
    const controller = new AbortController();
    getPatients({ q: debounced, facility_id: facilityId, page_size: 6, sort: "name" }, { signal: controller.signal })
      .then((page) => setResults(page.items))
      .catch(() => {});
    return () => controller.abort();
  }, [debounced, facilityId]);

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.getElementById("patient-quick-search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (id) => {
    setQ("");
    setOpen(false);
    navigate(`/patients/${id}`);
  };

  return (
    <div className="relative w-full max-w-md">
      <label htmlFor="patient-quick-search" className="sr-only">
        Find a patient
      </label>
      <UserRound aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
      <input
        id="patient-quick-search"
        type="search"
        autoComplete="off"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && results[0]) go(results[0].id);
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder="Find a patient by name or MRN"
        className="field bg-ink-50/60 pl-9 pr-14"
      />
      <span className="kbd pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 sm:inline">Ctrl K</span>
      {open && q.trim().length >= 2 && (
        <div className="absolute left-0 right-0 z-40 mt-2 animate-fade-in rounded-xl border border-ink-200 bg-white p-1.5 shadow-pop">
          {results.length === 0 ? (
            <p className="px-3 py-2 text-sm text-ink-500">No matching patients</p>
          ) : (
            results.map((p) => (
              <button
                key={p.id}
                type="button"
                onMouseDown={() => go(p.id)}
                className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left hover:bg-ink-50"
              >
                <Avatar name={p.name} size="sm" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink-900">{p.name}</span>
                  <span className="block truncate text-xs text-ink-500">
                    {p.mrn} · {p.age} y · {p.facility_name}
                  </span>
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <Menu
      panelClassName="w-64"
      trigger={({ open, toggle }) => (
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-label="Account menu"
          className="flex items-center gap-2 rounded-full p-0.5 transition hover:ring-2 hover:ring-ink-200"
        >
          <Avatar name={user?.name} size="sm" />
        </button>
      )}
    >
      {({ close }) => (
        <div>
          <div className="px-2.5 py-2">
            <p className="truncate text-sm font-semibold text-ink-900">{user?.name}</p>
            <p className="truncate text-xs text-ink-500">{user?.email}</p>
            <div className="mt-2 flex items-center gap-1.5">
              <Badge tone={user?.role === "admin" ? "brand" : "neutral"}>{humanize(user?.role)}</Badge>
              {user?.title && <span className="truncate text-xs text-ink-500">{user.title}</span>}
            </div>
          </div>
          <div className="my-1 border-t border-ink-100" />
          <button
            type="button"
            onClick={() => {
              close();
              navigate("/settings");
            }}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-ink-700 hover:bg-ink-50"
          >
            <ClipboardList aria-hidden="true" className="h-4 w-4 text-ink-400" /> Organization settings
          </button>
          <button
            type="button"
            onClick={() => {
              close();
              logout();
            }}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-ink-700 hover:bg-ink-50"
          >
            <LogOut aria-hidden="true" className="h-4 w-4 text-ink-400" /> Sign out
          </button>
        </div>
      )}
    </Menu>
  );
}

export default function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMobileOpen(false), [location.pathname]);

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 lg:block">
        <Sidebar />
      </aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-ink-950/50" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 animate-fade-in">
            <Sidebar onNavigate={() => setMobileOpen(false)} />
            <button type="button" aria-label="Close menu" onClick={() => setMobileOpen(false)} className="absolute right-3 top-5 text-ink-300">
              <X aria-hidden="true" className="h-5 w-5" />
            </button>
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-ink-200/80 bg-white/90 px-4 backdrop-blur sm:px-6">
          <button type="button" aria-label="Open menu" className="btn btn-ghost p-2 lg:hidden" onClick={() => setMobileOpen(true)}>
            <MenuIcon aria-hidden="true" className="h-5 w-5" />
          </button>
          <PatientQuickSearch />
          <div className="ml-auto flex items-center gap-3">
            <LiveIndicator />
            <FacilitySwitcher />
            <UserMenu />
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
