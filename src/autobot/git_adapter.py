"""The Git adapter — the registered ``git_adapter`` seat (C4-M1).

principals.json registers ``git_adapter``; cap-31 makes it the owner of
"Source-state capture (Git)" and cap-32 the owner of "CandidateReceipt",
with the verifier_runner and git_sequencer as consumers and the task_force
FORBIDDEN; objects.json registers obj-16 with minter ``git_adapter``,
class receipt, immutable, attempt-bound. On "what tree a ref names" the
adapter holds R on cap-42: Git itself is the authority, and the adapter
reads it through Git's own object model, never through the filesystem.

Like the collector and the git executor, this component GRANTS NO
PROTOCOL AUTHORITY: it reads the frozen and admitted facts through the
Sequencer's public read surface (``candidate_state``), reads the candidate
tree identity from Git (cap-42), mints the ONE canonical obj-16 payload,
and hands the bytes to the freeze admission, which re-validates every
ledger-derivable binding and refuses divergence. Candidate identity
derives from authoritative repository and ledger facts ONLY — never agent
claims, transcript statements, mtime, a working tree observed later
without binding, or a "latest commit" heuristic.

The class split is Chapter 0's:
- SOURCE_MODIFYING work binds the candidate Git tree identity — the exact
  tree under verification, immutable once minted (workspace drift after
  the freeze cannot reach the verifier's subject).
- READ_ONLY work gains NO invented candidate tree (D-37 absence): the
  candidate is the attempt's admitted artifact/observation set.
"""

import subprocess

from contract_library import canonicalize

from .models import ConflictError, instant

CANDIDATE_KIND = "CandidateReceipt"

# obj-16's registered minter (objects.json; cap-32).
CANDIDATE_MINTER = "git_adapter"

_TREE_HEX_LENGTHS = (40, 64)  # sha1 and sha256 repository object names


def capture_tree(repo_path, treeish):
    """cap-42 read: the tree object name ``treeish`` peels to, from Git's
    own object model (``rev-parse <treeish>^{tree}``). Refuses anything
    that does not resolve to a tree — the adapter never invents identity."""
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", treeish + "^{tree}"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise ConflictError(
            "candidate tree capture failed: %r does not resolve to a tree"
            " in %s (cap-42: Git is the authority on what a ref names)"
            % (treeish, repo_path))
    tree = proc.stdout.strip()
    if len(tree) not in _TREE_HEX_LENGTHS or any(
            c not in "0123456789abcdef" for c in tree):
        raise ConflictError(
            "candidate tree capture returned %r, not a Git object name"
            % tree)
    return tree


def build_candidate_payload(state, clock, candidate_tree=None):
    """The canonical obj-16 payload over the Sequencer's candidate_state
    facts. Pure except for the capture instant (audit metadata, D-27):
    every binding member comes from durable ledger facts, and the tree —
    when the class requires one — from cap-42's capture."""
    finalization_class = state["finalization_class"]
    if finalization_class == "SOURCE_MODIFYING":
        if candidate_tree is None:
            raise ConflictError(
                "SOURCE_MODIFYING work binds the candidate Git tree"
                " identity: no tree was captured (obj-16)")
    elif candidate_tree is not None:
        raise ConflictError(
            "READ_ONLY work gains no invented candidate tree (obj-16):"
            " the candidate is the artifact/observation set")
    payload = {
        "schema_version": "v1",
        "kind": CANDIDATE_KIND,
        "minter": CANDIDATE_MINTER,
        "attempt": {"sequence_id": state["sequence_id"],
                    "attempt_id": state["attempt_id"],
                    "fence": state["fence"]},
        "attempt_contract_hash": state["attempt_contract_hash"],
        "record_hash": state["record_hash"],
        "requirement_digest": state["requirement_digest"],
        "contract_digest": state["contract_digest"],
        "finalization_class": finalization_class,
        "source_state": dict(state["source_state"]),
        "artifacts": sorted(
            ({"logical_path": a["logical_path"],
              "content_sha256": a["content_sha256"], "role": a["role"]}
             for a in state["artifacts"]),
            key=lambda a: (a["logical_path"], a["content_sha256"])),
        "captured_utc": instant(clock.now()),
    }
    if candidate_tree is not None:  # D-37: absence by omission
        payload["candidate_tree"] = {"git_tree_sha": candidate_tree}
    return payload


def mint_candidate_receipt(sequencer, store, attempt_id,
                           repo_path=None, treeish=None):
    """Mint the obj-16 CandidateReceipt for one FINISHED attempt and put
    its canonical bytes in the content store. Returns
    ``{"candidate_address", "payload"}`` — the freeze admission
    (``Sequencer.freeze_candidate``) then independently re-validates every
    ledger-derivable binding before t-a05/t-s07 commit.

    For SOURCE_MODIFYING work the caller names the repository and the
    tree-ish whose tree IS the candidate (the proposed new value's tree,
    never "whatever the workspace holds now"); for READ_ONLY work both
    must be absent."""
    state = sequencer.candidate_state(attempt_id)
    candidate_tree = None
    if state["finalization_class"] == "SOURCE_MODIFYING":
        if repo_path is None or treeish is None:
            raise ConflictError(
                "SOURCE_MODIFYING candidate capture needs the repository"
                " and the candidate tree-ish (cap-31/cap-42)")
        candidate_tree = capture_tree(repo_path, treeish)
    elif repo_path is not None or treeish is not None:
        raise ConflictError(
            "READ_ONLY candidate capture takes no repository or tree-ish"
            " (no invented candidate tree)")
    payload = build_candidate_payload(state, sequencer.clock,
                                      candidate_tree)
    address = store.put(canonicalize(payload))
    return {"candidate_address": address, "payload": payload}
