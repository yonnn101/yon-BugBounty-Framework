import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import * as api from "../api/client";
import type { GraphView } from "../api/types";

const POLL_MS = 2000;
const TERMINAL = new Set(["SUCCESS", "FAILURE", "REVOKED", "REJECTED"]);

type DiscoveryResult = {
  subdomain_asset_ids?: string[];
  program_id?: string;
};

function summarizeDiscoveryResult(result: unknown): string {
  if (!result || typeof result !== "object") return "";
  const r = result as DiscoveryResult;
  const n = r.subdomain_asset_ids?.length;
  if (typeof n === "number") return `${n} subdomain(s) saved; DNS batch queued.`;
  return "";
}

type Props = {
  programId: string;
  graph: GraphView | null;
  onGraphRefresh: () => void | Promise<void>;
};

export function SubdomainDiscoveryPanel({ programId, graph, onGraphRefresh }: Props) {
  const [discoveryRootId, setDiscoveryRootId] = useState("");
  const [discoveryDomain, setDiscoveryDomain] = useState("");
  const [discoveryBusy, setDiscoveryBusy] = useState(false);
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [pollTaskId, setPollTaskId] = useState<string | null>(null);

  const domainNodes = useMemo(
    () => graph?.nodes.filter((n) => n.type === "DOMAIN") ?? [],
    [graph],
  );

  useEffect(() => {
    if (!graph?.nodes.length) return;
    const domains = graph.nodes.filter((n) => n.type === "DOMAIN");
    if (domains.length === 0) {
      setDiscoveryRootId("");
      setDiscoveryDomain("");
      return;
    }
    setDiscoveryRootId((prev) => {
      if (prev && domains.some((d) => d.id === prev)) return prev;
      const first = domains[0];
      setDiscoveryDomain(first.value);
      return first.id;
    });
  }, [graph]);

  const refreshGraph = useCallback(async () => {
    await onGraphRefresh();
  }, [onGraphRefresh]);

  useEffect(() => {
    if (!pollTaskId) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const st = await api.getCeleryTaskStatus(pollTaskId);
        if (cancelled) return;

        if (st.state === "PENDING" || st.state === "RECEIVED") {
          setStatusLine(`Task ${pollTaskId.slice(0, 8)}… — waiting for worker (${st.state})`);
          return;
        }
        if (st.state === "STARTED" || st.state === "RETRY") {
          setStatusLine(`Task ${pollTaskId.slice(0, 8)}… — running (${st.state})`);
          return;
        }

        if (TERMINAL.has(st.state)) {
          if (st.state === "SUCCESS") {
            const extra = summarizeDiscoveryResult(st.result);
            setStatusLine(
              extra
                ? `Finished — ${extra} Graph updated.`
                : "Discovery task finished. Graph updated.",
            );
            await refreshGraph();
          } else {
            setStatusLine(`Task failed: ${st.error ?? st.state}`);
          }
          setPollTaskId(null);
          return;
        }

        setStatusLine(`Task ${pollTaskId.slice(0, 8)}… — ${st.state}`);
      } catch {
        if (!cancelled) {
          setStatusLine("Could not poll task status (check API / Redis).");
          setPollTaskId(null);
        }
      }
    };

    void tick();
    const id = setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [pollTaskId, refreshGraph]);

  function onDiscoveryRootChange(assetId: string) {
    setDiscoveryRootId(assetId);
    const n = graph?.nodes.find((x) => x.id === assetId);
    setDiscoveryDomain(n?.value ?? "");
  }

  async function onStartDiscovery(e: FormEvent) {
    e.preventDefault();
    if (!discoveryRootId) return;
    setDiscoveryBusy(true);
    setStatusLine(null);
    try {
      const res = await api.startSubdomainDiscovery(programId, {
        root_domain_asset_id: discoveryRootId,
        domain: discoveryDomain.trim() || undefined,
      });
      setStatusLine(`Queued — tracking task ${res.task_id.slice(0, 8)}…`);
      setPollTaskId(res.task_id);
    } catch (ex) {
      setStatusLine(ex instanceof Error ? ex.message : "Discovery failed");
    } finally {
      setDiscoveryBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-cyan-900/40 bg-surface-800/50 p-6">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        Subdomain discovery (Subfinder)
      </h2>
      <p className="mt-1 text-xs text-slate-500">
        Runs passive subdomain enumeration, saves SUBDOMAIN assets, then queues batched DNS (separate
        worker task). Requires a <span className="text-slate-400">DOMAIN</span> asset and Celery
        workers on <code className="text-slate-400">slow</code> + <code className="text-slate-400">fast</code>{" "}
        queues.
      </p>
      <form onSubmit={onStartDiscovery} className="mt-4 flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end">
        <div className="min-w-[200px] flex-1">
          <label className="text-xs text-slate-500">Root DOMAIN asset</label>
          <select
            required
            value={discoveryRootId}
            onChange={(e) => onDiscoveryRootChange(e.target.value)}
            disabled={domainNodes.length === 0}
            className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            {domainNodes.length === 0 ? (
              <option value="">— add a DOMAIN asset above —</option>
            ) : (
              domainNodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.value}
                </option>
              ))
            )}
          </select>
        </div>
        <div className="min-w-[200px] flex-1">
          <label className="text-xs text-slate-500">Domain for subfinder (-d)</label>
          <input
            value={discoveryDomain}
            onChange={(e) => setDiscoveryDomain(e.target.value)}
            placeholder="example.com"
            className="mt-1 w-full rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-white focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <p className="mt-1 text-[10px] text-slate-600">Leave empty to use the root DOMAIN value.</p>
        </div>
        <button
          type="submit"
          disabled={discoveryBusy || domainNodes.length === 0 || !discoveryRootId || pollTaskId !== null}
          className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"
        >
          {discoveryBusy ? "Queueing…" : pollTaskId ? "Running…" : "Run discovery"}
        </button>
      </form>
      {statusLine && <p className="mt-3 text-sm text-slate-400">{statusLine}</p>}
    </section>
  );
}
