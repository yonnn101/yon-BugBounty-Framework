import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { SubdomainDiscoveryPanel } from "../components/SubdomainDiscoveryPanel";
import * as api from "../api/client";
import type { GraphView, Program } from "../api/types";

const ASSET_TYPES = ["DOMAIN", "SUBDOMAIN", "IP", "URL", "PORT", "SERVICE"] as const;
const RELATION_TYPES = ["resolves_to", "hosts", "runs_on", "contains"] as const;

export function ProgramDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [program, setProgram] = useState<Program | null>(null);
  const [graph, setGraph] = useState<GraphView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [editName, setEditName] = useState("");
  const [editPlatform, setEditPlatform] = useState("");
  const [saving, setSaving] = useState(false);

  const [assetType, setAssetType] = useState<string>("DOMAIN");
  const [assetValue, setAssetValue] = useState("");
  const [parentId, setParentId] = useState("");
  const [relationType, setRelationType] = useState<string>("");
  const [ingesting, setIngesting] = useState(false);
  const [ingestMsg, setIngestMsg] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    if (!id) return;
    setErr(null);
    setLoading(true);
    try {
      const [p, g] = await Promise.all([api.getProgram(id), api.getGraph(id)]);
      setProgram(p);
      setEditName(p.name);
      setEditPlatform(p.platform);
      setGraph(g);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
      setProgram(null);
      setGraph(null);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const nodeLabel = useMemo(() => {
    const m = new Map<string, string>();
    graph?.nodes.forEach((n) => m.set(n.id, `${n.type}: ${n.value}`));
    return m;
  }, [graph]);

  async function onSaveProgram(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setSaving(true);
    setErr(null);
    try {
      const p = await api.updateProgram(id, { name: editName, platform: editPlatform });
      setProgram(p);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Update failed");
    } finally {
      setSaving(false);
    }
  }

  async function onDelete() {
    if (!id) return;
    if (!confirm("Delete this program and all its assets?")) return;
    try {
      await api.deleteProgram(id);
      navigate("/programs");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Delete failed");
    }
  }

  async function onIngest(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setIngestMsg(null);
    setIngesting(true);
    try {
      const hasParent = parentId.trim().length > 0;
      const hasRel = relationType.trim().length > 0;
      if (hasParent !== hasRel) {
        setIngestMsg("Provide both parent asset ID and relation type, or leave both empty.");
        return;
      }
      const res = await api.ingestAsset(id, {
        type: assetType,
        value: assetValue.trim(),
        metadata: {},
        parent_asset_id: hasParent ? parentId.trim() : null,
        relation_type: hasRel ? relationType.trim() : null,
      });
      setIngestMsg(`Created/updated asset ${res.asset_id}${res.relation_id ? `, relation ${res.relation_id}` : ""}`);
      setAssetValue("");
      await loadAll();
    } catch (ex) {
      setIngestMsg(ex instanceof Error ? ex.message : "Ingest failed");
    } finally {
      setIngesting(false);
    }
  }

  if (!id) return <p className="text-red-400">Missing program id</p>;

  if (loading && !program) {
    return <p className="text-slate-500">Loading program…</p>;
  }

  if (!program) {
    return (
      <div className="space-y-4">
        <p className="text-red-400">{err ?? "Program not found."}</p>
        <Link to="/programs" className="text-accent hover:underline">
          ← Back to programs
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-10">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/programs" className="text-sm text-slate-500 hover:text-accent">
            ← Programs
          </Link>
          <h1 className="mt-2 font-display text-2xl font-bold text-white">{program.name}</h1>
          <p className="text-sm text-slate-500">
            {program.platform} · owner {program.owner_id.slice(0, 8)}…
          </p>
        </div>
        <button
          type="button"
          onClick={() => void onDelete()}
          className="rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-300 hover:bg-red-950/70"
        >
          Delete program
        </button>
      </div>

      {err && <p className="text-sm text-red-400">{err}</p>}

      <section className="rounded-xl border border-surface-600 bg-surface-800/50 p-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Edit program</h2>
        <form onSubmit={onSaveProgram} className="mt-4 flex flex-wrap items-end gap-4">
          <div>
            <label className="text-xs text-slate-500">Name</label>
            <input
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              className="mt-1 block rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-white focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <div>
            <label className="text-xs text-slate-500">Platform</label>
            <input
              value={editPlatform}
              onChange={(e) => setEditPlatform(e.target.value)}
              className="mt-1 block rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-white focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-surface-600 px-4 py-2 text-sm font-medium text-white hover:bg-surface-500 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-surface-600 bg-surface-800/50 p-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Add or touch asset</h2>
        <p className="mt-1 text-xs text-slate-500">
          Same type+value updates <code className="text-slate-400">last_seen</code>. Optional parent creates a graph edge.
        </p>
        <form onSubmit={onIngest} className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <label className="text-xs text-slate-500">Type</label>
            <select
              value={assetType}
              onChange={(e) => setAssetType(e.target.value)}
              className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-white"
            >
              {ASSET_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className="text-xs text-slate-500">Value</label>
            <input
              required
              value={assetValue}
              onChange={(e) => setAssetValue(e.target.value)}
              placeholder="example.com"
              className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-white focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <div>
            <label className="text-xs text-slate-500">Parent asset ID (optional)</label>
            <select
              value={parentId}
              onChange={(e) => setParentId(e.target.value)}
              className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-white"
            >
              <option value="">— none —</option>
              {graph?.nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.type}: {n.value.slice(0, 40)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500">Relation type</label>
            <select
              value={relationType}
              onChange={(e) => setRelationType(e.target.value)}
              className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-white"
            >
              <option value="">— none —</option>
              {RELATION_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end">
            <button
              type="submit"
              disabled={ingesting}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-surface-900 hover:bg-cyan-300 disabled:opacity-50"
            >
              {ingesting ? "Saving…" : "Ingest asset"}
            </button>
          </div>
        </form>
        {ingestMsg && <p className="mt-3 text-sm text-slate-400">{ingestMsg}</p>}
      </section>

      {id && <SubdomainDiscoveryPanel programId={id} graph={graph} onGraphRefresh={loadAll} />}

      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Asset graph</h2>
          <button
            type="button"
            onClick={() => void loadAll()}
            className="text-sm text-accent hover:underline"
          >
            Refresh
          </button>
        </div>
        {!graph ? (
          <p className="text-slate-500">No graph data.</p>
        ) : (
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="overflow-hidden rounded-xl border border-surface-600">
              <h3 className="border-b border-surface-600 bg-surface-800 px-4 py-2 text-xs font-semibold uppercase text-slate-500">
                Nodes ({graph.nodes.length})
              </h3>
              <div className="max-h-80 overflow-auto">
                <table className="w-full text-left text-sm">
                  <thead className="sticky top-0 bg-surface-900 text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Type</th>
                      <th className="px-3 py-2">Value</th>
                      <th className="px-3 py-2 font-mono text-[10px]">id</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-700">
                    {graph.nodes.map((n) => (
                      <tr key={n.id} className="hover:bg-surface-800/80">
                        <td className="px-3 py-2 text-accent">{n.type}</td>
                        <td className="px-3 py-2 text-slate-200">{n.value}</td>
                        <td className="px-3 py-2 font-mono text-[10px] text-slate-500" title={n.id}>
                          {n.id.slice(0, 8)}…
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="overflow-hidden rounded-xl border border-surface-600">
              <h3 className="border-b border-surface-600 bg-surface-800 px-4 py-2 text-xs font-semibold uppercase text-slate-500">
                Edges ({graph.edges.length})
              </h3>
              <div className="max-h-80 overflow-auto">
                <table className="w-full text-left text-sm">
                  <thead className="sticky top-0 bg-surface-900 text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Parent → Child</th>
                      <th className="px-3 py-2">Relation</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-700">
                    {graph.edges.map((e) => (
                      <tr key={e.id} className="hover:bg-surface-800/80">
                        <td className="px-3 py-2 text-slate-300">
                          <span title={e.parent_id}>{nodeLabel.get(e.parent_id) ?? e.parent_id.slice(0, 8)}</span>
                          <span className="text-slate-600"> → </span>
                          <span title={e.child_id}>{nodeLabel.get(e.child_id) ?? e.child_id.slice(0, 8)}</span>
                        </td>
                        <td className="px-3 py-2 text-cyan-200/90">{e.relation_type}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
