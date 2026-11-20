"""Registered effect families for the generalized authority system.

Each effect family defines a type of external mutation the system can
perform under authority. The Git effect (git_executor.py) is the first
and production-proven implementation. This registry generalizes the
pattern to other effect types.

An effect adapter supplies:
    typed target         — where the effect lands
    expected prior state — what should be there now
    requested new state  — what it should become
    permit requirements  — what authority is needed
    EffectIntent         — the frozen intent (obj-25)
    executor identity    — who performs the effect
    producer observation — what actually happened
    outcome classification — HAPPENED / NOT_HAPPENED / INDETERMINATE
    receipt              — the evidence of what occurred

Effect families:
    GIT_CAS              — Git ref compare-and-swap (production-proven)
    FILESYSTEM_PLACE     — Persistent file placement
    WEB_MUTATION         — External web/API mutation
    MESSAGE_DELIVERY     — Message/notification delivery
    DATABASE_MUTATION    — Database record mutation
    SERVICE_OPERATION    — External service operation
"""

EFFECT_FAMILIES = {
    "GIT_CAS": {
        "description": "Git ref compare-and-swap: the local canonical "
                       "commit effect. Production-proven since PR 20.",
        "effect_id": "eff-local-canonical-cas",
        "executor": "git_sequencer",
        "receipt_kind": "CommitReceipt",
        "reversible": False,
        "requires_permit": True,
        "observation_rule": "local",
        "production_status": "PROVEN",
    },
    "FILESYSTEM_PLACE": {
        "description": "Persistent file placement: content-addressed "
                       "artifact placed at a declared destination.",
        "effect_id": "eff-filesystem-place",
        "executor": "filesystem_placer",
        "receipt_kind": "FilesystemPlacementReceipt",
        "reversible": True,
        "requires_permit": True,
        "observation_rule": "local",
        "production_status": "IMPLEMENTED",
    },
    "WEB_MUTATION": {
        "description": "External web/API mutation.",
        "effect_id": "eff-web-mutation",
        "executor": "web_executor",
        "receipt_kind": "WebMutationReceipt",
        "reversible": False,
        "requires_permit": True,
        "observation_rule": "remote",
        "production_status": "DEFERRED",
    },
    "MESSAGE_DELIVERY": {
        "description": "Message or notification delivery.",
        "effect_id": "eff-message-delivery",
        "executor": "message_executor",
        "receipt_kind": "MessageReceipt",
        "reversible": False,
        "requires_permit": True,
        "observation_rule": "remote",
        "production_status": "DEFERRED",
    },
    "DATABASE_MUTATION": {
        "description": "Database record mutation.",
        "effect_id": "eff-database-mutation",
        "executor": "database_executor",
        "receipt_kind": "DatabaseMutationReceipt",
        "reversible": True,
        "requires_permit": True,
        "observation_rule": "local",
        "production_status": "DEFERRED",
    },
    "SERVICE_OPERATION": {
        "description": "External service operation.",
        "effect_id": "eff-service-operation",
        "executor": "service_executor",
        "receipt_kind": "ServiceOperationReceipt",
        "reversible": False,
        "requires_permit": True,
        "observation_rule": "remote",
        "production_status": "DEFERRED",
    },
}

IMPLEMENTED_FAMILIES = frozenset(
    name for name, fam in EFFECT_FAMILIES.items()
    if fam["production_status"] in ("PROVEN", "IMPLEMENTED"))

DEFERRED_FAMILIES = frozenset(
    name for name, fam in EFFECT_FAMILIES.items()
    if fam["production_status"] == "DEFERRED")


def get_family(name):
    """Return the effect family definition, or None."""
    return EFFECT_FAMILIES.get(name)


def family_names():
    """All registered effect family names."""
    return sorted(EFFECT_FAMILIES.keys())


def is_implemented(name):
    """Whether the effect family has an implementation."""
    return name in IMPLEMENTED_FAMILIES
