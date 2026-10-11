import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Loader2, ShieldCheck } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useDocumentTitle } from "../hooks";

const DEMO_ACCOUNTS = [
  { email: "clinician@riverside.example", who: "Dr. Maya Chen", org: "Riverside Health", role: "Clinician" },
  { email: "admin@riverside.example", who: "Jordan Blake", org: "Riverside Health", role: "Admin" },
  { email: "clinician@harborview.example", who: "Dr. Samuel Okoro", org: "Harborview Medical Group", role: "Clinician" },
  { email: "admin@harborview.example", who: "Priya Nair", org: "Harborview Medical Group", role: "Admin" },
];

export default function Login() {
  useDocumentTitle("Sign in");
  const { login, status, notice } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState(DEMO_ACCOUNTS[0].email);
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (status === "signed-in") return <Navigate to={location.state?.from?.pathname || "/dashboard"} replace />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(location.state?.from?.pathname || "/dashboard", { replace: true });
    } catch (err) {
      setError(err.status === 401 ? "That email and password don't match an account." : err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden overflow-hidden bg-ink-950 p-12 text-white lg:flex lg:flex-col">
        <div className="absolute inset-0 bg-[radial-gradient(80%_60%_at_10%_10%,rgba(37,153,139,0.35),transparent_60%),radial-gradient(60%_50%_at_90%_90%,rgba(122,213,196,0.15),transparent_60%)]" />
        <div className="relative flex items-center gap-3">
          <img src="/atlas.svg" alt="" className="h-10 w-10" />
          <div>
            <p className="text-lg font-semibold tracking-tight">Atlas</p>
            <p className="text-xs text-brand-200/80">by Atlas Health</p>
          </div>
        </div>
        <div className="relative mt-auto max-w-lg">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">Every patient, every facility, one record.</h1>
          <p className="mt-4 text-base text-ink-300">
            Admissions, vitals, labs and medications stream in from your hospitals as they happen. Atlas keeps them in one
            searchable record, separated by organisation.
          </p>
          <ul className="mt-8 space-y-3 text-sm text-ink-300">
            {[
              "Live clinical data from the Meridian exchange",
              "Caseload dashboards per facility",
              "Tenant isolation on every query",
            ].map((line) => (
              <li key={line} className="flex items-center gap-2.5">
                <ShieldCheck aria-hidden="true" className="h-4 w-4 text-brand-300" /> {line}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative mt-12 text-xs text-ink-500">All records in this environment are synthetic. No real patient data.</p>
      </section>

      <section className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <img src="/atlas.svg" alt="" className="h-9 w-9" />
            <p className="text-lg font-semibold">Atlas</p>
          </div>
          <h2 className="text-2xl font-semibold tracking-tight">Sign in</h2>
          <p className="mt-1 text-sm text-ink-500">Use one of the demo accounts below. The password is in the README.</p>

          {notice && !error && (
            <p role="status" className="mt-6 rounded-lg border border-caution-100 bg-caution-50 px-3 py-2 text-sm text-caution-700">
              {notice}
            </p>
          )}

          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label htmlFor="email" className="label">
                Email
              </label>
              <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} className="field" />
            </div>
            <div>
              <label htmlFor="password" className="label">
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="field"
                placeholder="atlas-demo"
              />
            </div>
            {error && (
              <p role="alert" className="flex items-start gap-2 rounded-lg border border-critical-100 bg-critical-50 px-3 py-2 text-sm text-critical-700">
                <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" /> {error}
              </p>
            )}
            <button type="submit" disabled={busy} className="btn btn-primary w-full py-2.5">
              {busy ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <ArrowRight aria-hidden="true" className="h-4 w-4" />}
              Sign in
            </button>
          </form>

          <div className="mt-8">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-400">Demo accounts</p>
            <ul className="divide-y divide-ink-100 rounded-xl border border-ink-200 bg-white">
              {DEMO_ACCOUNTS.map((acct) => (
                <li key={acct.email}>
                  <button
                    type="button"
                    onClick={() => setEmail(acct.email)}
                    className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left text-sm hover:bg-ink-50"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-ink-900">{acct.who}</span>
                      <span className="block truncate text-xs text-ink-500">{acct.org}</span>
                    </span>
                    <span className="badge badge-neutral">{acct.role}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>
    </div>
  );
}
