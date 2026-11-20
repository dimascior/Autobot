"""Typed resource scope for Autobot assignments.

A ResourceScope declares which resources an assignment is authorized to
observe or affect. It is frozen into the contract and bound into the
assignment manifest at dispatch time.

ResourceScope is AUTHORIZATION — it declares what a specialist MAY
access. The ExecutionProfile is RUNTIME REACH — it declares what the
provider session CAN do. A connector observation that falls within
ResourceScope but outside ExecutionProfile reach is still a reach
violation.

Resource kinds model the actual resource types the system uses:
    REPOSITORY           — a Git repository path
    REPOSITORY_PATH      — specific paths within a repository
    FILESYSTEM_READ      — read-only filesystem roots
    ARTIFACT_WORKSPACE   — the specialist's controlled build workspace
    PERSISTENT_DEST      — persistent file placement targets
    WEB_SOURCE           — authorized web/API observation endpoints
    SERVICE              — service-level resource access
    DATABASE             — database resource access
    EFFECT_TARGET        — targets for external effects
"""

RESOURCE_KINDS = frozenset({
    "REPOSITORY",
    "REPOSITORY_PATH",
    "FILESYSTEM_READ",
    "ARTIFACT_WORKSPACE",
    "PERSISTENT_DEST",
    "WEB_SOURCE",
    "SERVICE",
    "DATABASE",
    "EFFECT_TARGET",
})

ACTIVITY_CLASSES = frozenset({
    "OBSERVATION",
    "ARTIFACT_PRODUCTION",
    "EXTERNAL_EFFECT",
})


def build_scope(entries):
    """Build a canonical ResourceScope from a list of scope entries.

    Each entry is a dict with:
        kind            — one of RESOURCE_KINDS
        identity        — resource identifier (path, URL, etc.)
        activity_class  — OBSERVATION, ARTIFACT_PRODUCTION, or EXTERNAL_EFFECT
        constraints     — optional dict of resource-specific constraints
    """
    validated = []
    for entry in entries:
        kind = entry.get("kind")
        if kind not in RESOURCE_KINDS:
            raise ValueError("unknown resource kind %r" % kind)
        activity = entry.get("activity_class")
        if activity not in ACTIVITY_CLASSES:
            raise ValueError("unknown activity class %r" % activity)
        validated.append({
            "kind": kind,
            "identity": str(entry["identity"]),
            "activity_class": activity,
            "constraints": dict(entry.get("constraints") or {}),
        })
    return sorted(validated,
                  key=lambda e: (e["kind"], e["identity"], e["activity_class"]))


def scope_for_engineering(repo_path, workspace_path):
    """The default ResourceScope for engineering work."""
    return build_scope([
        {"kind": "REPOSITORY", "identity": repo_path,
         "activity_class": "OBSERVATION"},
        {"kind": "REPOSITORY", "identity": repo_path,
         "activity_class": "ARTIFACT_PRODUCTION"},
        {"kind": "ARTIFACT_WORKSPACE", "identity": workspace_path,
         "activity_class": "ARTIFACT_PRODUCTION"},
    ])


def scope_for_research(repo_path, source_scopes=None):
    """The default ResourceScope for research work."""
    entries = [
        {"kind": "REPOSITORY", "identity": repo_path,
         "activity_class": "OBSERVATION"},
    ]
    for src in (source_scopes or []):
        entries.append({
            "kind": "WEB_SOURCE", "identity": src,
            "activity_class": "OBSERVATION"})
    return build_scope(entries)


def scope_for_operation(repo_path, workspace_path, destinations=None,
                        read_roots=None):
    """The default ResourceScope for general operations."""
    entries = [
        {"kind": "REPOSITORY", "identity": repo_path,
         "activity_class": "OBSERVATION"},
        {"kind": "ARTIFACT_WORKSPACE", "identity": workspace_path,
         "activity_class": "ARTIFACT_PRODUCTION"},
    ]
    for root in (read_roots or []):
        entries.append({
            "kind": "FILESYSTEM_READ", "identity": root,
            "activity_class": "OBSERVATION"})
    for dest in (destinations or []):
        entries.append({
            "kind": "PERSISTENT_DEST", "identity": dest,
            "activity_class": "EXTERNAL_EFFECT"})
    return build_scope(entries)


def validate_scope(scope):
    """Validate a ResourceScope list structure."""
    if not isinstance(scope, list):
        raise ValueError("ResourceScope must be a list")
    for entry in scope:
        if entry.get("kind") not in RESOURCE_KINDS:
            raise ValueError("unknown resource kind %r" % entry.get("kind"))
        if entry.get("activity_class") not in ACTIVITY_CLASSES:
            raise ValueError("unknown activity class %r"
                             % entry.get("activity_class"))


def scope_contains(scope, kind, identity, activity_class):
    """Check if the scope authorizes a specific resource access."""
    for entry in scope:
        if (entry["kind"] == kind
                and entry["identity"] == identity
                and entry["activity_class"] == activity_class):
            return True
    return False
