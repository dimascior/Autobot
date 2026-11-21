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
    "CREATE_PROJECT",
    "CREATE_WORKSTREAM",
    "SET_WORK_PROFILE",
    "SET_RESOURCE_SCOPE",
    "SET_EXECUTION_PROFILE",
    "SET_ROLE",
    "ADD_DEPENDENCY",
    "REQUEST_ENGINEERING_DECISION",
    "REQUEST_CAPABILITY",
    "REQUEST_REVIEW",
    "COMPLETE_WORKSTREAM",
    "COMPLETE_PROJECT",
    "RESTRUCTURE_PROJECT",
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


class OrchestrationDecisionError(Exception):
    """A typed orchestration decision failed structural validation."""


def validate_orchestration_decision(decision):
    """Validate the structure of one orchestration decision."""
    if not isinstance(decision, dict):
        raise OrchestrationDecisionError("decision must be a dict")
    cls = decision.get("class")
    if cls not in ORCHESTRATOR_DECISION_CLASSES:
        raise OrchestrationDecisionError(
            "unknown decision class %r" % cls)
    if cls == "CREATE_PROJECT":
        if not decision.get("objective_id"):
            raise OrchestrationDecisionError(
                "CREATE_PROJECT requires objective_id")
        if not decision.get("title"):
            raise OrchestrationDecisionError(
                "CREATE_PROJECT requires title")
    elif cls == "CREATE_WORKSTREAM":
        if not decision.get("project_id"):
            raise OrchestrationDecisionError(
                "CREATE_WORKSTREAM requires project_id")
        if not decision.get("workstream_key"):
            raise OrchestrationDecisionError(
                "CREATE_WORKSTREAM requires workstream_key")
    elif cls == "SET_WORK_PROFILE":
        wp = decision.get("work_profile")
        if wp not in VALID_PROFILES:
            raise OrchestrationDecisionError(
                "SET_WORK_PROFILE requires valid work_profile, got %r" % wp)
    elif cls == "ADD_DEPENDENCY":
        if not decision.get("source_workstream"):
            raise OrchestrationDecisionError(
                "ADD_DEPENDENCY requires source_workstream")
        if not decision.get("target_workstream"):
            raise OrchestrationDecisionError(
                "ADD_DEPENDENCY requires target_workstream")
        dk = decision.get("dependency_kind")
        if dk not in DEPENDENCY_KINDS:
            raise OrchestrationDecisionError(
                "ADD_DEPENDENCY requires valid dependency_kind, got %r" % dk)
    elif cls == "REQUEST_ENGINEERING_DECISION":
        if not decision.get("question"):
            raise OrchestrationDecisionError(
                "REQUEST_ENGINEERING_DECISION requires question")
    return decision


def validate_orchestration_decisions(decisions):
    """Validate a batch of orchestration decisions."""
    validated = []
    for d in decisions:
        validated.append(validate_orchestration_decision(d))
    return validated


