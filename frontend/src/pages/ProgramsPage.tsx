import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";
import type { Program } from "../api/types";
import { parseScopeInput } from "../utils/scopeInput";

export function ProgramsPage() {
  const [programs, setPrograms] = useState<Program[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("H1");
  const [inScopeText, setInScopeText] = useState("");
  const [outScopeText, setOutScopeText] = useState("");

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

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setCreating(true);
    try {
      const in_scope = parseScopeInput(inScopeText);
      const out_scope = parseScopeInput(outScopeText);
      await api.createProgram({
        name,
        platform,
        in_scope,
        out_scope,
        settings: {},
      });
      setName("");
      setInScopeText("");
      setOutScopeText("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Create failed");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-10">
      <div>
        <h1 className="font-display text-2xl font-bold text-white">Programs</h1>
        <p className="mt-1 text-sm text-slate-500">
          Scope containers for your targets. Only you can see programs you create.
        </p>
      </div>

      <section className="rounded-xl border border-surface-600 bg-surface-800/50 p-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">New program</h2>
        <form onSubmit={onCreate} className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className="text-xs text-slate-500">Name</label>
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-white focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              placeholder="Acme Corp BB program"
            />
          </div>
          <div>
            <label className="text-xs text-slate-500">Platform</label>
            <input
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-white focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <div />
          <div>
            <label className="text-xs text-slate-500">in_scope</label>
            <p className="mt-0.5 text-[11px] leading-snug text-slate-600">
              JSON array/object, or one domain per line (also comma or semicolon separated).
            </p>
            <textarea
              value={inScopeText}
              onChange={(e) => setInScopeText(e.target.value)}
              rows={5}
              placeholder={`example.com\napi.example.com\n\nOr: ["example.com","api.example.com"]`}
              className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 font-mono text-sm text-slate-200 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <div>
            <label className="text-xs text-slate-500">out_scope</label>
            <p className="mt-0.5 text-[11px] leading-snug text-slate-600">
              Same as in_scope: JSON or a plain domain list.
            </p>
            <textarea
              value={outScopeText}
              onChange={(e) => setOutScopeText(e.target.value)}
              rows={5}
              placeholder={`staging.example.com\nOr leave empty`}
              className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 font-mono text-sm text-slate-200 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={creating}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-surface-900 hover:bg-cyan-300 disabled:opacity-50"
            >
              {creating ? "Creating…" : "Create program"}
            </button>
          </div>
        </form>
      </section>

      {err && <p className="text-sm text-red-400">{err}</p>}

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Your programs</h2>
        {loading ? (
          <p className="mt-4 text-slate-500">Loading…</p>
        ) : programs.length === 0 ? (
          <p className="mt-4 text-slate-500">No programs yet.</p>
        ) : (
          <ul className="mt-4 divide-y divide-surface-600 rounded-xl border border-surface-600 bg-surface-800/30">
            {programs.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 hover:bg-surface-800/80">
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
                <Link
                  to={`/programs/${p.id}`}
                  className="text-sm text-accent hover:underline"
                >
                  Open →
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
