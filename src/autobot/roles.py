"""Organizational roles in the Autobot authority system.

A role is an organizational responsibility — it determines WHAT a
participant does in the system, not how it is executed. Roles are
distinct from:
    WorkProfile         — semantic type of work
    ExecutionProfile    — runtime reach
    ResourceScope       — resource authorization
    Route               — provider/model implementation

The role hierarchy:
    ORCHESTRATOR        — organizes objectives and project graphs
    ENGINEER            — solves engineering problems, designs architecture
    PROJECT_MANAGER     — manages engineering realization (Engineer acting as PM)
    SPECIALIST          — performs assignment work
    REVIEWER            — independent verification
"""

ROLES = {
    "ORCHESTRATOR": {
        "description": "Organizes the objective, constructs the project "
                       "work graph, identifies work domains, invokes "
                       "Engineer for technical reasoning.",
        "default_model": "claude-opus-4-6[1m]",
        "default_execution_profile": "opus-orchestrator/v1",
        "can_reason": True,
        "can_execute": False,
    },
    "ENGINEER": {
        "description": "Solves engineering problems: root-cause analysis, "
                       "architecture, tradeoffs, solution design, "
                       "implementation planning, verification strategy.",
        "default_model": "claude-opus-4-6[1m]",
        "default_execution_profile": "opus-engineer/v1",
        "can_reason": True,
        "can_execute": True,
    },
    "PROJECT_MANAGER": {
        "description": "Manages engineering realization. Engineer acting "
                       "as PM for a project's engineering work.",
        "default_model": "claude-opus-4-6[1m]",
        "default_execution_profile": "opus-orchestrator/v1",
        "can_reason": True,
        "can_execute": False,
    },
    "SPECIALIST": {
        "description": "Performs assignment work: implementation, research, "
                       "operations. Receives frozen prompts with "
                       "specification inputs.",
        "default_model": "gpt-6-luna",
        "default_execution_profile": "luna-local/v1",
        "can_reason": False,
        "can_execute": True,
    },
    "REVIEWER": {
        "description": "Independent verification. Separate specialist "
                       "with separate assignment, attempt, call, session "
                       "and evidence chain.",
        "default_model": "gpt-6-luna",
        "default_execution_profile": "luna-local/v1",
        "can_reason": False,
        "can_execute": True,
    },
}

VALID_ROLES = frozenset(ROLES.keys())


def get_role(name):
    """Return the role definition dict, or None if unknown."""
    return ROLES.get(name)


def default_execution_profile_for_role(role_name):
    """The default ExecutionProfile for a role."""
    role = get_role(role_name)
    if role is None:
        return None
    return role["default_execution_profile"]


def validate_role(name):
    """Raise ValueError if the role name is not registered."""
    if name not in VALID_ROLES:
        raise ValueError(
            "unknown role %r; valid: %s"
            % (name, ", ".join(sorted(VALID_ROLES))))
