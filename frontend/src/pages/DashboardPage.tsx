import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";
import type { Program } from "../api/types";
import { useAuth } from "../context/AuthContext";

export function DashboardPage() {
  const { user } = useAuth();
  const [programs, setPrograms] = useState<Program[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      setPrograms(await api.listPrograms());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load programs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="font-display text-2xl font-bold text-white">Dashboard</h1>
        <p className="mt-1 text-slate-500">
          Signed in as <span className="text-slate-300">{user?.email}</span>
          {user?.is_superuser && (
            <span className="ml-2 rounded bg-accent/15 px-2 py-0.5 text-xs text-accent">superuser</span>
          )}
        </p>
      </div>

      <section className="rounded-xl border border-surface-600 bg-surface-800/50 p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Your programs</h2>
          <Link
            to="/programs"
            className="text-sm font-medium text-accent hover:underline"
          >
            Manage &amp; create →
          </Link>
        </div>
        {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
        {loading ? (
          <p className="mt-4 text-slate-500">Loading programs…</p>
        ) : programs.length === 0 ? (
          <p className="mt-4 text-slate-500">
            No programs yet.{" "}
            <Link to="/programs" className="text-accent hover:underline">
              Create one on the Programs page
            </Link>
            .
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-surface-600 rounded-lg border border-surface-600 bg-surface-900/40">
            {programs.map((p) => (
              <li
                key={p.id}
                className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 hover:bg-surface-800/80"
              >
                <div>
                  <Link to={`/programs/${p.id}`} className="font-medium text-white hover:text-accent">
                    {p.name}
                  </Link>
                  <p className="text-xs text-slate-500">
                    {p.platform} · {new Date(p.created_at).toLocaleDateString()}
                    {p.summary != null && (
                      <span className="ml-2 text-slate-600">
                        · {p.summary.total_assets} asset{p.summary.total_assets === 1 ? "" : "s"}
                      </span>
                    )}
                  </p>
                </div>
                <Link to={`/programs/${p.id}`} className="text-sm text-accent hover:underline">
                  Open graph →
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid gap-4 sm:grid-cols-2">
        <Link
          to="/programs"
          className="group rounded-xl border border-surface-600 bg-surface-800/50 p-6 transition hover:border-accent/40 hover:bg-surface-800"
        >
          <h2 className="font-display text-lg font-semibold text-white group-hover:text-accent">Programs</h2>
          <p className="mt-2 text-sm text-slate-500">
            Create programs, edit scope (JSON or domain lists), assets, and the attack-surface graph.
          </p>
          <span className="mt-4 inline-block text-sm text-accent">Full programs UI →</span>
        </Link>
        <div className="rounded-xl border border-surface-600 border-dashed bg-surface-900/40 p-6">
          <h2 className="font-display text-lg font-semibold text-slate-500">Workers & tools</h2>
          <p className="mt-2 text-sm text-slate-600">Celery tasks and AsyncBaseTool integrations — coming next.</p>
        </div>
      </div>
    </div>
  );
}
