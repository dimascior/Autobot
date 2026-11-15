"""The launch gate: build manifest, place in CAS, hand to transport.

This replaces the bare transport.launch(projection, provider, role, call_id)
call in the Taskforce. The manifest becomes immutable once canonicalized,
and its content address is the cross-process identity.
"""

from .manifest import build_execution_manifest


def launch_with_manifest(sequencer, transport, attempt_id,
                         factory_assignment, capability,
                         parent_call_id=None):
    """Build the manifest from durable state and launch through it.

    Returns ``{"call_id": str, "manifest_address": str,
               "native_session_id": str}``."""
    manifest_bytes, manifest_address = build_execution_manifest(
        sequencer, attempt_id, factory_assignment, capability,
        parent_call_id=parent_call_id)
    import json
    manifest = json.loads(manifest_bytes)
    native = transport.launch_manifest(
        manifest_address, manifest, manifest_bytes=manifest_bytes)
    return {"call_id": manifest["call_id"],
            "manifest_address": manifest_address,
            "native_session_id": native}
