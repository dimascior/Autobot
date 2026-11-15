"""The execution boundary: the manifest that crosses from Autobot to AutoSwarm.

One schema, one builder, one gate. The manifest carries already-frozen
authority facts to the concrete execution. It grants nothing and
transitions nothing — the authority chain (TaskContractRecord → Sequence →
Attempt → PromptProjection → t-a02 call_id) is complete before the
manifest exists. AutoSwarm verifies and claims; Autobot never does.
"""

from .manifest import build_execution_manifest, manifest_schema
from .launch_gate import launch_with_manifest
