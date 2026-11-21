"""The Autobot Coordinator: admission and elevation layer.

The Coordinator is the entry point for objectives. It decides how an
objective enters the organization:

    DIRECT              — bounded work routed to Luna Max / Taskforce
    ENGINEER_ELEVATED   — focused high-complexity technical design
                          routed to Opus Engineer
    ORCHESTRATOR_ELEVATED — complex multi-domain objective routed to
                            Opus Objective Orchestrator

The Coordinator NEVER:
    - solves technical problems (the Engineer does that)
    - decomposes into work units (the Orchestrator does that for
      elevated objectives; the Taskforce handles direct ones)
    - mutates authority (the Registry and Sequencer do that)
    - assigns specialists (the Taskforce does that)

Design:
    One CoordinatorDecision per objective admission.
    One OrchestrationRun per coordinated realization, even for DIRECT.
    The organizational identity model remains uniform.
"""

import hashlib
import json

from .proposals_util import canonical_json_digest

COORDINATOR_POLICY_ID = "coordinator-admission/v1"

ELEVATION_MODES = frozenset({
    "DIRECT",
    "ENGINEER_ELEVATED",
    "ORCHESTRATOR_ELEVATED",
})

COMPLEXITY_FEATURES = frozenset({
    "multi_domain",
    "high_work_count",
    "deep_dependencies",
    "high_parallelism",
    "large_context",
    "research_required",
    "engineering_design",
    "external_effects",
    "cross_artifact_synthesis",
    "high_uncertainty",
    "organizational_complexity",
})


class CoordinatorPolicy:
    """The admission reasoning seam.

    Implementations receive objective context and organizational state,
    return an elevation decision. They hold no persistence handles.
    """

    policy_id = COORDINATOR_POLICY_ID

    def evaluate(self, objective, context):
        """Evaluate an objective and return an elevation decision.

        Returns a dict with:
            elevation_mode: one of ELEVATION_MODES
            features: dict of complexity characteristics
            route: target route identifier
            reasoning: optional explanation
        """
        raise NotImplementedError


class DeterministicCoordinatorPolicy(CoordinatorPolicy):
    """Test policy returning pre-configured elevation decisions."""

    policy_id = "deterministic-coordinator/v1"

    def __init__(self, mode="DIRECT", features=None, route=None):
        self._mode = mode
        self._features = features or {}
        self._route = route

    def evaluate(self, objective, context):
        return {
            "elevation_mode": self._mode,
            "features": dict(self._features),
            "route": self._route,
        }


class PassthroughCoordinatorPolicy(CoordinatorPolicy):
    """Routes everything directly without elevation.

    The transitional policy: the Coordinator exists but doesn't
    elevate. All work flows through the existing Engineer path."""

    policy_id = "passthrough-coordinator/v1"

    def evaluate(self, objective, context):
        return {
            "elevation_mode": "DIRECT",
            "features": {},
            "route": None,
        }


class CoordinatorService:
    """The Coordinator's admission mechanics.

    For each objective:
    1. Build the admission context from organizational state
    2. Invoke the CoordinatorPolicy to evaluate complexity
    3. Record the durable CoordinatorDecision
    4. Create an OrchestrationRun in the appropriate mode
    5. For DIRECT, create a single OrchestrationNode
    6. For ENGINEER_ELEVATED, create an ENGINEER_REASONING node
    7. For ORCHESTRATOR_ELEVATED, hand off to the Orchestrator
    """

    def __init__(self, plan, policy, clock):
        self.plan = plan
        self.policy = policy
        self.clock = clock

    def admit_objective(self, objective_id, now=None):
        """Admit one objective into the organization.

        Returns the CoordinatorDecision and OrchestrationRun.
        Idempotent: an already-admitted objective returns its existing
        decision and run."""
        from autobot.models import instant
        if now is None:
            now = instant(self.clock.now())

        objective = self.plan.objective(objective_id)
        if objective is None:
            raise ValueError("objective %r not found" % objective_id)

        existing = self.plan.coordinator_decisions_for(objective_id)
        if existing:
            runs = self.plan.active_orchestration_runs(objective_id)
            return {
                "decision": existing[-1],
                "run": runs[0] if runs else None,
                "new": False,
            }

        context = self._build_context(objective)
        evaluation = self.policy.evaluate(objective, context)

        mode = evaluation["elevation_mode"]
        if mode not in ELEVATION_MODES:
            raise ValueError("invalid elevation mode %r" % mode)

        input_digest = self._compute_input_digest(objective, context)

        decision_id = "cd_" + canonical_json_digest({
            "objective": objective_id,
            "digest": input_digest,
        })[:32]

        with self.plan.transaction() as cur:
            self.plan.record_coordinator_decision(
                cur, decision_id, objective_id,
                self.policy.policy_id, input_digest,
                mode, evaluation.get("features"),
                evaluation.get("route"), now)

            run_id = "run_" + canonical_json_digest({
                "objective": objective_id,
                "decision": decision_id,
            })[:32]
            run = self.plan.create_orchestration_run(
                cur, run_id, objective_id, decision_id, mode,
                "coordinator", now)

        run = self.plan.orchestration_run(run_id)

        return {
            "decision": self.plan.coordinator_decision(decision_id),
            "run": run,
            "new": True,
            "mode": mode,
        }

    def create_initial_node(self, run, objective, now):
        """Create the initial OrchestrationNode for a run.

        DIRECT: one TASK_CONTRACT or ENGINEER_REASONING node.
        ENGINEER_ELEVATED: one ENGINEER_REASONING node.
        ORCHESTRATOR_ELEVATED: returns None (Orchestrator creates nodes).
        """
        mode = run["mode"]
        run_id = run["orchestration_id"]
        obj_text = objective.get("text", "")

        if mode == "ORCHESTRATOR_ELEVATED":
            return None

        if mode == "ENGINEER_ELEVATED":
            node_id = "nd_" + canonical_json_digest({
                "run": run_id, "key": "engineer-reasoning",
            })[:32]
            with self.plan.transaction() as cur:
                node = self.plan.create_orchestration_node(
                    cur, node_id, run_id, "engineer-reasoning", now,
                    title="Engineer reasoning for objective",
                    description=obj_text,
                    work_profile="ENGINEERING",
                    organizational_role="ENGINEER",
                    realization_kind="ENGINEER_REASONING",
                    capability="architecture",
                    execution_profile="opus-engineer/v1")
            return node

        node_id = "nd_" + canonical_json_digest({
            "run": run_id, "key": "direct-work",
        })[:32]
        with self.plan.transaction() as cur:
            node = self.plan.create_orchestration_node(
                cur, node_id, run_id, "direct-work", now,
                title="Direct execution",
                description=obj_text,
                realization_kind="TASK_CONTRACT")
        return node

    def _build_context(self, objective):
        """Build the admission context from organizational state."""
        active_runs = self.plan.active_orchestration_runs()
        active_projects = self.plan.active_projects()
        return {
            "active_runs": len(active_runs),
            "active_projects": len(active_projects),
            "objective_text": objective.get("text", ""),
            "constraints": objective.get("constraints"),
        }

    def _compute_input_digest(self, objective, context):
        data = {
            "objective_id": objective["objective_id"],
            "objective_text": objective.get("text", ""),
            "constraints": objective.get("constraints"),
            "active_runs": context.get("active_runs", 0),
        }
        canonical = json.dumps(data, sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(
            canonical.encode("utf-8")).hexdigest()
