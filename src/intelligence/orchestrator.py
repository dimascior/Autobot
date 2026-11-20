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

from autobot.planning_view import build_orchestrator_view, enrich_view
from autobot.work_profile import VALID_PROFILES


ORCHESTRATOR_POLICY_ID = "reasoning-orchestrator/v1"

ORCHESTRATOR_ROLE = "ORCHESTRATOR"


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
        """
        if not objectives:
            return {
                "orchestrator_cycle": True,
                "decisions": [],
                "delegate_to_engineer": [],
                "policy_id": self.policy.policy_id,
            }

        view = self.build_view(objectives, projects, taskforce_state,
                               **kwargs)
        decisions = self.policy.interpret(view)
        delegate = []
        for project in projects:
            if self.policy.should_delegate_to_engineer(view, project):
                delegate.append(project.get("project_id")
                                or project.get("title", "unnamed"))

        self._last_cycle = {
            "orchestrator_cycle": True,
            "decisions": decisions,
            "delegate_to_engineer": delegate,
            "policy_id": self.policy.policy_id,
            "objectives_count": len(objectives),
            "projects_count": len(projects),
        }
        return dict(self._last_cycle)
