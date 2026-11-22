"""The Objective Orchestrator service.

The Orchestrator answers: "What work must the organization perform?"

It sits above the Engineer in the reasoning hierarchy. Where the Engineer
solves technical problems and manages projects, the Orchestrator interprets
objectives, identifies domains, creates projects, constructs the Project
Work Graph, and determines when objectives are complete.

Responsibilities:
    - interpret objective text into actionable project structure
    - identify which domains (engineering, research, operations) are needed
    - create projects with appropriate WorkProfile assignments
    - construct the Project Work Graph with workstream dependencies
    - assign ResourceScope and ExecutionProfile to workstreams
    - invoke the Engineer for technical reasoning within engineering domains
    - consume accepted artifacts and determine cross-profile propagation
    - respond to findings by restructuring the graph when required
    - determine project and objective completion

The Orchestrator NEVER:
    - selects a provider or model (routing is downstream)
    - assigns specialists to work (the Taskforce and Project Manager do that)
    - mutates authority (the Registry and Sequencer do that)
    - certifies results (the verifiers do that)

Design:
    role:              ORCHESTRATOR
    model:             claude-opus-4-6[1m]
    execution_profile: opus-orchestrator/v1

The service owns the ORCHESTRATION CYCLE: it reads accepted state through
a curated OrchestratorPlanningView, invokes the OrchestratorPolicy behind
the reasoning seam, and returns typed orchestration decisions. The policy
holds no authority handle — it receives plain data and returns proposals.

Deterministic policies drive the tests; the production policy reasons with
a model behind the same seam.
"""

import hashlib
import json

from autobot.planning_view import build_orchestrator_view, enrich_view
from autobot.work_profile import VALID_PROFILES
from autobot.resource_scope import RESOURCE_KINDS, ACTIVITY_CLASSES


ORCHESTRATOR_POLICY_ID = "reasoning-orchestrator/v1"

ORCHESTRATOR_ROLE = "ORCHESTRATOR"

ORCHESTRATOR_DECISION_CLASSES = frozenset({
    # Node-oriented (canonical v2)
    "CREATE_NODE",
    "SET_NODE_WORK_PROFILE",
    "SET_NODE_RESOURCE_SCOPE",
    "SET_NODE_EXECUTION_PROFILE",
    "SET_NODE_ROLE",
    "SET_NODE_REALIZATION_KIND",
    "ADD_EDGE",
    "CREATE_PROJECT",
    "BIND_NODE_TO_PROJECT",
    "COMPLETE_NODE",
    "COMPLETE_RUN",
    "RESTRUCTURE_RUN",
    "REQUEST_ENGINEERING_DECISION",
    "REQUEST_CAPABILITY",
    "REQUEST_REVIEW",
    # Compatibility aliases (v1 → v2 mapping)
    "CREATE_WORKSTREAM",
    "SET_WORK_PROFILE",
    "SET_RESOURCE_SCOPE",
    "SET_EXECUTION_PROFILE",
    "SET_ROLE",
    "ADD_DEPENDENCY",
    "COMPLETE_WORKSTREAM",
    "COMPLETE_PROJECT",
    "RESTRUCTURE_PROJECT",
})

_V1_TO_V2 = {
    "CREATE_WORKSTREAM": "CREATE_NODE",
    "ADD_DEPENDENCY": "ADD_EDGE",
    "COMPLETE_WORKSTREAM": "COMPLETE_NODE",
    "COMPLETE_PROJECT": "COMPLETE_RUN",
    "RESTRUCTURE_PROJECT": "RESTRUCTURE_RUN",
    "SET_WORK_PROFILE": "SET_NODE_WORK_PROFILE",
    "SET_RESOURCE_SCOPE": "SET_NODE_RESOURCE_SCOPE",
    "SET_EXECUTION_PROFILE": "SET_NODE_EXECUTION_PROFILE",
    "SET_ROLE": "SET_NODE_ROLE",
}

REALIZATION_KINDS = frozenset({
    "TASK_CONTRACT",
    "ENGINEER_REASONING",
    "CONTROL",
})

DEPENDENCY_KINDS = frozenset({
    "ACCEPTED_ARTIFACT",
    "ACCEPTED_DECISION",
    "REVIEW_OF",
    "COMPLETION",
})


