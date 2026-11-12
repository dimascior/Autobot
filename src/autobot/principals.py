"""The registered principals, pinned to Chapter 0 (D-21 attribution).

Mirrors ``docs/architecture/chapter0/registries/principals.json`` exactly —
id to kind — and a test cross-checks this table against the registry file,
so the runtime cannot drift from Chapter 0 (the same pinning discipline as
``models.py`` states and ``effects.py``). Witness producers (D-21) and
attributed engineering context (M3 PromptProjection) validate against it.
"""

# id -> kind, in registry order.
PRINCIPALS = {
    "you": "human",
    "registry": "autobot_seat",
    "planner": "autobot_seat",
    "agent_factory": "autobot_seat",
    "sequencer": "autobot_seat",
    "dispatcher": "autobot_seat",
    "launcher": "autobot_seat",
    "collector": "autobot_seat",
    "git_adapter": "autobot_seat",
    "environment_probe": "autobot_seat",
    "verifier_runner": "autobot_seat",
    "git_sequencer": "autobot_seat",
    "recovery": "autobot_seat",
    "authority_cli": "autobot_seat",
    "autobot_service": "autobot_seat",
    "autobot_code": "autobot_seat",
    "migration": "autobot_seat",
    "autoswarm": "external_program",
    "helios": "external_program",
    "akashic": "external_program",
    "hdb": "external_program",
    "git": "external_program",
    "ci_system": "external_program",
    "agent_clis": "external_program",
    "registered_verifiers": "external_program",
    "task_force": "agent",
}
