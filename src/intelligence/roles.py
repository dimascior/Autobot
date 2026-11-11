"""The Agent Factory: seat, route and model selection (cap-14).

Two operations stay distinct, because their owners are distinct frozen
principals:

- The Chief Engineer / Taskforce determine WHAT is needed — "this
  sequence needs a software-implementation specialist in the builder
  role". That is engineering reasoning (cap-22 side), and it is the
  whole request: capability and role, never a provider or model.
- The AGENT FACTORY determines WHICH seat satisfies the request —
  provider, model, context budget, tool requirements, expected output.
  That is cap-14 ("Seat, route and model selection", owner
  agent_factory, consumer dispatcher, forbidden task_force). The
  dispatcher then consumes the assignment for execution (t-a02).

A frozen TaskContractRecord names only WHAT must hold; it never names a
provider or model, so the engineering organization can change providers
without changing authority identity. Every assignment this factory
returns is stamped ``selected_by: "agent_factory"`` and the seat row
persists it, so the ledger-side record of who routed the seat is
explicit.

``CAPABILITIES`` is the engineering capability vocabulary the Chief
Engineer and the Taskforce share. It is intelligence-plane vocabulary —
NOT a Chapter 1 enum: the protocol-side ``capability_requirements`` field
carries registered cap-NN ids and stays whatever the frozen record says.
"""

from .proposals_util import canonical_json_digest

# The registered principal this component embodies (principals.json).
FACTORY_PRINCIPAL = "agent_factory"

CAPABILITIES = (
    "architecture",
    "database_design",
    "debugging",
    "documentation",
    "domain_engineering",
    "git_repository_operations",
    "research",
    "security_review",
    "software_implementation",
    "verification",
)


class AgentFactoryError(Exception):
    pass


class AgentFactory:
    """Registered specialist seats: capability -> ordered candidates.

    Selection is deterministic (registration order) so allocation is
    reproducible; availability is a runtime predicate the caller may
    narrow with ``providers``. Callers ask for WHAT they need; this
    factory alone answers WHICH seat serves it (cap-14)."""

    principal = FACTORY_PRINCIPAL

    def __init__(self):
        self._rows = []

    def register(self, capability, role, provider, model=None,
                 tool_requirements=(), context_budget=None,
                 expected_output="artifacts", completion_policy=None):
        if capability not in CAPABILITIES:
            raise AgentFactoryError(
                "capability %r is not in the registered vocabulary"
                % (capability,))
        if not (isinstance(role, str) and role):
            raise AgentFactoryError("role must be a non-empty string")
        if not (isinstance(provider, str) and provider):
            raise AgentFactoryError("provider must be a non-empty string")
        row = {
            "capability": capability,
            "role": role,
            "provider": provider,
            "model": model,
            "tool_requirements": list(tool_requirements),
            "context_budget": context_budget,
            "expected_output": expected_output,
            "completion_policy": completion_policy or
                {"required": True, "success_outcomes": ["success"]},
        }
        self._rows.append(row)
        return row

    def select(self, capability, role=None, providers=None):
        """One seat request -> one ASSIGNMENT. The request is the
        engineering ask (capability, optionally the role; ``providers``
        is a runtime availability narrowing, not a caller choice of
        identity); the assignment is this factory's cap-14 decision:
        capability, provider, model, role, context budget, tool
        requirements, expected output and completion policy — none of
        which is contract identity — stamped with the selecting
        principal."""
        for row in self._rows:
            if row["capability"] != capability:
                continue
            if role is not None and row["role"] != role:
                continue
            if providers is not None and row["provider"] not in providers:
                continue
            assignment = dict(row)
            assignment["selected_by"] = FACTORY_PRINCIPAL
            assignment["assignment_id"] = "seat_" + canonical_json_digest({
                "capability": row["capability"], "role": row["role"],
                "provider": row["provider"], "model": row["model"]})[:32]
            return assignment
        raise AgentFactoryError(
            "no registered seat satisfies capability=%r role=%r"
            " providers=%r" % (capability, role, providers))