def _decision_digest(decision):
    """Deterministic identity for an orchestration decision."""
    canonical = json.dumps(decision, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _taskforce_digest_input(taskforce):
    """Extract digest-safe representation from taskforce state.
    Handles both the structured format (lists of dicts) and simple
    count format (ints) for backwards compatibility."""
    members_raw = taskforce.get("members") or []
    seats_raw = taskforce.get("seats") or []
    if isinstance(members_raw, (int, float)):
        members = [str(members_raw)]
    elif isinstance(members_raw, list):
        members = sorted(
            m.get("specialist_id", "") if isinstance(m, dict)
            else str(m) for m in members_raw)
    else:
        members = [str(members_raw)]
    if isinstance(seats_raw, (int, float)):
        seats = [str(seats_raw)]
    elif isinstance(seats_raw, list):
        seats = sorted(
            (s.get("seat_id", ""), s.get("capability", ""),
             s.get("status", "")) if isinstance(s, dict)
            else (str(s),) for s in seats_raw)
    else:
        seats = [str(seats_raw)]
    return {"members": members, "seats": seats}


class OrchestrationDecisionError(Exception):
    """A typed orchestration decision failed structural validation."""


def normalize_decision(decision):
    """Normalize a v1 decision class to v2 if applicable.

    Returns a copy with the canonical v2 class and original v1 class
    preserved as 'original_class' when translated."""
    d = dict(decision)
    cls = d.get("class")
    if cls in _V1_TO_V2:
        d["original_class"] = cls
        d["class"] = _V1_TO_V2[cls]
        if cls == "CREATE_WORKSTREAM" and "workstream_key" in d \
                and "node_key" not in d:
            d["node_key"] = d["workstream_key"]
        if cls == "ADD_DEPENDENCY":
            if "source_workstream" in d and "source_node" not in d:
                d["source_node"] = d["source_workstream"]
            if "target_workstream" in d and "target_node" not in d:
                d["target_node"] = d["target_workstream"]
        if cls == "COMPLETE_WORKSTREAM" and "workstream_id" in d \
                and "node_id" not in d:
            d["node_id"] = d["workstream_id"]
    return d


def _backfill_context(decisions, objectives):
    """Normalize model output before validation.

    The Opus model may use alternative field names or omit context
    that the validation contract requires. This bridges the model's
    natural output to the strict decision schema."""
    oid = objectives[0].get("objective_id") if objectives else None
    for d in decisions:
        if not isinstance(d, dict):
            continue
        cls = d.get("class", "")
        if cls == "CREATE_PROJECT":
            if not d.get("objective_id") and oid:
                d["objective_id"] = oid
            if not d.get("title"):
                obj_text = objectives[0].get("text", "") if objectives else ""
                d["title"] = (d.get("name") or d.get("project_name")
                              or obj_text[:120] or "Orchestrated project")
        if cls in ("CREATE_NODE", "CREATE_WORKSTREAM"):
            if not d.get("node_key"):
                for alt in ("key", "name", "id", "node_name",
                            "workstream_key"):
                    if d.get(alt):
                        d["node_key"] = d[alt]
                        break
            if not d.get("realization_kind"):
                d.setdefault("realization_kind", "TASK_CONTRACT")
            if not d.get("work_profile"):
                role = (d.get("organizational_role") or "").lower()
                if role == "reviewer":
                    d.setdefault("work_profile", "RESEARCH")
                elif d.get("capability") in ("research", "verification"):
                    d.setdefault("work_profile", "RESEARCH")
                else:
                    d.setdefault("work_profile", "RESEARCH")
        if cls in ("ADD_EDGE", "ADD_DEPENDENCY"):
            for src in ("source", "from", "from_node", "predecessor"):
                if not d.get("source_node") and d.get(src):
                    d["source_node"] = d[src]
            for tgt in ("target", "to", "to_node", "successor"):
                if not d.get("target_node") and d.get(tgt):
                    d["target_node"] = d[tgt]


def validate_orchestration_decision(decision):
    """Validate the structure of one orchestration decision."""
    if not isinstance(decision, dict):
        raise OrchestrationDecisionError("decision must be a dict")
    cls = decision.get("class")
    if cls not in ORCHESTRATOR_DECISION_CLASSES:
        raise OrchestrationDecisionError(
            "unknown decision class %r" % cls)

    if cls == "CREATE_NODE" or cls == "CREATE_WORKSTREAM":
        if not (decision.get("node_key") or decision.get("workstream_key")):
            raise OrchestrationDecisionError(
                "%s requires node_key" % cls)
    elif cls == "CREATE_PROJECT":
        if not decision.get("objective_id"):
            raise OrchestrationDecisionError(
                "CREATE_PROJECT requires objective_id")
        if not decision.get("title"):
            raise OrchestrationDecisionError(
                "CREATE_PROJECT requires title")
    elif cls in ("SET_WORK_PROFILE", "SET_NODE_WORK_PROFILE"):
        wp = decision.get("work_profile")
        if wp not in VALID_PROFILES:
            raise OrchestrationDecisionError(
                "%s requires valid work_profile, got %r" % (cls, wp))
    elif cls in ("ADD_DEPENDENCY", "ADD_EDGE"):
        src = decision.get("source_node") or decision.get("source_workstream")
        tgt = decision.get("target_node") or decision.get("target_workstream")
        if not src:
            raise OrchestrationDecisionError(
                "%s requires source_node" % cls)
        if not tgt:
            raise OrchestrationDecisionError(
                "%s requires target_node" % cls)
        dk = decision.get("dependency_kind")
        if dk not in DEPENDENCY_KINDS:
            raise OrchestrationDecisionError(
                "%s requires valid dependency_kind, got %r" % (cls, dk))
    elif cls == "REQUEST_ENGINEERING_DECISION":
        if not decision.get("question"):
            raise OrchestrationDecisionError(
                "REQUEST_ENGINEERING_DECISION requires question")
    elif cls == "SET_NODE_REALIZATION_KIND":
        rk = decision.get("realization_kind")
        if rk not in REALIZATION_KINDS:
            raise OrchestrationDecisionError(
                "SET_NODE_REALIZATION_KIND requires valid realization_kind,"
                " got %r" % rk)
    return decision


def validate_orchestration_decisions(decisions, lenient=False):
    """Validate a batch of orchestration decisions.

    With lenient=True, skip individual invalid decisions instead of
    raising. The production Opus Orchestrator uses lenient mode
    because the model may produce structurally imperfect output."""
    validated = []
    for d in decisions:
        try:
            validated.append(validate_orchestration_decision(d))
        except OrchestrationDecisionError:
            if not lenient:
                raise
    return validated


class OrchestratorPolicy:
    """The orchestration reasoning seam.

    Implementations return typed orchestration decisions for one
    OrchestratorPlanningView; they hold no authority handles.
    """

    policy_id = ORCHESTRATOR_POLICY_ID
    lenient_validation = False

    def interpret(self, view):
        """Given the orchestrator's planning view, return orchestration
        decisions: project creation, workstream layout, domain assignment,
        dependency specification, delegation to Engineer, completion."""
        raise NotImplementedError

    def should_delegate_to_engineer(self, view, project):
        """Determine whether a project requires Engineer reasoning.

        Returns True when the project contains engineering work that
        needs technical problem-solving beyond the Orchestrator's scope."""
        wp = project.get("work_profile")
        if wp == "ENGINEERING":
            return True
        workstreams = project.get("workstreams", [])
        return any(ws.get("work_profile") == "ENGINEERING"
                   for ws in workstreams)


class DeterministicOrchestratorPolicy(OrchestratorPolicy):
    """A test policy that returns pre-scripted orchestration decisions."""

    policy_id = "deterministic-orchestrator/v1"

    def __init__(self, decisions=None):
        self._decisions = list(decisions or [])

    def interpret(self, view):
        return list(self._decisions)


class PassthroughOrchestratorPolicy(OrchestratorPolicy):
    """Passes objectives directly to the Engineer unchanged.

    This is the transitional policy: the Orchestrator exists in the loop
    but all reasoning still flows through the Engineer. It preserves the
    exact pre-Orchestrator behavior while the integration is proven."""

    policy_id = "passthrough-orchestrator/v1"

    def interpret(self, view):
        return []

    def should_delegate_to_engineer(self, view, project):
        return True


class OrchestratorPlanningView:
    """Everything the Orchestrator reasons over, as plain data.

    Built from authority-accepted state, the intelligence store, and the
    Taskforce roster. The canonical view is now OrchestrationRun/Node
    oriented. Projects appear as optional bindings.
    """

    def __init__(self, objectives, projects, taskforce_state,
                 accepted_artifacts=None, findings=None,
                 failed_attempts=None, effect_outcomes=None,
                 work_profiles=None, resource_scopes=None,
                 execution_profiles=None, dependency_state=None,
                 runs=None, nodes=None, edges=None,
                 authority_bindings=None, engineering_bindings=None,
                 project_bindings=None, node_outputs=None):
        self.objectives = list(objectives)
        self.projects = list(projects)
        self.taskforce = dict(taskforce_state) if taskforce_state else {}
        self.accepted_artifacts = list(accepted_artifacts or [])
        self.findings = list(findings or [])
        self.failed_attempts = list(failed_attempts or [])
        self.effect_outcomes = list(effect_outcomes or [])
        self.work_profiles = dict(work_profiles or {})
        self.resource_scopes = dict(resource_scopes or {})
        self.execution_profiles = dict(execution_profiles or {})
        self.dependency_state = dict(dependency_state or {})
        self.runs = list(runs or [])
        self.nodes = list(nodes or [])
        self.edges = list(edges or [])
        self.authority_bindings = list(authority_bindings or [])
        self.engineering_bindings = list(engineering_bindings or [])
        self.project_bindings = list(project_bindings or [])
        self.node_outputs = list(node_outputs or [])

    def for_objective(self, objective_id):
        """Return the view narrowed to one objective."""
        obj = next((o for o in self.objectives
                    if o.get("objective_id") == objective_id), None)
        if obj is None:
            return None
        projects = [p for p in self.projects
                    if p.get("objective_id") == objective_id]
        run_ids = {r.get("orchestration_id") for r in self.runs
                   if r.get("objective_id") == objective_id}
        return OrchestratorPlanningView(
            objectives=[obj],
            projects=projects,
            taskforce_state=self.taskforce,
            accepted_artifacts=[a for a in self.accepted_artifacts
                                if a.get("objective_id") == objective_id],
            findings=[f for f in self.findings
                      if f.get("objective_id") == objective_id],
            failed_attempts=[a for a in self.failed_attempts
                             if a.get("objective_id") == objective_id],
            effect_outcomes=self.effect_outcomes,
            work_profiles=self.work_profiles,
            resource_scopes=self.resource_scopes,
            execution_profiles=self.execution_profiles,
            dependency_state=self.dependency_state,
            runs=[r for r in self.runs
                  if r.get("objective_id") == objective_id],
            nodes=[n for n in self.nodes
                   if n.get("orchestration_id") in run_ids],
            edges=[e for e in self.edges
                   if e.get("orchestration_id") in run_ids],
            authority_bindings=self.authority_bindings,
            engineering_bindings=self.engineering_bindings,
            project_bindings=self.project_bindings,
            node_outputs=self.node_outputs,
        )

    def to_dict(self):
        """Plain-data representation for the reasoning seam."""
        return {
            "role": ORCHESTRATOR_ROLE,
            "objectives": self.objectives,
            "projects": self.projects,
            "taskforce": self.taskforce,
            "accepted_artifacts": self.accepted_artifacts,
            "findings": self.findings,
            "failed_attempts": self.failed_attempts,
            "effect_outcomes": self.effect_outcomes,
            "work_profiles": self.work_profiles,
            "resource_scopes": self.resource_scopes,
            "execution_profiles": self.execution_profiles,
            "dependency_state": self.dependency_state,
            "runs": self.runs,
            "nodes": self.nodes,
            "edges": self.edges,
            "authority_bindings": self.authority_bindings,
            "engineering_bindings": self.engineering_bindings,
            "project_bindings": self.project_bindings,
            "node_outputs": self.node_outputs,
        }


class OrchestratorService:
    """The Orchestrator's cycle mechanics.

    Each cycle:
    1. Build the OrchestratorPlanningView from accepted state
    2. Invoke the OrchestratorPolicy to interpret objectives
    3. For projects requiring engineering, delegate to the Engineer
    4. Return the combined orchestration + delegation decisions

    The service holds no authority handle. Its output is orchestration
    decisions that the driver integrates into the existing proposal path.
    """

    def __init__(self, plan, policy, clock=None):
        self.plan = plan
        self.policy = policy
        self.clock = clock
        self._last_cycle = None

    def build_view(self, objectives, projects, taskforce_state, **kwargs):
        return OrchestratorPlanningView(
            objectives=objectives,
            projects=projects,
            taskforce_state=taskforce_state,
            **kwargs)

    def _compute_organizational_digest(self, view):
        """Canonical digest over all state that can change an orchestration
        decision. Two materially different organizational states must
        produce different digests. Uses Run/Node state as primary."""
        return _decision_digest({
            "objectives": sorted(
                (o.get("objective_id", ""), o.get("status", ""))
                for o in view.objectives),
            "runs": sorted(
                (r.get("orchestration_id", ""), r.get("mode", ""),
                 r.get("status", ""))
                for r in view.runs),
            "nodes": sorted(
                (n.get("node_id", ""), n.get("node_key", ""),
                 n.get("work_profile") or "", n.get("status") or "",
                 n.get("realization_kind") or "",
                 n.get("organizational_role") or "",
                 n.get("execution_profile") or "",
                 n.get("capability") or "")
                for n in view.nodes),
            "edges": sorted(
                (e.get("edge_id", ""), e.get("source_node_id", ""),
                 e.get("target_node_id", ""), e.get("status") or "",
                 e.get("dependency_kind") or "")
                for e in view.edges),
            "authority_bindings": sorted(
                (b.get("node_id", ""), b.get("sequence_id") or "",
                 b.get("status") or "")
                for b in view.authority_bindings),
            "projects": sorted(
                (p.get("project_id", ""), p.get("status", ""))
                for p in view.projects),
            "workstreams": sorted(
                (w.get("workstream_id", ""), w.get("workstream_key", ""),
                 w.get("work_profile") or "", w.get("status") or "",
                 w.get("execution_profile") or "")
                for p in view.projects
                for w in (p.get("workstreams") or [])),
            "dependencies": sorted(
                str(d) for d in (view.dependency_state or {}).items()),
            "accepted_artifacts": sorted(
                a.get("content_sha256", "") if isinstance(a, dict)
                else str(a) for a in view.accepted_artifacts),
            "node_outputs": sorted(
                (o.get("node_id", ""), o.get("output_kind", ""),
                 o.get("content_sha256") or "")
                for o in view.node_outputs),
            "findings": sorted(
                f.get("finding_id", "") if isinstance(f, dict)
                else str(f) for f in view.findings),
            "failed_attempts": sorted(
                a.get("attempt_id", "") if isinstance(a, dict)
                else str(a) for a in view.failed_attempts),
            "taskforce": _taskforce_digest_input(view.taskforce),
        })

    def run_cycle(self, objectives, projects, taskforce_state, **kwargs):
        """One orchestration cycle.

        Returns a report with orchestration decisions and which projects
        should be delegated to the Engineer for technical reasoning.

        Exactly-once semantics: if a durable reasoning record exists for
        the same organizational input digest AND its decisions have been
        applied, those decisions are not re-applied. If the input digest
        is new, the policy is invoked and decisions are recorded before
        application."""
        if not objectives:
            return {
                "orchestrator_cycle": True,
                "decisions": [],
                "delegate_to_engineer": [],
                "policy_id": self.policy.policy_id,
                "had_activity": False,
            }

        view = self.build_view(objectives, projects, taskforce_state,
                               **kwargs)

        input_digest = self._compute_organizational_digest(view)

        replayed = False
        already_applied = False
        if self.plan and hasattr(self.plan, "orchestration_for_digest"):
            existing = self.plan.orchestration_for_digest(input_digest)
            if existing is not None:
                decisions = json.loads(existing["decisions"])
                replayed = True
                already_applied = existing.get("application_results") \
                    is not None
            else:
                decisions = self.policy.interpret(view)
                if decisions:
                    _backfill_context(decisions, objectives)
                    decisions = validate_orchestration_decisions(
                        decisions,
                        lenient=self.policy.lenient_validation)
        else:
            decisions = self.policy.interpret(view)
            if decisions:
                _backfill_context(decisions, objectives)
                decisions = validate_orchestration_decisions(
                    decisions,
                    lenient=self.policy.lenient_validation)

        delegate = []
        for project in projects:
            if self.policy.should_delegate_to_engineer(view, project):
                delegate.append(project.get("project_id")
                                or project.get("title", "unnamed"))

        had_activity = bool(decisions) and not replayed

        if had_activity and self.plan and \
                hasattr(self.plan, "record_orchestration_reasoning"):
            from autobot.models import instant
            now = instant(self.clock.now()) if self.clock else None
            if now:
                self.plan.record_orchestration_reasoning(
                    input_digest=input_digest,
                    policy_id=self.policy.policy_id,
                    decisions_json=json.dumps(decisions, sort_keys=True),
                    now=now,
                    objectives=[o.get("objective_id")
                                for o in objectives])

        self._last_cycle = {
            "orchestrator_cycle": True,
            "decisions": decisions if not already_applied else [],
            "delegate_to_engineer": delegate,
            "policy_id": self.policy.policy_id,
            "input_digest": input_digest,
            "objectives_count": len(objectives),
            "projects_count": len(projects),
            "had_activity": had_activity,
            "replayed": replayed,
            "already_applied": already_applied,
        }
        return dict(self._last_cycle)


class OrchestrationApplicator:
    """Apply validated orchestration decisions to durable organizational state.

    The Orchestrator produces typed decisions. This layer persists them
    to both the new OrchestrationNode model and the legacy workstream
    model (for backward compatibility with the existing driver and
    Taskforce paths).

    The Orchestrator itself holds no persistence handle.
    """

    def __init__(self, plan, orchestration_id=None):
        self.plan = plan
        self.orchestration_id = orchestration_id

    def apply(self, decisions, now):
        """Apply a batch of validated orchestration decisions.

        Returns a list of application results, one per decision."""
        results = []
        for decision in decisions:
            normalized = normalize_decision(decision)
            result = self._apply_one(normalized, now)
            results.append(result)
        return results

    def _resolve_node_ref(self, ref, orchestration_id):
        """Resolve a node reference to a node_id.

        Accepts either a node_id (returned as-is) or a node_key
        (looked up within the given run). This lets the Opus
        Orchestrator use node_keys in ADD_EDGE decisions for nodes
        it created in the same batch."""
        if hasattr(self.plan, "orchestration_node"):
            node = self.plan.orchestration_node(ref)
            if node is not None:
                return ref
        if orchestration_id and \
                hasattr(self.plan, "orchestration_nodes"):
            for n in self.plan.orchestration_nodes(orchestration_id):
                if n.get("node_key") == ref:
                    return n["node_id"]
        return ref

    def _apply_one(self, decision, now):
        cls = decision["class"]
        decision_id = "orc_" + _decision_digest(decision)[:32]

        if cls == "CREATE_PROJECT":
            return self._apply_create_project(decision, decision_id, now)
        elif cls == "CREATE_NODE":
            return self._apply_create_node(decision, decision_id, now)
        elif cls == "ADD_EDGE":
            return self._apply_add_edge(decision, decision_id, now)
        elif cls in ("SET_NODE_WORK_PROFILE", "SET_WORK_PROFILE"):
            return self._apply_set_node_dimension(
                decision, decision_id, "work_profile", now)
        elif cls in ("SET_NODE_RESOURCE_SCOPE", "SET_RESOURCE_SCOPE"):
            return self._apply_set_node_dimension(
                decision, decision_id, "resource_scope", now)
        elif cls in ("SET_NODE_EXECUTION_PROFILE", "SET_EXECUTION_PROFILE"):
            return self._apply_set_node_dimension(
                decision, decision_id, "execution_profile", now)
        elif cls in ("SET_NODE_ROLE", "SET_ROLE"):
            return self._apply_set_node_dimension(
                decision, decision_id, "organizational_role", now)
        elif cls == "SET_NODE_REALIZATION_KIND":
            return self._apply_set_node_dimension(
                decision, decision_id, "realization_kind", now)
        elif cls in ("COMPLETE_NODE", "COMPLETE_WORKSTREAM"):
            return self._apply_complete_node(
                decision, decision_id, now)
        elif cls in ("COMPLETE_RUN", "COMPLETE_PROJECT"):
            return self._apply_complete_run(
                decision, decision_id, now)
        elif cls == "BIND_NODE_TO_PROJECT":
            return self._apply_bind_to_project(
                decision, decision_id, now)
        elif cls == "REQUEST_ENGINEERING_DECISION":
            return self._apply_engineering_request(
                decision, decision_id, now)
        elif cls == "REQUEST_CAPABILITY":
            return self._apply_capability_request(
                decision, decision_id, now)
        elif cls == "REQUEST_REVIEW":
            return self._apply_review_request(
                decision, decision_id, now)
        elif cls in ("RESTRUCTURE_RUN", "RESTRUCTURE_PROJECT"):
            return self._apply_restructure(
                decision, decision_id, now)
        return {"decision_id": decision_id, "class": cls,
                "status": "UNKNOWN"}

    def _apply_create_project(self, decision, decision_id, now):
        objective_id = decision["objective_id"]
        title = decision["title"]
        solution = decision.get("solution", "")
        with self.plan.transaction() as cur:
            project, created = self.plan.open_project(
                cur, objective_id, title, solution,
                "IMPLICIT", decision_id, now)
        return {"decision_id": decision_id, "class": "CREATE_PROJECT",
                "status": "CREATED" if created else "EXISTS",
                "project_id": project["project_id"],
                "opened_by_orchestrator": True}

    def _apply_create_node(self, decision, decision_id, now):
        from .proposals_util import canonical_json_digest
        node_key = decision.get("node_key") \
            or decision.get("workstream_key")
        wp = decision.get("work_profile")
        ep = decision.get("execution_profile")
        rs = decision.get("resource_scope")
        role = decision.get("organizational_role")
        rk = decision.get("realization_kind")
        cap = decision.get("capability")
        cp = decision.get("completion_policy")
        project_id = decision.get("project_id")

        orch_id = self.orchestration_id or decision.get("orchestration_id")

        if orch_id:
            node_id = "nd_" + canonical_json_digest({
                "run": orch_id, "key": node_key,
            })[:32]
            with self.plan.transaction() as cur:
                node = self.plan.create_orchestration_node(
                    cur, node_id, orch_id, node_key, now,
                    title=decision.get("title"),
                    description=decision.get("description"),
                    work_profile=wp, organizational_role=role,
                    realization_kind=rk, capability=cap,
                    resource_scope=rs, execution_profile=ep,
                    completion_policy=cp,
                    created_by_decision=decision_id)
            result = {"decision_id": decision_id, "class": "CREATE_NODE",
                      "status": "CREATED", "node_id": node_id,
                      "node_key": node_key}
        else:
            result = {"decision_id": decision_id, "class": "CREATE_NODE",
                      "status": "SKIPPED",
                      "note": "no orchestration_id available"}

        if project_id and hasattr(self.plan, "add_workstream"):
            with self.plan.transaction() as cur:
                ws = self.plan.add_workstream(
                    cur, project_id, node_key, decision_id, now,
                    work_profile=wp, organizational_role=role,
                    resource_scope=rs, execution_profile=ep,
                    completion_policy=cp)
            result["workstream_id"] = ws["workstream_id"]

        return result

    def _apply_add_edge(self, decision, decision_id, now):
        source = decision.get("source_node") \
            or decision.get("source_workstream")
        target = decision.get("target_node") \
            or decision.get("target_workstream")
        kind = decision["dependency_kind"]
        orch_id = self.orchestration_id or decision.get("orchestration_id")
        if orch_id:
            source = self._resolve_node_ref(source, orch_id)
            target = self._resolve_node_ref(target, orch_id)

        if orch_id and hasattr(self.plan, "create_orchestration_edge"):
            from .proposals_util import canonical_json_digest
            edge_id = "oe_" + canonical_json_digest({
                "source": source, "target": target, "kind": kind,
            })[:32]
            with self.plan.transaction() as cur:
                self.plan.create_orchestration_edge(
                    cur, edge_id, orch_id, source, target, kind)

        if hasattr(self.plan, "add_workstream_dependency"):
            try:
                with self.plan.transaction() as cur:
                    self.plan.add_workstream_dependency(
                        cur, source, target, kind)
            except Exception:
                pass

        return {"decision_id": decision_id, "class": "ADD_EDGE",
                "status": "CREATED",
                "source": source, "target": target}

    def _apply_set_node_dimension(self, decision, decision_id, field, now):
        node_id = decision.get("node_id") or decision.get("workstream_id")
        value = decision.get(field)
        if not node_id or value is None:
            return {"decision_id": decision_id, "class": decision["class"],
                    "status": "SKIPPED",
                    "note": "missing node_id or value"}

        if hasattr(self.plan, "update_node_dimension"):
            try:
                with self.plan.transaction() as cur:
                    self.plan.update_node_dimension(
                        cur, node_id, field, value)
            except Exception:
                pass

        serialized = json.dumps(value, sort_keys=True) \
            if isinstance(value, (list, dict)) else value
        try:
            with self.plan.transaction() as cur:
                cur.execute(
                    "UPDATE project_workstreams SET %s = ?"
                    " WHERE workstream_id = ?" % field,
                    (serialized, node_id))
        except Exception:
            pass

        return {"decision_id": decision_id, "class": decision["class"],
                "status": "APPLIED", "node_id": node_id,
                "field": field, "value": value}

    def _apply_complete_node(self, decision, decision_id, now):
        node_id = decision.get("node_id") or decision.get("workstream_id")
        if not node_id:
            return {"decision_id": decision_id,
                    "class": "COMPLETE_NODE",
                    "status": "SKIPPED", "note": "missing node_id"}

        if hasattr(self.plan, "update_node_status"):
            with self.plan.transaction() as cur:
                self.plan.update_node_status(
                    cur, node_id, "COMPLETED", now)

        try:
            with self.plan.transaction() as cur:
                cur.execute(
                    "UPDATE project_workstreams SET status = 'COMPLETED'"
                    " WHERE workstream_id = ? AND status != 'COMPLETED'",
                    (node_id,))
        except Exception:
            pass

        return {"decision_id": decision_id, "class": "COMPLETE_NODE",
                "status": "APPLIED", "node_id": node_id}

    def _apply_complete_run(self, decision, decision_id, now):
        run_id = decision.get("orchestration_id") \
            or decision.get("project_id")
        objective_id = decision.get("objective_id")

        if run_id and hasattr(self.plan, "complete_orchestration_run"):
            with self.plan.transaction() as cur:
                self.plan.complete_orchestration_run(cur, run_id, now)

        project_id = decision.get("project_id")
        if project_id:
            with self.plan.transaction() as cur:
                cur.execute(
                    "UPDATE projects SET status = 'COMPLETED',"
                    " closed_utc = ?, closed_by = ?"
                    " WHERE project_id = ? AND status = 'ACTIVE'",
                    (now, decision_id, project_id))
                if objective_id:
                    cur.execute(
                        "UPDATE objectives SET status = 'COMPLETE',"
                        " completed_utc = ?"
                        " WHERE objective_id = ? AND status = 'ACTIVE'",
                        (now, objective_id))

        return {"decision_id": decision_id, "class": "COMPLETE_RUN",
                "status": "APPLIED",
                "orchestration_id": run_id,
                "project_id": project_id}

    def _apply_bind_to_project(self, decision, decision_id, now):
        node_id = decision.get("node_id")
        project_id = decision.get("project_id")
        if not node_id or not project_id:
            return {"decision_id": decision_id,
                    "class": "BIND_NODE_TO_PROJECT",
                    "status": "SKIPPED",
                    "note": "missing node_id or project_id"}
        with self.plan.transaction() as cur:
            self.plan.create_project_binding(
                cur, node_id, project_id, now,
                binding_reason=decision.get("reason"))
        return {"decision_id": decision_id,
                "class": "BIND_NODE_TO_PROJECT",
                "status": "APPLIED",
                "node_id": node_id, "project_id": project_id}

    def _apply_engineering_request(self, decision, decision_id, now):
        node_id = decision.get("node_id") or decision.get("workstream_id")
        question = decision.get("question", "")
        orch_id = self.orchestration_id or decision.get("orchestration_id")

        if node_id and orch_id and \
                hasattr(self.plan, "create_engineering_binding"):
            from .proposals_util import canonical_json_digest
            req_id = "er_" + canonical_json_digest({
                "node": node_id, "question": question,
            })[:32]
            with self.plan.transaction() as cur:
                self.plan.create_engineering_binding(
                    cur, req_id, node_id, orch_id, question, now)

        if node_id:
            try:
                with self.plan.transaction() as cur:
                    self.plan.update_node_dimension(
                        cur, node_id, "organizational_role", "ENGINEER")
            except Exception:
                pass
            try:
                with self.plan.transaction() as cur:
                    cur.execute(
                        "UPDATE project_workstreams"
                        " SET organizational_role = 'ENGINEER'"
                        " WHERE workstream_id = ?", (node_id,))
            except Exception:
                pass

        return {"decision_id": decision_id,
                "class": "REQUEST_ENGINEERING_DECISION",
                "status": "APPLIED",
                "node_id": node_id, "question": question}

    def _apply_capability_request(self, decision, decision_id, now):
        return {"decision_id": decision_id,
                "class": "REQUEST_CAPABILITY",
                "status": "APPLIED",
                "capability": decision.get("capability"),
                "node_id": decision.get("node_id")
                or decision.get("workstream_id")}

    def _apply_review_request(self, decision, decision_id, now):
        return {"decision_id": decision_id,
                "class": "REQUEST_REVIEW",
                "status": "APPLIED",
                "node_id": decision.get("node_id")
                or decision.get("workstream_id")}

    def _apply_restructure(self, decision, decision_id, now):
        return {"decision_id": decision_id,
                "class": "RESTRUCTURE_RUN",
                "status": "APPLIED",
                "rationale": decision.get("rationale", ""),
                "orchestration_id": decision.get("orchestration_id")
                or decision.get("project_id")}
