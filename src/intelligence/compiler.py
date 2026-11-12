"""The deterministic proposal -> TaskContractRecord compiler (cap-07).

Sits between Chief Engineer proposals and Registry admission: it
translates one contract-class proposal into the canonical Chapter 1
TaskContractRecord shape, producing candidate values for the
requirement text, dependency edges, roles and transforms, completion
policies, retry/repair/reuse/effect policies, verification assertions,
evidence requirements, repository and delegation bindings, and the
SEM-05-derived finalization requirements.

It is a PURE function of (proposal, environment, clock): no database
handle, no store handle, no network. The Registry remains responsible
for canonical validation, identity and freeze (cap-08) — the compiler
proposes bytes; admission decides. Proposal provenance (why the Chief
Engineer created this contract) is recorded in the plan store, never
inside the frozen payload, which keeps answering only "what exactly was
authorized?".

The environment is the planner's standing authority context: the bound
repository declaration, the active delegation you granted (cap-03), and
the registered verifier/transform registrations — exactly the objects
the Registry must already hold for the candidate to freeze (an unbound
candidate is REJECTED with ERR_UNRESOLVED_BINDING, never guessed at).
"""

from autobot.models import instant, mint_id
from contract_library import object_hash
from contract_library.grammars import utf16_key
from contract_library.validate import derived_finalization_requirements

from .proposals import CONTRACT_CLASSES, ProposalError


def _sorted_set(values):
    return sorted(values, key=utf16_key)


def _sorted_edges(edges):
    return sorted(edges, key=lambda e: utf16_key(e["edge_id"]))


def _sorted_assertions(assertions):
    return sorted(assertions, key=lambda a: utf16_key(a["assertion_id"]))


class RepositoryEnvironment:
    """The compiler's bound authority context for one repository.

    ``declaration`` and ``delegation`` are the exact payloads you
    admitted (their object hashes are RECOMPUTED here, never caller-
    claimed); ``verifier_registrations`` / ``transform_registrations``
    are the registered payloads the candidate binds; ``defaults`` carry
    the engineering profile the Chief Engineer does not restate per
    proposal (assertions, scope, policies, protocol capability ids,
    evidence requirements, the role transform)."""

    def __init__(self, declaration, delegation, verifier_registrations,
                 transform_registrations, defaults):
        self.declaration = declaration
        self.declaration_hash = object_hash(
            "RepositoryAuthorityDeclaration", declaration)
        self.delegation = delegation
        self.delegation_hash = object_hash("DelegationContract", delegation)
        self.verifier_hashes = _sorted_set([
            object_hash("VerifierRegistration", payload)
            for payload in verifier_registrations])
        self.transform_hashes = _sorted_set([
            object_hash("TransformRegistration", payload)
            for payload in transform_registrations])
        self.defaults = dict(defaults)

    @property
    def repository_id(self):
        return self.declaration["repository_id"]

    def floor_ids(self, finalization_class):
        floors = self.delegation.get("mandatory_floors", {})
        entry = floors.get(self.repository_id, {}).get(finalization_class, [])
        return _sorted_set(entry)


def compile_proposal(proposal, environment, clock):
    """One contract-class proposal -> one canonical candidate payload."""
    cls = proposal.get("class")
    if cls not in CONTRACT_CLASSES:
        raise ProposalError(
            "class %r does not compile to a TaskContractRecord" % (cls,))
    requirement = proposal["requirement"]
    read_only = cls in ("INVESTIGATE", "REQUEST_REVIEW") or \
        requirement.get("finalization_class") == "READ_ONLY"
    fin_class = "READ_ONLY" if read_only else "SOURCE_MODIFYING"
    declaration = environment.declaration
    location = declaration["authority_location"]
    defaults = environment.defaults

    edges = [dict(edge) for edge in requirement.get("predecessors", [])]
    if cls == "REQUEST_REVIEW":
        edges.append({
            "edge_id": "review-of",
            "edge_kind": "RECORD",
            "target": proposal["review_of"]["record_hash"],
            "observation_requirement": "ACCEPTANCE",
        })

    roles = requirement["roles"]
    completion_policies = {}
    for role in sorted(roles, key=utf16_key):
        policy = roles[role]
        completion_policies[role] = {
            "required": policy["required"],
            "success_outcomes": _sorted_set(policy["success_outcomes"]),
        }
    transform_id = defaults["role_transform_id"]
    role_transforms = {role: transform_id
                       for role in sorted(roles, key=utf16_key)}

    assertions = requirement.get(
        "acceptance_assertions", defaults["acceptance_assertions"])
    scope = requirement.get("allowed_scope", defaults["allowed_scope"])

    payload = {
        "schema_version": "v1",
        "kind": "TaskContractRecord",
        "task_contract_id": mint_id("tc"),
        "authored_by": "planner",
        "revision": 1,
        "force_new_sequence": False,
        "frozen_utc": instant(clock.now()),
        "objective": requirement["objective"],
        "finalization_class": fin_class,
        "source_domain": {
            "repository_id": environment.repository_id,
            "authority_location": location,
        },
        "predecessors": _sorted_edges(edges),
        "completion_policies": completion_policies,
        "role_transforms": role_transforms,
        "capability_requirements": _sorted_set(
            defaults["capability_requirements"]),
        "evidence_requirements": _sorted_set(
            defaults["evidence_requirements"]),
        "acceptance_assertions": {
            "ids_and_environment_constraints": _sorted_assertions(
                assertions["ids_and_environment_constraints"]),
        },
        "allowed_scope": {
            "paths": _sorted_set(scope.get("paths", [])),
            "artifact_paths": _sorted_set(scope.get("artifact_paths", [])),
        },
        # The schema requires these on every record; "no inputs" is the
        # empty mapping, and the effect windows ride along even for
        # READ_ONLY (which never mints an intent).
        "specification_inputs": requirement.get(
            "specification_inputs",
            {"name_to_exact_byte_sha256": {}}),
        "effect_policy": dict(requirement.get(
            "effect_policy", defaults["effect_policy"])),
        "retry_policy": dict(requirement.get(
            "retry_policy", defaults["retry_policy"])),
        "repair_policy": dict(requirement.get(
            "repair_policy", defaults["repair_policy"])),
        "reuse_policy": dict(requirement.get(
            "reuse_policy", defaults["reuse_policy"])),
        "resolved_bindings": {
            "repository_authority_declaration":
                environment.declaration_hash,
            "delegation_contract": environment.delegation_hash,
            "mandatory_floor": {
                "delegation": environment.delegation_hash,
                "mandatory_assertion_ids": environment.floor_ids(fin_class),
            },
            "verifier_registrations": list(environment.verifier_hashes),
            "transform_registrations": list(environment.transform_hashes),
        },
        "delegation_id": environment.delegation["delegation_id"],
    }
    if fin_class == "SOURCE_MODIFYING":
        # SEM-03: the canonical ref is the declaration's, never invented.
        payload["source_domain"]["target_ref"] = declaration["canonical_ref"]
    # SEM-05: the exact derived requirement set for this class/location
    # (the REMOTE arms stay with their chapters; a REMOTE declaration
    # compiles only if the mode-less derivation applies).
    derived = derived_finalization_requirements(
        fin_class, location, payload.get("remote_verification_mode"))
    if derived is None:
        raise ProposalError(
            "no SEM-05 derivation for class %r at location %r"
            % (fin_class, location))
    payload["finalization_requirements"] = derived
    return payload
