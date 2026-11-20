"""PlanningView: the structured state surface for reasoning roles.

The Orchestrator and Engineer consume a curated PlanningView, never raw
database handles. The view projects authority-accepted state into the
information a reasoning role needs to make decisions.

The Orchestrator sees the full project graph. The Engineer sees the
engineering-relevant subset. Neither sees or touches authority internals.
"""


def build_orchestrator_view(objective, projects, taskforce_state,
                            accepted_artifacts=None, findings=None,
                            failed_attempts=None, effect_outcomes=None):
    """Build the Orchestrator's PlanningView.

    The Orchestrator uses this to determine what work must exist,
    how it decomposes, which domain owns each workstream, dependencies,
    required capabilities, and completion conditions.
    """
    return {
        "role": "ORCHESTRATOR",
        "objective": dict(objective),
        "projects": list(projects),
        "taskforce": dict(taskforce_state),
        "accepted_artifacts": list(accepted_artifacts or []),
        "findings": list(findings or []),
        "failed_attempts": list(failed_attempts or []),
        "effect_outcomes": list(effect_outcomes or []),
    }


def build_engineer_view(objective, project, workstreams,
                        accepted_artifacts=None, findings=None,
                        failed_attempts=None):
    """Build the Engineer's PlanningView.

    The Engineer uses this to solve technical problems, design
    architecture, and plan implementation. It sees the engineering-
    relevant subset of the Orchestrator's view.
    """
    return {
        "role": "ENGINEER",
        "objective": dict(objective),
        "project": dict(project),
        "workstreams": list(workstreams),
        "accepted_artifacts": list(accepted_artifacts or []),
        "findings": list(findings or []),
        "failed_attempts": list(failed_attempts or []),
    }


def enrich_view(view, work_profiles=None, resource_scopes=None,
                execution_profiles=None, dependency_state=None):
    """Enrich a PlanningView with generalized dimension state."""
    enriched = dict(view)
    if work_profiles is not None:
        enriched["work_profiles"] = dict(work_profiles)
    if resource_scopes is not None:
        enriched["resource_scopes"] = dict(resource_scopes)
    if execution_profiles is not None:
        enriched["execution_profiles"] = dict(execution_profiles)
    if dependency_state is not None:
        enriched["dependency_state"] = dict(dependency_state)
    return enriched
