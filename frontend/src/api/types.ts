export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
  updated_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface ProgramSummaryStats {
  total_assets: number;
  assets_by_type: Record<string, number>;
}

export interface Program {
  id: string;
  owner_id: string;
  name: string;
  platform: string;
  reward_type: string | null;
  in_scope: unknown;
  out_scope: unknown;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  /** Present on list/detail responses when the API includes inventory summary. */
  summary?: ProgramSummaryStats;
}

export interface GraphNode {
  id: string;
  type: string;
  value: string;
  metadata: Record<string, unknown>;
  first_seen: string;
  last_seen: string;
}

export interface GraphEdge {
  id: string;
  parent_id: string;
  child_id: string;
  relation_type: string;
}

export interface GraphView {
  program_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** Raw `/programs/{id}/graph` payload before flattening for tables. */
export interface GraphTreeNode {
  id: string;
  type: string;
  value: string;
  metadata: Record<string, unknown>;
  first_seen: string;
  last_seen: string;
  children: GraphTreeNode[];
}

export interface HierarchicalGraphView {
  program_id: string;
  roots: GraphTreeNode[];
  orphans: GraphTreeNode[];
}

export interface IngestAssetResponse {
  asset_id: string;
  relation_id: string | null;
}

export interface SubdomainDiscoveryResponse {
  task_id: string;
  status: string;
}

export interface CeleryTaskStatus {
  task_id: string;
  state: string;
  result?: unknown;
  error?: string | null;
}
