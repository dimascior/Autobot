"""The effects vocabulary, mirrored from Chapter 0's transitions.json and
pinned to it by tests/autobot/test_effects_registry.py — the runtime cannot
drift from the frozen registry.

Six registered effects (every one requires_intent), four outcome rules
(observation → HAPPENED | ABSENT | UNKNOWN_OUTCOME), the architectural
retryable split (INV-73), and the attempt_scoped intent-class checks
(INV-47 as amended by CORR-55/CORR-56). The standalone classes
(staging_delete, workspace_sync) mint through their own machines
(t-g01/t-w01), which land with the Chapter 6-adjacent milestones.
"""

EFFECTS = {
    "eff-local-canonical-cas": {
        "type": "LOCAL_REF_CAS", "outcome_rule": "local", "receipt": "obj-26",
        "intent_class": "attempt_scoped", "standalone": False,
        "canonical_on": ["SL"],
    },
    "eff-local-sequence-ref": {
        "type": "LOCAL_REF_CAS", "outcome_rule": "local", "receipt": "obj-26",
        "intent_class": "attempt_scoped", "standalone": False,
        "canonical_on": [],
    },
    "eff-staging-push": {
        "type": "STAGING_PUSH", "outcome_rule": "remote_create", "receipt": "obj-27",
        "intent_class": "attempt_scoped", "standalone": False,
        "canonical_on": [],
    },
    "eff-canonical-push": {
        "type": "CANONICAL_PUSH", "outcome_rule": "remote_cas", "receipt": "obj-28",
        "intent_class": "attempt_scoped", "standalone": False,
        "canonical_on": ["SRD", "SRP", "SRPO"],
    },
    "eff-staging-delete": {
        "type": "STAGING_REF_DELETE", "outcome_rule": "remote_delete", "receipt": "obj-41",
        "intent_class": "staging_delete", "standalone": True,
        "canonical_on": [],
    },
    "eff-workspace-sync": {
        "type": "LOCAL_REF_CAS", "outcome_rule": "local", "receipt": "obj-29",
        "intent_class": "workspace_sync", "standalone": True,
        "canonical_on": [],
    },
}

# observation -> outcome, exactly as the registry's outcome_rules state them
# (INV-62 for the remote rules; the "note" prose stays in the registry).
OUTCOME_RULES = {
    "local": {
        "target_equals_new": "HAPPENED",
        "target_equals_expected_old_and_executor_fenced_and_no_ref_lock": "ABSENT",
        "target_equals_expected_old_otherwise": "UNKNOWN_OUTCOME",
        "target_other": "ABSENT",
    },
    "remote_cas": {
        "target_equals_new": "HAPPENED",
        "target_equals_expected_old": "UNKNOWN_OUTCOME",
        "target_other": "ABSENT",
        "unobservable": "UNKNOWN_OUTCOME",
    },
    "remote_create": {
        "target_equals_new": "HAPPENED",
        "target_absent": "UNKNOWN_OUTCOME",
        "target_other": "ABSENT",
        "unobservable": "UNKNOWN_OUTCOME",
    },
    "remote_delete": {
        "target_absent": "HAPPENED",
        "target_equals_expected_old": "UNKNOWN_OUTCOME",
        "target_other": "ABSENT",
        "unobservable": "UNKNOWN_OUTCOME",
    },
}

# INV-73: the exclusion is architectural, not an emergent check failure.
RETRYABLE = frozenset((
    "eff-local-canonical-cas", "eff-local-sequence-ref",
    "eff-staging-push", "eff-canonical-push",
))
NOT_RETRYABLE = frozenset(("eff-staging-delete", "eff-workspace-sync"))

# intent_classes.classes.attempt_scoped.checks, verbatim (groups of
# alternatives; one member of each group must hold).
ATTEMPT_SCOPED_CHECKS = [
    ["delegation_active_at_intent"],
    ["permit_reserved", "reservation_held"],
    ["fence_matches"],
    ["effect_intent_committed"],
    ["intent_ttl_gt_hard_timeout"],
]
