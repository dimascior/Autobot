"""The production Objective Orchestrator policy.

``OpusOrchestratorPolicy`` reasons with Claude Opus 4.6 [1M] through
the existing ClaudeCliReasoner to decompose complex objectives into
OrchestrationNode DAGs. It holds no authority handle and returns only
validated typed decisions that the OrchestrationApplicator persists.

Responsibilities:
    - interpret complex objective into OrchestrationNodes
    - choose WorkProfiles, ResourceScopes, ExecutionProfiles
    - choose realization kinds (TASK_CONTRACT, ENGINEER_REASONING, CONTROL)
    - construct dependency edges
    - identify parallelism
    - request reviews
    - create Project bindings when organizationally useful
    - consume accepted outputs and react to findings
    - determine completion
"""

import hashlib
import json

from .orchestrator import (
    OrchestratorPolicy,
    validate_orchestration_decisions,
    ORCHESTRATOR_DECISION_CLASSES,
    REALIZATION_KINDS,
)

OPUS_ORCHESTRATOR_POLICY_ID = "opus-orchestrator/v1"

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are the Autobot Objective Orchestrator. Your role is to decompose
complex objectives into concrete work nodes that specialists can execute.

You receive the current organizational state as a JSON planning view
and return a JSON array of typed orchestration decisions.

RULES:
1. Each decision has a "class" field from the allowed decision classes.
2. CREATE_NODE requires: node_key, work_profile, realization_kind.
3. ADD_EDGE requires: source_node, target_node, dependency_kind.
4. Node keys must be short kebab-case identifiers.
5. Work profiles: RESEARCH, ENGINEERING, GENERAL_OPERATION.
6. Realization kinds: TASK_CONTRACT (specialist executes), ENGINEER_REASONING (Opus solves), CONTROL (organizational).
7. Dependency kinds: ACCEPTED_ARTIFACT, ACCEPTED_DECISION, REVIEW_OF, COMPLETION.
8. Never name a provider, model, specialist, or route.
9. Identify what can run in parallel (no dependencies between independent work).
10. Create Project bindings only when durable coordinated structure adds value.