class OrchestratorPolicy:
    """The orchestration reasoning seam.

    Implementations return typed orchestration decisions for one
    OrchestratorPlanningView; they hold no authority handles.
    """

    policy_id = ORCHESTRATOR_POLICY_ID

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
    Taskforce roster. The Orchestrator sees the full project graph across
    all WorkProfiles, unlike the Engineer which sees only the engineering-
    relevant subset.
    """

    def __init__(self, objectives, projects, taskforce_state,
                 accepted_artifacts=None, findings=None,
                 failed_attempts=None, effect_outcomes=None,
                 work_profiles=None, resource_scopes=None,
                 execution_profiles=None, dependency_state=None):
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

    def for_objective(self, objective_id):
        """Return the view narrowed to one objective."""
        obj = next((o for o in self.objectives
                    if o.get("objective_id") == objective_id), None)
        if obj is None:
            return None
        projects = [p for p in self.projects
                    if p.get("objective_id") == objective_id]
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

    def build_view(self, objectives, projects, taskforce_state,
                   accepted_artifacts=None, findings=None,
                   failed_attempts=None, effect_outcomes=None,
                   work_profiles=None, resource_scopes=None,
                   execution_profiles=None, dependency_state=None):
        return OrchestratorPlanningView(
            objectives=objectives,
            projects=projects,
            taskforce_state=taskforce_state,
            accepted_artifacts=accepted_artifacts,
            findings=findings,
            failed_attempts=failed_attempts,
            effect_outcomes=effect_outcomes,
            work_profiles=work_profiles,
            resource_scopes=resource_scopes,
            execution_profiles=execution_profiles,
            dependency_state=dependency_state,
        )

    def run_cycle(self, objectives, projects, taskforce_state, **kwargs):
        """One orchestration cycle.

        Returns a report with orchestration decisions and which projects
        should be delegated to the Engineer for technical reasoning.

        Exactly-once semantics: if a durable reasoning record exists for
        the same organizational input digest, the recorded decisions are
        replayed instead of invoking the policy again."""
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

        input_digest = _decision_digest({
            "objectives": sorted(o.get("objective_id", "")
                                 for o in objectives),
            "projects": sorted(p.get("project_id", "")
                               for p in projects),
            "taskforce_digest": str(taskforce_state.get("member_count", 0))
                + ":" + str(taskforce_state.get("seat_count", 0)),
        })

        replayed = False
        if self.plan and hasattr(self.plan, "orchestration_for_digest"):
            existing = self.plan.orchestration_for_digest(input_digest)
            if existing is not None:
                decisions = json.loads(existing["decisions"])
                replayed = True
            else:
                decisions = self.policy.interpret(view)
                if decisions:
                    decisions = validate_orchestration_decisions(decisions)
        else:
            decisions = self.policy.interpret(view)
            if decisions:
                decisions = validate_orchestration_decisions(decisions)

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
            "decisions": decisions,
            "delegate_to_engineer": delegate,
            "policy_id": self.policy.policy_id,
            "input_digest": input_digest,
            "objectives_count": len(objectives),
            "projects_count": len(projects),
            "had_activity": had_activity,
            "replayed": replayed,
        }
        return dict(self._last_cycle)


class OrchestrationApplicator:
    """Apply validated orchestration decisions to durable organizational state.

    The Orchestrator produces typed decisions. This layer persists them.
    The Orchestrator itself holds no persistence handle.
    """

    def __init__(self, plan):
        self.plan = plan

    def apply(self, decisions, now):
        """Apply a batch of validated orchestration decisions.

        Returns a list of application results, one per decision."""
        results = []
        for decision in decisions:
            result = self._apply_one(decision, now)
            results.append(result)
        return results

    def _apply_one(self, decision, now):
        cls = decision["class"]
        decision_id = "orc_" + _decision_digest(decision)[:32]

        if cls == "CREATE_PROJECT":
            return self._apply_create_project(decision, decision_id, now)
        elif cls == "CREATE_WORKSTREAM":
            return self._apply_create_workstream(decision, decision_id, now)
        elif cls == "ADD_DEPENDENCY":
            return self._apply_add_dependency(decision, decision_id, now)
        elif cls in ("SET_WORK_PROFILE", "SET_RESOURCE_SCOPE",
                     "SET_EXECUTION_PROFILE", "SET_ROLE"):
            return {"decision_id": decision_id, "class": cls,
                    "status": "RECORDED", "note": "workstream update"}
        elif cls in ("COMPLETE_WORKSTREAM", "COMPLETE_PROJECT"):
            return {"decision_id": decision_id, "class": cls,
                    "status": "RECORDED", "note": "completion signal"}
        elif cls == "REQUEST_ENGINEERING_DECISION":
            return {"decision_id": decision_id, "class": cls,
                    "status": "RECORDED",
                    "question": decision.get("question")}
        elif cls == "REQUEST_CAPABILITY":
            return {"decision_id": decision_id, "class": cls,
                    "status": "RECORDED",
                    "capability": decision.get("capability")}
        elif cls == "REQUEST_REVIEW":
            return {"decision_id": decision_id, "class": cls,
                    "status": "RECORDED"}
        elif cls == "RESTRUCTURE_PROJECT":
            return {"decision_id": decision_id, "class": cls,
                    "status": "RECORDED",
                    "note": decision.get("rationale", "")}
        return {"decision_id": decision_id, "class": cls,
                "status": "UNKNOWN"}

    def _apply_create_project(self, decision, decision_id, now):
        objective_id = decision["objective_id"]
        title = decision["title"]
        solution = decision.get("solution", "")
        with self.plan.transaction() as cur:
            project, created = self.plan.open_project(
                cur, objective_id, title, solution,
                "ORCHESTRATOR", decision_id, now)
        return {"decision_id": decision_id, "class": "CREATE_PROJECT",
                "status": "CREATED" if created else "EXISTS",
                "project_id": project["project_id"]}

    def _apply_create_workstream(self, decision, decision_id, now):
        project_id = decision["project_id"]
        key = decision["workstream_key"]
        wp = decision.get("work_profile")
        ep = decision.get("execution_profile")
        rs = decision.get("resource_scope")
        role = decision.get("organizational_role")
        cp = decision.get("completion_policy")
        with self.plan.transaction() as cur:
            ws = self.plan.add_workstream(
                cur, project_id, key, decision_id, now,
                work_profile=wp, organizational_role=role,
                resource_scope=rs, execution_profile=ep,
                completion_policy=cp)
        return {"decision_id": decision_id, "class": "CREATE_WORKSTREAM",
                "status": "CREATED",
                "workstream_id": ws["workstream_id"]}

    def _apply_add_dependency(self, decision, decision_id, now):
        source = decision["source_workstream"]
        target = decision["target_workstream"]
        kind = decision["dependency_kind"]
        with self.plan.transaction() as cur:
            dep = self.plan.add_workstream_dependency(
                cur, source, target, kind)
        return {"decision_id": decision_id, "class": "ADD_DEPENDENCY",
                "status": "CREATED",
                "source": source, "target": target}
