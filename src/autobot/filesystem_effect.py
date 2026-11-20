"""Filesystem effect: authoritative persistent file placement.

Places a content-addressed artifact at a declared destination under
authority. The placement is independently verifiable: the destination
file's content address must match the source artifact's.

This is distinct from specialist workspace mutation: a specialist may
write any file in its worktree during execution. A FilesystemEffect
is a controlled, authorized, permanent placement of a specific artifact
at a specific location, recorded in the authority chain.

The placement protocol:
    1. EffectIntent minted with source address, destination, expected prior
    2. Permit granted by the Sequencer
    3. Executor places the bytes
    4. Independent observation: destination content address matches source
    5. Receipt minted with the observation
"""

import hashlib
import os
import shutil
from pathlib import Path


def content_address(data):
    """SHA-256 content address of exact bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def observe_destination(destination):
    """Independently observe the current state of a destination.

    Returns:
        dict with 'exists', 'content_address' (if exists), 'size'
    """
    path = Path(destination)
    if not path.is_file():
        return {"exists": False, "content_address": None, "size": 0}
    data = path.read_bytes()
    return {
        "exists": True,
        "content_address": content_address(data),
        "size": len(data),
    }


def place_artifact(source_bytes, destination, expected_prior_address=None):
    """Execute a filesystem placement.

    Args:
        source_bytes: the exact bytes to place
        destination: absolute path to the target location
        expected_prior_address: if set, the destination must currently
            have this content address (compare-and-swap semantics)

    Returns:
        dict with 'outcome', 'source_address', 'destination',
        'prior_observation', 'post_observation'
    """
    dest = Path(destination)
    source_addr = content_address(source_bytes)

    prior = observe_destination(destination)
    if expected_prior_address is not None:
        if prior["content_address"] != expected_prior_address:
            return {
                "outcome": "NOT_HAPPENED",
                "reason": "prior_mismatch",
                "source_address": source_addr,
                "destination": str(dest),
                "expected_prior": expected_prior_address,
                "actual_prior": prior["content_address"],
                "prior_observation": prior,
                "post_observation": prior,
            }

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source_bytes)

    post = observe_destination(destination)
    if post["content_address"] != source_addr:
        return {
            "outcome": "INDETERMINATE",
            "reason": "post_observation_mismatch",
            "source_address": source_addr,
            "destination": str(dest),
            "prior_observation": prior,
            "post_observation": post,
        }

    return {
        "outcome": "HAPPENED",
        "source_address": source_addr,
        "destination": str(dest),
        "prior_observation": prior,
        "post_observation": post,
    }


def build_placement_receipt(intent_id, placement_result):
    """Build a FilesystemPlacementReceipt from an execution result."""
    return {
        "kind": "FilesystemPlacementReceipt",
        "intent_id": intent_id,
        "outcome": placement_result["outcome"],
        "source_address": placement_result["source_address"],
        "destination": placement_result["destination"],
        "prior_address": placement_result["prior_observation"].get(
            "content_address"),
        "post_address": placement_result["post_observation"].get(
            "content_address"),
        "verified": (placement_result["outcome"] == "HAPPENED"
                     and placement_result["post_observation"].get(
                         "content_address")
                     == placement_result["source_address"]),
    }
