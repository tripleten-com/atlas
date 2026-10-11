import { useMemo, useState } from "react";
import { Building2, Lock, ScrollText, Users } from "lucide-react";
import { getAudit, getOrg } from "../api/client";
import { Avatar, Badge, Card, Empty, KeyValue, PageHeader, PanelSkeleton, Retry, StatusDot, Tabs } from "../components";
import { useAuth } from "../context/AuthContext";
import { useApi, useDocumentTitle, useNow } from "../hooks";
import { formatDateTime, formatNumber, formatRelative, humanize } from "../lib/format";

const AUDIT_TABS = [
  { id: "all", label: "All", match: () => true },
  { id: "signin", label: "Sign-ins", match: (a) => a === "login" || a.startsWith("auth.") },
  { id: "views", label: "Record views", match: (a) => a.endsWith(".view") },
  { id: "integrations", label: "Integration changes", match: (a) => a.startsWith("integration.") },
];

const ACTION_LABELS = {
  login: "Signed in",
  "patient.view": "Viewed patient record",
  "integration.resync": "Requested resync",
  "integration.pause": "Paused feed",
  "integration.resume": "Resumed feed",
  "integration.retry_failed": "Retried failed events",
};

const FEED_STATUS = {
  active: { tone: "ok", label: "Receiving" },
  paused: { tone: "neutral", label: "Paused" },
  error: { tone: "high", label: "Error" },
};

function RoleBadge({ role }) {
  return <Badge tone={role === "admin" ? "brand" : "neutral"}>{role === "admin" ? "Admin" : "Clinician"}</Badge>;
}

// "patient:1583" -> "Patient 1583"
function formatTarget(target) {
  if (!target) return "None";
  const [kind, id] = target.split(":");
  return id ? `${humanize(kind)} ${id}` : target;
}

function ProfileCard({ user, now }) {
  return (
    <Card title="Your profile">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
        <div className="flex items-center gap-3 sm:w-72">
          <Avatar name={user.name} size="lg" />
          <div className="min-w-0">
            <p className="truncate font-semibold text-ink-900">{user.name}</p>
            <p className="truncate text-sm text-ink-500">{user.email}</p>
          </div>
        </div>
        <KeyValue
          className="flex-1"
          items={[
            { label: "Role", value: <RoleBadge role={user.role} /> },
            { label: "Title", value: user.title || "Not set" },
            {
              label: "Last sign-in",
              value: user.last_login_at ? (
                <span title={formatDateTime(user.last_login_at)}>{formatRelative(user.last_login_at, now)}</span>
              ) : (
                "Never"
              ),
            },
            { label: "Organisation", value: user.tenant?.name },
          ]}
        />
      </div>
    </Card>
  );
}

