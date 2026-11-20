"""Work profiles: the semantic classification of an Autobot workstream.

A WorkProfile defines WHAT KIND of work the assignment is — engineering,
research, or general operation — and determines which evidence profile,
execution profile, and resource scope defaults apply.

The profile is frozen into the TaskContractRecord at compilation time and
bound into the assignment manifest; it cannot change after dispatch.

WorkProfile is distinct from:
    finalization_class  — whether the work modifies source (evidence tier)
    ExecutionProfile    — runtime reach granted to the provider session
    ResourceScope       — which resources the assignment may observe/affect
    Route               — which provider/model executes the work
"""

WORK_PROFILES = {
    "ENGINEERING": {
        "description": "Engineering work: implementation, investigation, "
                       "architecture, debugging, testing.",
        "default_finalization_class": "SOURCE_MODIFYING",
        "default_execution_profile": "luna-local/v1",
        "default_activity_classes": frozenset({
            "OBSERVATION", "ARTIFACT_PRODUCTION"}),
        "supports_external_effect": True,
    },
    "RESEARCH": {
        "description": "Research work: investigation, source retrieval, "
                       "analysis, citation, artifact production.",
        "default_finalization_class": "READ_ONLY",
        "default_execution_profile": "luna-research/v1",
        "default_activity_classes": frozenset({
            "OBSERVATION", "ARTIFACT_PRODUCTION"}),
        "supports_external_effect": False,
    },
    "GENERAL_OPERATION": {
        "description": "General operations: file work, report generation, "
                       "dataset transformation, structured resource ops.",
        "default_finalization_class": "SOURCE_MODIFYING",
        "default_execution_profile": "luna-resource/v1",
        "default_activity_classes": frozenset({
            "OBSERVATION", "ARTIFACT_PRODUCTION", "EXTERNAL_EFFECT"}),
        "supports_external_effect": True,
    },
}

VALID_PROFILES = frozenset(WORK_PROFILES.keys())


def get_profile(name):
    """Return the work profile dict, or None if unknown."""
    return WORK_PROFILES.get(name)


def default_execution_profile(work_profile_name):
    """The default ExecutionProfile for a WorkProfile."""
    profile = get_profile(work_profile_name)
    if profile is None:
        return None
    return profile["default_execution_profile"]


def default_finalization_class(work_profile_name):
    """The default finalization class for a WorkProfile."""
    profile = get_profile(work_profile_name)
    if profile is None:
        return None
    return profile["default_finalization_class"]


def validate_profile(name):
    """Raise ValueError if the profile name is not registered."""
    if name not in VALID_PROFILES:
        raise ValueError(
            "unknown work profile %r; valid: %s"
            % (name, ", ".join(sorted(VALID_PROFILES))))
