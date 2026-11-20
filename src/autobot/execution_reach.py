"""Registered execution-reach policies for obj-13 evidence evaluation.

Each policy is identified by a versioned string (e.g. ``luna-local/v1``)
and declares which execution-reach classes the policy authorizes. The
Collector binds the policy identity into the ExecutionReceipt so the
exact rules under which a session was evaluated are always traceable.

Reach classes are properties of the item type, not the item's content:
    MACHINE_LOCAL        — shell commands, file writes on the assignment machine
    PROVIDER_CONNECTOR   — provider-owned MCP/connector tool calls
    PROVIDER_SUBAGENT    — provider-delegated sub-agent sessions
    MODEL_INTERNAL       — reasoning, telemetry, lifecycle (no external reach)

A policy authorizes a set of reach classes. Any execution-relevant item
whose reach class is NOT in the authorized set is a violation.
"""

EXECUTION_REACH_POLICIES = {
    "luna-local/v1": {
        "description": "Luna local-only engineering: machine commands and file "
                       "activity. No provider connector or sub-agent reach.",
        "authorized_reach": frozenset({"MACHINE_LOCAL"}),
        "provider": "codex",
        "model_family": "luna",
    },
    "luna-research/v1": {
        "description": "Luna research: machine-local plus provider connectors "
                       "for authorized external observations.",
        "authorized_reach": frozenset({"MACHINE_LOCAL", "PROVIDER_CONNECTOR"}),
        "provider": "codex",
        "model_family": "luna",
    },
    "luna-resource/v1": {
        "description": "Luna resource operations: machine-local plus provider "
                       "connectors for controlled resource access.",
        "authorized_reach": frozenset({"MACHINE_LOCAL", "PROVIDER_CONNECTOR"}),
        "provider": "codex",
        "model_family": "luna",
    },
    "opus-engineer/v1": {
        "description": "Opus engineer: machine-local plus provider connectors "
                       "and sub-agents for high-context engineering.",
        "authorized_reach": frozenset({
            "MACHINE_LOCAL", "PROVIDER_CONNECTOR", "PROVIDER_SUBAGENT"}),
        "provider": "anthropic",
        "model_family": "opus",
    },
    "opus-orchestrator/v1": {
        "description": "Opus orchestrator: model-internal reasoning only. "
                       "No direct machine or provider execution.",
        "authorized_reach": frozenset(),
        "provider": "anthropic",
        "model_family": "opus",
    },
    "opus-high-context/v1": {
        "description": "Opus high-context escalation: machine-local plus "
                       "provider connectors for elevated reasoning tasks.",
        "authorized_reach": frozenset({
            "MACHINE_LOCAL", "PROVIDER_CONNECTOR"}),
        "provider": "anthropic",
        "model_family": "opus",
    },
}

DEFAULT_LUNA_POLICY = "luna-local/v1"


def get_policy(policy_id):
    """Return the registered policy dict, or None if unknown."""
    return EXECUTION_REACH_POLICIES.get(policy_id)


def authorized_reach(policy_id):
    """Return the authorized reach set for a policy, or empty frozenset."""
    policy = get_policy(policy_id)
    if policy is None:
        return frozenset()
    return policy["authorized_reach"]


def policy_ids():
    """All registered policy identifiers."""
    return sorted(EXECUTION_REACH_POLICIES.keys())