function FacilitiesTable({ facilities }) {
  return (
    <Card title="Facilities" subtitle={`${facilities.length} in your organisation`} padded={false}>
      {facilities.length === 0 ? (
        <Empty icon={Building2} title="No facilities yet" />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Code</th>
                <th scope="col">Kind</th>
                <th scope="col">Data source</th>
                <th scope="col">Timezone</th>
                <th scope="col" className="text-right">
                  Patients
                </th>
                <th scope="col" className="text-right">
                  Active encounters
                </th>
                <th scope="col">Feed status</th>
              </tr>
            </thead>
            <tbody>
              {facilities.map((f) => {
                const feed = FEED_STATUS[f.feed_status];
                return (
                  <tr key={f.id}>
                    <td className="whitespace-nowrap font-medium text-ink-900">{f.name}</td>
                    <td className="font-mono text-xs text-ink-600">{f.code}</td>
                    <td className="text-ink-600">{f.kind === "demo" ? "Test" : humanize(f.kind)}</td>
                    <td>
                      {f.data_source === "meridian" ? <Badge tone="brand">Meridian feed</Badge> : <Badge>Seeded history</Badge>}
                    </td>
                    <td className="whitespace-nowrap text-ink-600">{f.timezone}</td>
                    <td className="num text-right">{formatNumber(f.patient_count)}</td>
                    <td className="num text-right">{formatNumber(f.active_encounters)}</td>
                    <td className="whitespace-nowrap">
                      {feed ? (
                        <StatusDot tone={feed.tone} label={feed.label} />
                      ) : f.feed_status ? (
                        humanize(f.feed_status)
                      ) : (
                        <span className="text-xs text-ink-500">No feed</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function PeopleTable({ users, now }) {
  return (
    <Card title="People" subtitle={`${users.length} with access`} padded={false}>
      {users.length === 0 ? (
        <Empty icon={Users} title="No people yet" />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Title</th>
                <th scope="col">Last sign-in</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="flex items-center gap-3">
                      <Avatar name={u.name} size="sm" />
                      <span className="whitespace-nowrap font-medium text-ink-900">{u.name}</span>
                    </div>
                  </td>
                  <td className="text-ink-600">{u.email}</td>
                  <td>
                    <RoleBadge role={u.role} />
                  </td>
                  <td className="text-ink-600">{u.title || "Not set"}</td>
                  <td className="whitespace-nowrap text-ink-600" title={u.last_login_at ? formatDateTime(u.last_login_at) : undefined}>
                    {u.last_login_at ? formatRelative(u.last_login_at, now) : "Never"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function AuditLog({ now }) {
  const [tab, setTab] = useState("all");
  const { data, error, loading, reload } = useApi(() => getAudit({ limit: 100 }), []);
  const tabs = useMemo(
    () => AUDIT_TABS.map((t) => ({ id: t.id, label: t.label, count: data ? data.filter((r) => t.match(r.action)).length : undefined })),
    [data],
  );
  const rows = useMemo(() => {
    const match = AUDIT_TABS.find((t) => t.id === tab).match;
    return (data || []).filter((r) => match(r.action));
  }, [data, tab]);

  return (
    <div className="card">
      <header className="card-header flex-wrap">
        <div>
          <h2 className="card-title">Audit log</h2>
          <p className="card-subtitle mt-0.5">The 100 most recent actions in your organisation</p>
        </div>
        <div className="overflow-x-auto">
          <Tabs tabs={tabs} value={tab} onChange={setTab} size="sm" />
        </div>
      </header>
      {error ? (
        <Retry error={error} onRetry={reload} />
      ) : loading && !data ? (
        <PanelSkeleton rows={6} />
      ) : rows.length === 0 ? (
        <Empty icon={ScrollText} title="Nothing recorded" msg={data.length ? "No actions of this kind in the recent log." : undefined} />
      ) : (
        <div className="table-wrap max-h-[32rem] overflow-y-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Person</th>
                <th scope="col">Action</th>
                <th scope="col">Target</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="whitespace-nowrap text-ink-600" title={formatDateTime(r.ts)}>
                    {formatDateTime(r.ts)}
                    <p className="text-xs text-ink-400">{formatRelative(r.ts, now)}</p>
                  </td>
                  <td className="whitespace-nowrap">{r.user_name || <span className="text-ink-500">System</span>}</td>
                  <td>
                    <span className="text-ink-900">{ACTION_LABELS[r.action] || humanize(r.action)}</span>
                    {r.detail?.events !== undefined && (
                      <span className="ml-1 text-xs text-ink-500">({formatNumber(r.detail.events)} events)</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap text-ink-600">{formatTarget(r.target)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function Settings() {
  useDocumentTitle("Organization");
  const { user, isAdmin } = useAuth();
  const now = useNow(30_000);
  const { data, error, loading, reload } = useApi(() => getOrg(), []);
  const tenant = data?.tenant || user?.tenant;
  // Prefer the fresh row from the org list; the token copy may be older.
  const me = data?.users.find((u) => u.id === user?.id) || user;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Organization"
        description={
          tenant ? (
            <>
              {tenant.name} <span className="text-ink-300">·</span> <span className="font-mono text-xs">{tenant.slug}</span>
            </>
          ) : (
            "Your organisation, its facilities and the people with access."
          )
        }
      />

      {me && <ProfileCard user={me} now={now} />}

      {error ? (
        <div className="card">
          <Retry error={error} onRetry={reload} />
        </div>
      ) : loading && !data ? (
        <>
          <div className="card">
            <PanelSkeleton rows={4} />
          </div>
          <div className="card">
            <PanelSkeleton rows={3} />
          </div>
        </>
      ) : (
        <>
          <FacilitiesTable facilities={data.facilities} />
          <PeopleTable users={data.users} now={now} />
        </>
      )}

      {isAdmin ? (
        <AuditLog now={now} />
      ) : (
        <div className="card flex items-start gap-3 px-5 py-4">
          <Lock aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" />
          <div>
            <p className="text-sm font-medium text-ink-900">Audit log</p>
            <p className="mt-0.5 text-sm text-ink-500">Only admins can view the audit log.</p>
          </div>
        </div>
      )}
    </div>
  );
}
