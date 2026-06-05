import { flattenHierarchicalGraph } from "../utils/graphTree";
import type {
  CeleryTaskStatus,
  GraphView,
  HierarchicalGraphView,
  IngestAssetResponse,
  Program,
  SubdomainDiscoveryResponse,
  TokenResponse,
  User,
} from "./types";

/**
 * API base path. Default `/api` matches FastAPI (see main.py) and nginx/Vite proxies.
 * Set `VITE_API_BASE_URL` to a full origin (e.g. https://api.example.com) for split hosting.
 */
const base = () => {
  const raw = import.meta.env.VITE_API_BASE_URL;
  if (raw !== undefined && raw !== null && String(raw).trim() !== "") {
    return String(raw).replace(/\/$/, "");
  }
  return "/api";
};

/** Send cookies (httpOnly JWT) on same-origin or credentialed cross-origin requests. */
const cred: RequestInit = { credentials: "include" };

async function parseError(res: Response): Promise<string> {
  try {
    const j = (await res.json()) as { detail?: unknown };
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail))
      return j.detail.map((e: { msg?: string }) => e.msg ?? JSON.stringify(e)).join("; ");
    return res.statusText || `HTTP ${res.status}`;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const body = new URLSearchParams();
  body.set("username", email);
  body.set("password", password);
  const res = await fetch(`${base()}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
    ...cred,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<TokenResponse>;
}

export async function logout(): Promise<void> {
  await fetch(`${base()}/auth/logout`, { method: "POST", ...cred });
}

export async function register(payload: {
  email: string;
  password: string;
  full_name?: string;
}): Promise<User> {
  const res = await fetch(`${base()}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    ...cred,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<User>;
}

export async function fetchMe(): Promise<User> {
  const res = await fetch(`${base()}/auth/me`, { ...cred });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<User>;
}

export async function adminPing(): Promise<{ status: string }> {
  const res = await fetch(`${base()}/admin/ping`, { ...cred });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<{ status: string }>;
}

export async function listPrograms(): Promise<Program[]> {
  const res = await fetch(`${base()}/programs`, { ...cred });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<Program[]>;
}

export async function createProgram(body: {
  name: string;
  platform?: string;
  reward_type?: string | null;
  in_scope?: unknown;
  out_scope?: unknown;
  settings?: Record<string, unknown>;
}): Promise<Program> {
  const res = await fetch(`${base()}/programs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...cred,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<Program>;
}

export async function getProgram(id: string): Promise<Program> {
  const res = await fetch(`${base()}/programs/${id}`, { ...cred });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<Program>;
}

export async function updateProgram(
  id: string,
  patch: Partial<{
    name: string;
    platform: string;
    reward_type: string | null;
    in_scope: unknown;
    out_scope: unknown;
    settings: Record<string, unknown>;
  }>,
): Promise<Program> {
  const res = await fetch(`${base()}/programs/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
    ...cred,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<Program>;
}

export async function deleteProgram(id: string): Promise<void> {
  const res = await fetch(`${base()}/programs/${id}`, {
    method: "DELETE",
    ...cred,
  });
  if (!res.ok) throw new Error(await parseError(res));
}

export async function getGraph(programId: string): Promise<GraphView> {
  const res = await fetch(`${base()}/programs/${programId}/graph`, { ...cred });
  if (!res.ok) throw new Error(await parseError(res));
  const raw = (await res.json()) as HierarchicalGraphView;
  const { nodes, edges } = flattenHierarchicalGraph(raw);
  return { program_id: raw.program_id, nodes, edges };
}

export async function ingestAsset(
  programId: string,
  body: {
    type: string;
    value: string;
    metadata?: Record<string, unknown>;
    parent_asset_id?: string | null;
    relation_type?: string | null;
  },
): Promise<IngestAssetResponse> {
  const res = await fetch(`${base()}/programs/${programId}/assets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: body.type,
      value: body.value,
      metadata: body.metadata ?? {},
      parent_asset_id: body.parent_asset_id ?? null,
      relation_type: body.relation_type ?? null,
    }),
    ...cred,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<IngestAssetResponse>;
}

export async function startSubdomainDiscovery(
  programId: string,
  body: { root_domain_asset_id: string; domain?: string | null },
): Promise<SubdomainDiscoveryResponse> {
  const res = await fetch(`${base()}/programs/${programId}/tasks/subdomain-discovery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      root_domain_asset_id: body.root_domain_asset_id,
      ...(body.domain != null && body.domain.trim() !== ""
        ? { domain: body.domain.trim() }
        : {}),
    }),
    ...cred,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<SubdomainDiscoveryResponse>;
}

export async function getCeleryTaskStatus(taskId: string): Promise<CeleryTaskStatus> {
  const res = await fetch(`${base()}/tasks/${encodeURIComponent(taskId)}`, { ...cred });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<CeleryTaskStatus>;
}