Respond with ONLY a JSON array of decisions. No explanation, no markdown.
"""


def _render_orchestrator_view(view):
    """Render the OrchestratorPlanningView into a structured prompt."""
    data = view.to_dict()
    objectives = data.get("objectives", [])
    runs = data.get("runs", [])
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    prompt_parts = ["# Organizational State\n"]

    prompt_parts.append("## Objectives\n")
    for obj in objectives:
        prompt_parts.append("- %s: %s (status: %s)" % (
            obj.get("objective_id", "?"),
            obj.get("text", "?"),
            obj.get("status", "?")))

    if runs:
        prompt_parts.append("\n## Active Runs\n")
        for run in runs:
            prompt_parts.append("- %s: mode=%s status=%s" % (
                run.get("orchestration_id", "?"),
                run.get("mode", "?"),
                run.get("status", "?")))

    if nodes:
        prompt_parts.append("\n## Existing Nodes\n")
        for nd in nodes:
            prompt_parts.append(
                "- %s (key=%s, profile=%s, kind=%s, status=%s)" % (
                    nd.get("node_id", "?"),
                    nd.get("node_key", "?"),
                    nd.get("work_profile", "?"),
                    nd.get("realization_kind", "?"),
                    nd.get("status", "?")))

    if edges:
        prompt_parts.append("\n## Edges\n")
        for e in edges:
            prompt_parts.append("- %s → %s (%s, status=%s)" % (
                e.get("source_node_id", "?"),
                e.get("target_node_id", "?"),
                e.get("dependency_kind", "?"),
                e.get("status", "?")))

    outputs = data.get("node_outputs", [])
    if outputs:
        prompt_parts.append("\n## Accepted Outputs\n")
        for out in outputs:
            prompt_parts.append("- node=%s kind=%s sha=%s" % (
                out.get("node_id", "?"),
                out.get("output_kind", "?"),
                (out.get("content_sha256") or "?")[:16]))

    findings = data.get("findings", [])
    if findings:
        prompt_parts.append("\n## Findings\n")
        for f in findings:
            if isinstance(f, dict):
                prompt_parts.append("- %s: %s (%s)" % (
                    f.get("finding_id", "?"),
                    f.get("finding_class", "?"),
                    f.get("significance", "?")))

    taskforce = data.get("taskforce", {})
    if taskforce:
        members = taskforce.get("members", [])
        seats = taskforce.get("seats", [])
        if isinstance(members, list) and members:
            prompt_parts.append("\n## Taskforce\n")
            prompt_parts.append("Members: %d" % len(members))
            if isinstance(seats, list):
                caps = set()
                for s in seats:
                    if isinstance(s, dict):
                        caps.add(s.get("capability", "?"))
                if caps:
                    prompt_parts.append("Capabilities: %s" %
                                        ", ".join(sorted(caps)))

    prompt_parts.append(
        "\n## Decision Classes\n"
        "CREATE_NODE, SET_NODE_WORK_PROFILE, SET_NODE_RESOURCE_SCOPE, "
        "SET_NODE_EXECUTION_PROFILE, SET_NODE_ROLE, "
        "SET_NODE_REALIZATION_KIND, ADD_EDGE, CREATE_PROJECT, "
        "BIND_NODE_TO_PROJECT, COMPLETE_NODE, COMPLETE_RUN, "
        "RESTRUCTURE_RUN, REQUEST_ENGINEERING_DECISION, "
        "REQUEST_CAPABILITY, REQUEST_REVIEW\n")

    prompt_parts.append(
        "\nReturn a JSON array of decisions. "
        "If nothing needs to change, return [].")

    return "\n".join(prompt_parts)


def _parse_decisions(text):
    """Parse the model's response into a list of decision dicts.

    Handles: bare JSON array, JSON wrapped in markdown code fences,
    or text containing a JSON array."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        in_block = False
        json_lines = []
        for line in lines:
            if line.strip().startswith("```") and not in_block:
                in_block = True
                continue
            if line.strip() == "```" and in_block:
                break
            if in_block:
                json_lines.append(line)
        text = "\n".join(json_lines).strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "decisions" in result:
            return result["decisions"]
        return []
    except (json.JSONDecodeError, ValueError):
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
        return []


class OpusOrchestratorPolicy(OrchestratorPolicy):
    """Production Orchestrator policy backed by Opus 4.6 [1M].

    Uses the established ClaudeCliReasoner pattern. Renders the
    OrchestratorPlanningView into a structured prompt, invokes Opus,
    parses structured decision output, validates each decision.
    """

    policy_id = OPUS_ORCHESTRATOR_POLICY_ID
    lenient_validation = True

    def __init__(self, reasoner=None, model=None):
        self._reasoner = reasoner
        self._model = model
        self._last_prompt = None
        self._last_response = None

    @property
    def reasoner(self):
        if self._reasoner is not None:
            return self._reasoner
        if self._model:
            from .policy import ClaudeCliReasoner
            self._reasoner = ClaudeCliReasoner(self._model)
        return self._reasoner

    def interpret(self, view):
        """Invoke Opus to interpret the organizational state and
        return typed orchestration decisions."""
        prompt = _render_orchestrator_view(view)
        self._last_prompt = prompt

        reasoner = self.reasoner
        if reasoner is None:
            return []

        try:
            response = reasoner(prompt)
            self._last_response = response
        except Exception:
            self._last_response = None
            return []

        raw_decisions = _parse_decisions(response)
        if not raw_decisions:
            return []

        valid = []
        for d in raw_decisions:
            if not isinstance(d, dict):
                continue
            cls = d.get("class")
            if cls not in ORCHESTRATOR_DECISION_CLASSES:
                continue
            valid.append(d)

        try:
            validated = validate_orchestration_decisions(valid)
        except Exception:
            return valid

        return validated

    def should_delegate_to_engineer(self, view, project):
        """The Opus Orchestrator explicitly delegates to the Engineer
        through REQUEST_ENGINEERING_DECISION decisions, not through
        this method."""
        return False
