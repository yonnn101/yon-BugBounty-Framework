import type { GraphEdge, GraphNode, GraphTreeNode, HierarchicalGraphView } from "../api/types";

/**
 * Walk hierarchical API response (roots + orphans) into node/edge tables for legacy UI.
 * Tree edges use relation_type `nested` (structure is implied by AssetRelation on the server).
 */
export function flattenHierarchicalGraph(view: HierarchicalGraphView): {
  nodes: GraphNode[];
  edges: GraphEdge[];
} {
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  let edgeSeq = 0;

  const visit = (node: GraphTreeNode, parentId: string | null) => {
    nodes.set(node.id, {
      id: node.id,
      type: node.type,
      value: node.value,
      metadata: (node.metadata ?? {}) as Record<string, unknown>,
      first_seen: node.first_seen,
      last_seen: node.last_seen,
    });
    if (parentId !== null) {
      edges.push({
        id: `tree-${edgeSeq++}`,
        parent_id: parentId,
        child_id: node.id,
        relation_type: "nested",
      });
    }
    for (const ch of node.children ?? []) {
      visit(ch, node.id);
    }
  };

  for (const r of view.roots ?? []) visit(r, null);
  for (const o of view.orphans ?? []) visit(o, null);

  return { nodes: [...nodes.values()], edges };
}
