"""Mixed Project Work Graph for cross-profile orchestration.

A Project Work Graph is the Orchestrator's view of an objective's
decomposition into workstreams with their dependencies. Each workstream
has a WorkProfile, ResourceScope, and ExecutionProfile.

Dependencies propagate accepted artifacts: a workstream that depends on
a predecessor receives the predecessor's accepted artifact as input.

The graph supports mixed projects where RESEARCH, ENGINEERING, and
GENERAL_OPERATION workstreams coexist under one Orchestrator.
"""

DEPENDENCY_KINDS = frozenset({
    "ACCEPTED_ARTIFACT",
    "ACCEPTED_DECISION",
    "REVIEW_OF",
    "COMPLETION",
})


class WorkNode:
    """One workstream in a project graph."""

    __slots__ = ("node_id", "work_profile", "execution_profile",
                 "resource_scope", "role", "description",
                 "predecessors", "status")

    def __init__(self, node_id, work_profile, execution_profile=None,
                 resource_scope=None, role="SPECIALIST",
                 description="", predecessors=None):
        self.node_id = node_id
        self.work_profile = work_profile
        self.execution_profile = execution_profile
        self.resource_scope = resource_scope
        self.role = role
        self.description = description
        self.predecessors = list(predecessors or [])
        self.status = "PENDING"

    def to_dict(self):
        return {
            "node_id": self.node_id,
            "work_profile": self.work_profile,
            "execution_profile": self.execution_profile,
            "resource_scope": self.resource_scope,
            "role": self.role,
            "description": self.description,
            "predecessors": list(self.predecessors),
            "status": self.status,
        }


class Dependency:
    """A dependency edge between workstream nodes."""

    __slots__ = ("source", "target", "kind")

    def __init__(self, source, target, kind="ACCEPTED_ARTIFACT"):
        if kind not in DEPENDENCY_KINDS:
            raise ValueError("unknown dependency kind %r" % kind)
        self.source = source
        self.target = target
        self.kind = kind

    def to_dict(self):
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
        }


class ProjectGraph:
    """The Orchestrator's project work graph."""

    def __init__(self, objective_id, description=""):
        self.objective_id = objective_id
        self.description = description
        self._nodes = {}
        self._edges = []

    def add_node(self, node):
        if node.node_id in self._nodes:
            raise ValueError("duplicate node %r" % node.node_id)
        self._nodes[node.node_id] = node
        return node

    def add_dependency(self, source_id, target_id,
                       kind="ACCEPTED_ARTIFACT"):
        if source_id not in self._nodes:
            raise ValueError("unknown source node %r" % source_id)
        if target_id not in self._nodes:
            raise ValueError("unknown target node %r" % target_id)
        dep = Dependency(source_id, target_id, kind)
        self._edges.append(dep)
        self._nodes[target_id].predecessors.append(source_id)
        return dep

    @property
    def nodes(self):
        return dict(self._nodes)

    @property
    def edges(self):
        return list(self._edges)

    def roots(self):
        """Nodes with no predecessors."""
        return [n for n in self._nodes.values()
                if not n.predecessors]

    def leaves(self):
        """Nodes that no other node depends on."""
        targets = {e.source for e in self._edges}
        return [n for n in self._nodes.values()
                if n.node_id not in targets]

    def topological_order(self):
        """Return node_ids in dependency order (predecessors first)."""
        visited = set()
        order = []

        def visit(node_id):
            if node_id in visited:
                return
            visited.add(node_id)
            for pred_id in self._nodes[node_id].predecessors:
                visit(pred_id)
            order.append(node_id)

        for nid in sorted(self._nodes.keys()):
            visit(nid)
        return order

    def work_profiles_used(self):
        """Set of WorkProfile names used in this graph."""
        return {n.work_profile for n in self._nodes.values()}

    def execution_profiles_used(self):
        """Set of ExecutionProfile names used in this graph."""
        return {n.execution_profile for n in self._nodes.values()
                if n.execution_profile}

    def mark_completed(self, node_id):
        if node_id not in self._nodes:
            raise ValueError("unknown node %r" % node_id)
        self._nodes[node_id].status = "COMPLETED"

    def ready_nodes(self):
        """Nodes whose predecessors are all COMPLETED."""
        result = []
        for node in self._nodes.values():
            if node.status != "PENDING":
                continue
            if all(self._nodes[p].status == "COMPLETED"
                   for p in node.predecessors):
                result.append(node)
        return result

    def is_complete(self):
        """All nodes are COMPLETED."""
        return all(n.status == "COMPLETED"
                   for n in self._nodes.values())

    def to_dict(self):
        return {
            "objective_id": self.objective_id,
            "description": self.description,
            "nodes": {nid: n.to_dict()
                      for nid, n in sorted(self._nodes.items())},
            "edges": [e.to_dict() for e in self._edges],
        }
