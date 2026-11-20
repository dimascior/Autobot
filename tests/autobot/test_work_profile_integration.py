"""WorkProfile integration tests: compiler, proposals, and receipts.

Verifies that work_profile flows through the proposal-compiler-contract
chain without breaking existing behavior.
"""

import unittest

import support

from autobot.work_profile import (
    WORK_PROFILES, VALID_PROFILES,
    get_profile, default_execution_profile, default_finalization_class,
)
from autobot.execution_reach import EXECUTION_REACH_POLICIES


class WorkProfileInRequirementTests(unittest.TestCase):
    """work_profile is accepted in proposal validation."""

    def test_valid_work_profiles_accepted(self):
        from intelligence.proposals import _validate_requirement
        for wp in VALID_PROFILES:
            req = {
                "objective": "test",
                "work_profile": wp,
                "finalization_class": default_finalization_class(wp),
                "capability": "research",
                "guidance": "test guidance",
                "roles": {"builder": {
                    "required": True,
                    "success_outcomes": ["PASS"]}},
            }
            _validate_requirement(req, forced_read_only=False)

    def test_unknown_work_profile_rejected(self):
        from intelligence.proposals import _validate_requirement, ProposalError
        req = {
            "objective": "test",
            "work_profile": "UNKNOWN",
            "finalization_class": "READ_ONLY",
            "capability": "research",
            "guidance": "test",
            "roles": {"builder": {
                "required": True,
                "success_outcomes": ["PASS"]}},
        }
        with self.assertRaises(ProposalError):
            _validate_requirement(req, forced_read_only=False)

    def test_none_work_profile_accepted(self):
        from intelligence.proposals import _validate_requirement
        req = {
            "objective": "test",
            "finalization_class": "READ_ONLY",
            "capability": "research",
            "guidance": "test",
            "roles": {"builder": {
                "required": True,
                "success_outcomes": ["PASS"]}},
        }
        _validate_requirement(req, forced_read_only=False)


class WorkProfileInCompilerTests(unittest.TestCase):
    """work_profile passes through the compiler into the payload."""

    def _make_environment(self):
        from intelligence.compiler import RepositoryEnvironment
        declaration = {
            "schema_version": "v1",
            "kind": "RepositoryAuthorityDeclaration",
            "repository_id": "test",
            "authority_location": "LOCAL",
            "canonical_ref": "refs/heads/main",
        }
        delegation = {
            "schema_version": "v1",
            "kind": "DelegationContract",
            "delegation_id": "dg_test",
            "mandatory_floors": {},
        }
        verifier = {
            "schema_version": "v1",
            "kind": "VerifierRegistration",
            "verifier_id": "vf_test",
        }
        transform = {
            "schema_version": "v1",
            "kind": "TransformRegistration",
            "transform_id": "tf_test",
        }
        defaults = {
            "evidence_requirements": ["EXECUTION_LIFECYCLE"],
            "capability_requirements": [],
            "acceptance_assertions": {
                "ids_and_environment_constraints": [],
            },
            "allowed_scope": {"paths": [], "artifact_paths": []},
            "specification_inputs": {"name_to_exact_byte_sha256": {}},
            "effect_policy": {"window_seconds": 300},
            "retry_policy": {"max_attempts": 3},
            "repair_policy": {"max_repairs": 1},
            "reuse_policy": {"mode": "NONE"},
            "role_transform_id": "tf_test",
        }
        return RepositoryEnvironment(
            declaration=declaration,
            delegation=delegation,
            verifier_registrations=[verifier],
            transform_registrations=[transform],
            defaults=defaults,
        )

    def _make_proposal(self, work_profile=None, fin_class="SOURCE_MODIFYING"):
        req = {
            "objective": "test objective",
            "finalization_class": fin_class,
            "capability": "research",
            "guidance": "test guidance",
            "roles": {"builder": {
                "required": True,
                "success_outcomes": ["PASS"]}},
        }
        if work_profile is not None:
            req["work_profile"] = work_profile
        return {
            "class": "CREATE_TASK",
            "objective_id": "obj-test",
            "snapshot": {"entries": 0},
            "provenance": {},
            "requirement": req,
        }

    def test_work_profile_in_compiled_payload(self):
        from intelligence.compiler import compile_proposal
        from autobot.models import SystemClock
        env = self._make_environment()
        proposal = self._make_proposal(work_profile="ENGINEERING")
        payload = compile_proposal(proposal, env, SystemClock())
        self.assertEqual(payload["work_profile"], "ENGINEERING")

    def test_none_work_profile_absent_from_payload(self):
        from intelligence.compiler import compile_proposal
        from autobot.models import SystemClock
        env = self._make_environment()
        proposal = self._make_proposal(work_profile=None)
        payload = compile_proposal(proposal, env, SystemClock())
        self.assertNotIn("work_profile", payload)

    def test_research_work_profile(self):
        from intelligence.compiler import compile_proposal
        from autobot.models import SystemClock
        env = self._make_environment()
        proposal = self._make_proposal(
            work_profile="RESEARCH", fin_class="READ_ONLY")
        payload = compile_proposal(proposal, env, SystemClock())
        self.assertEqual(payload["work_profile"], "RESEARCH")
        self.assertEqual(payload["finalization_class"], "READ_ONLY")


class WorkProfileDefaultConsistency(unittest.TestCase):
    """WorkProfile defaults are consistent with existing behavior."""

    def test_engineering_default_matches_existing(self):
        self.assertEqual(default_finalization_class("ENGINEERING"),
                         "SOURCE_MODIFYING")

    def test_research_default_matches_existing(self):
        self.assertEqual(default_finalization_class("RESEARCH"),
                         "READ_ONLY")

    def test_all_execution_profiles_registered(self):
        for wp_name in VALID_PROFILES:
            ep = default_execution_profile(wp_name)
            self.assertIn(ep, EXECUTION_REACH_POLICIES)


if __name__ == "__main__":
    unittest.main()
