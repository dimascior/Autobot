"""Tests for execution-reach policy registration and command correlation."""

import json
import unittest

import support

from autobot.execution_reach import (
    EXECUTION_REACH_POLICIES,
    DEFAULT_LUNA_POLICY,
    authorized_reach,
    get_policy,
    policy_ids,
)
from autobot.receipts import (
    LUNA_LOCAL_AUTHORIZED_REACH,
    correlate_commands,
)


class PolicyRegistryTests(unittest.TestCase):
    """Every registered policy has the required shape."""

    def test_all_policies_have_required_fields(self):
        for pid, policy in EXECUTION_REACH_POLICIES.items():
            self.assertIn("description", policy, pid)
            self.assertIn("authorized_reach", policy, pid)
            self.assertIn("provider", policy, pid)
            self.assertIn("model_family", policy, pid)
            self.assertIsInstance(policy["authorized_reach"], frozenset, pid)

    def test_default_luna_policy_exists(self):
        policy = get_policy(DEFAULT_LUNA_POLICY)
        self.assertIsNotNone(policy)
        self.assertEqual(policy["authorized_reach"],
                         frozenset({"MACHINE_LOCAL"}))

    def test_luna_local_authorized_reach_matches_policy(self):
        self.assertEqual(LUNA_LOCAL_AUTHORIZED_REACH,
                         authorized_reach(DEFAULT_LUNA_POLICY))

    def test_research_policy_allows_connector(self):
        policy = get_policy("luna-research/v1")
        self.assertIsNotNone(policy)
        self.assertIn("PROVIDER_CONNECTOR", policy["authorized_reach"])
        self.assertNotIn("PROVIDER_SUBAGENT", policy["authorized_reach"])

    def test_opus_engineer_allows_all_external(self):
        policy = get_policy("opus-engineer/v1")
        self.assertIsNotNone(policy)
        self.assertIn("PROVIDER_CONNECTOR", policy["authorized_reach"])
        self.assertIn("PROVIDER_SUBAGENT", policy["authorized_reach"])

    def test_orchestrator_has_no_execution_reach(self):
        policy = get_policy("opus-orchestrator/v1")
        self.assertIsNotNone(policy)
        self.assertEqual(policy["authorized_reach"], frozenset())

    def test_unknown_policy_returns_none(self):
        self.assertIsNone(get_policy("nonexistent/v1"))

    def test_unknown_policy_returns_empty_reach(self):
        self.assertEqual(authorized_reach("nonexistent/v1"), frozenset())

    def test_policy_ids_sorted(self):
        ids = policy_ids()
        self.assertEqual(ids, sorted(ids))
        self.assertIn(DEFAULT_LUNA_POLICY, ids)


class CommandCorrelationTests(unittest.TestCase):
    """Command-correlation joins obj-13 command IDs against obj-12 facts."""

    def test_full_match(self):
        reach = {"command_ids": ["exec-001", "exec-002"]}
        facts = {
            "sha256:aaa": {"tool_use_id": "exec-001"},
            "sha256:bbb": {"tool_use_id": "exec-002"},
        }
        result = correlate_commands(reach, facts)
        self.assertEqual(len(result["matched"]), 2)
        self.assertEqual(result["provider_only"], [])
        self.assertEqual(result["helios_only"], [])

    def test_provider_only(self):
        reach = {"command_ids": ["exec-001", "exec-002"]}
        facts = {"sha256:aaa": {"tool_use_id": "exec-001"}}
        result = correlate_commands(reach, facts)
        self.assertEqual(result["matched"], ["exec-001"])
        self.assertEqual(result["provider_only"], ["exec-002"])
        self.assertEqual(result["helios_only"], [])

    def test_helios_only(self):
        reach = {"command_ids": ["exec-001"]}
        facts = {
            "sha256:aaa": {"tool_use_id": "exec-001"},
            "sha256:bbb": {"tool_use_id": "exec-003"},
        }
        result = correlate_commands(reach, facts)
        self.assertEqual(result["matched"], ["exec-001"])
        self.assertEqual(result["provider_only"], [])
        self.assertEqual(result["helios_only"], ["exec-003"])

    def test_empty_inputs(self):
        result = correlate_commands({}, {})
        self.assertEqual(result["matched"], [])
        self.assertEqual(result["provider_only"], [])
        self.assertEqual(result["helios_only"], [])

    def test_no_governance_facts(self):
        reach = {"command_ids": ["exec-001"]}
        facts = {"sha256:aaa": {"outcome": "success"}}
        result = correlate_commands(reach, facts)
        self.assertEqual(result["provider_only"], ["exec-001"])
        self.assertEqual(result["helios_only"], [])


class ReceiptPolicyDerivationTests(unittest.TestCase):
    """derive_evidence reads execution_profile from the frozen record."""

    def test_default_policy_when_record_has_no_profile(self):
        from autobot.receipts import derive_evidence
        from autobot import execution_reach as rp
        state = _make_evidence_state()
        self.assertNotIn("execution_profile", state["record_payload"])
        receipt = derive_evidence(_empty_store(), state, 3600)
        if receipt.get("execution_reach", {}).get("policy_id"):
            self.assertEqual(receipt["execution_reach"]["policy_id"],
                             rp.DEFAULT_LUNA_POLICY)

    def test_record_profile_overrides_default(self):
        from autobot.receipts import derive_evidence
        state = _make_evidence_state()
        state["record_payload"]["execution_profile"] = "luna-research/v1"
        receipt = derive_evidence(_empty_store(), state, 3600)
        if receipt.get("execution_reach", {}).get("policy_id"):
            self.assertEqual(receipt["execution_reach"]["policy_id"],
                             "luna-research/v1")

    def test_unknown_profile_falls_back_to_default(self):
        from autobot.receipts import derive_evidence
        from autobot import execution_reach as rp
        state = _make_evidence_state()
        state["record_payload"]["execution_profile"] = "nonexistent/v99"
        receipt = derive_evidence(_empty_store(), state, 3600)
        if receipt.get("execution_reach", {}).get("policy_id"):
            self.assertEqual(receipt["execution_reach"]["policy_id"],
                             rp.DEFAULT_LUNA_POLICY)


class CompilerExecutionProfileTests(unittest.TestCase):
    """The compiler freezes execution_profile from work_profile."""

    def test_engineering_profile_derived(self):
        from intelligence.compiler import compile_proposal
        proposal = _make_proposal(work_profile="ENGINEERING")
        env = _make_env()
        payload = compile_proposal(proposal, env, _clock())
        self.assertEqual(payload["work_profile"], "ENGINEERING")
        self.assertEqual(payload["execution_profile"], "luna-local/v1")

    def test_research_profile_derived(self):
        from intelligence.compiler import compile_proposal
        proposal = _make_proposal(work_profile="RESEARCH")
        env = _make_env()
        payload = compile_proposal(proposal, env, _clock())
        self.assertEqual(payload["execution_profile"], "luna-research/v1")

    def test_operations_profile_derived(self):
        from intelligence.compiler import compile_proposal
        proposal = _make_proposal(work_profile="GENERAL_OPERATION")
        env = _make_env()
        payload = compile_proposal(proposal, env, _clock())
        self.assertEqual(payload["execution_profile"], "luna-resource/v1")

    def test_explicit_profile_overrides_default(self):
        from intelligence.compiler import compile_proposal
        proposal = _make_proposal(work_profile="ENGINEERING",
                                  execution_profile="opus-engineer/v1")
        env = _make_env()
        payload = compile_proposal(proposal, env, _clock())
        self.assertEqual(payload["execution_profile"], "opus-engineer/v1")

    def test_no_work_profile_no_execution_profile(self):
        from intelligence.compiler import compile_proposal
        proposal = _make_proposal()
        env = _make_env()
        payload = compile_proposal(proposal, env, _clock())
        self.assertNotIn("execution_profile", payload)

    def test_no_work_profile_explicit_execution_profile(self):
        from intelligence.compiler import compile_proposal
        proposal = _make_proposal(execution_profile="opus-high-context/v1")
        env = _make_env()
        payload = compile_proposal(proposal, env, _clock())
        self.assertEqual(payload["execution_profile"],
                         "opus-high-context/v1")


class SessionRecognizerZstdTests(unittest.TestCase):
    """The recognizer handles both plain and zstd-compressed JSONL."""

    def test_plain_jsonl(self):
        from autobot.session_recognizer import recognize_session
        lines = (
            b'{"type":"session_meta","payload":{"session_id":"s1"}}\n'
            b'{"type":"event_msg","payload":{"item":{"type":"CommandExecution","id":"exec-001","command":["ls"],"exit_code":0,"status":"completed"}}}\n'
        )
        result = recognize_session(lines)
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], "s1")
        self.assertIn("exec-001", result["command_ids"])

    def test_zstd_jsonl(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("zstandard not installed")
        from autobot.session_recognizer import recognize_session
        plain = (
            b'{"type":"session_meta","payload":{"session_id":"s2"}}\n'
            b'{"type":"event_msg","payload":{"item":{"type":"CommandExecution","id":"exec-002","command":["pwd"],"exit_code":0,"status":"completed"}}}\n'
        )
        compressed = zstandard.ZstdCompressor().compress(plain)
        result = recognize_session(compressed)
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], "s2")
        self.assertIn("exec-002", result["command_ids"])

    def test_zstd_and_plain_produce_same_facts(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("zstandard not installed")
        from autobot.session_recognizer import recognize_session
        plain = (
            b'{"type":"session_meta","payload":{"session_id":"s3"}}\n'
            b'{"type":"event_msg","payload":{"item":{"type":"FileChange","file_path":"/tmp/x"}}}\n'
        )
        compressed = zstandard.ZstdCompressor().compress(plain)
        r_plain = recognize_session(plain)
        r_zstd = recognize_session(compressed)
        self.assertEqual(r_plain["session_id"], r_zstd["session_id"])
        self.assertEqual(r_plain["item_counts"], r_zstd["item_counts"])
        self.assertEqual(len(r_plain["execution_items"]),
                         len(r_zstd["execution_items"]))


def _make_evidence_state(execution_profile=None):
    """Minimal evidence state for receipt derivation tests."""
    record = {
        "evidence_requirements": ["SESSION_TRANSCRIPT",
                                  "COMMAND_GOVERNANCE"],
        "completion_policies": {"builder": {"required": True,
                                            "success_outcomes": ["success"]}},
    }
    if execution_profile:
        record["execution_profile"] = execution_profile
    return {
        "record_payload": record,
        "role": "builder",
        "attempt_id": "at_" + "0" * 32,
        "attempt_contract_hash": "sha256:" + "0" * 64,
        "call_id": "call_" + "0" * 32,
        "native_session_id": None,
        "witnesses": [],
        "artifacts": [],
        "anchor_utc": "2026-11-20T00:00:00.000Z",
    }


class _empty_store:
    def get(self, address):
        raise KeyError(address)


def _clock():
    import datetime
    class C:
        def now(self):
            return datetime.datetime(2026, 11, 20, 0, 0, 0)
    return C()


def _make_env():
    return support.intelligence_environment()


def _make_proposal(work_profile=None, execution_profile=None,
                   resource_scope=None):
    req = {
        "objective": "test objective",
        "capability": "research",
        "roles": {"investigator": {"required": True,
                                   "success_outcomes": ["success"]}},
    }
    if work_profile is not None:
        req["work_profile"] = work_profile
    if execution_profile is not None:
        req["execution_profile"] = execution_profile
    if resource_scope is not None:
        req["resource_scope"] = resource_scope
    return {"class": "INVESTIGATE", "requirement": req,
            "snapshot": {"entries": 1, "head_hash": "sha256:" + "a" * 64}}


class CompilerResourceScopeTests(unittest.TestCase):
    """The compiler freezes resource_scope into the TaskContractRecord."""

    def test_scope_frozen_when_present(self):
        from intelligence.compiler import compile_proposal
        scope = [{"kind": "REPOSITORY", "identity": "/repo",
                  "activity_class": "OBSERVATION", "constraints": {}}]
        proposal = _make_proposal(resource_scope=scope)
        payload = compile_proposal(proposal, _make_env(), _clock())
        self.assertEqual(payload["resource_scope"], scope)

    def test_no_scope_means_absent(self):
        from intelligence.compiler import compile_proposal
        proposal = _make_proposal()
        payload = compile_proposal(proposal, _make_env(), _clock())
        self.assertNotIn("resource_scope", payload)

    def test_research_scope_with_web_source(self):
        from intelligence.compiler import compile_proposal
        scope = [
            {"kind": "REPOSITORY", "identity": "/repo",
             "activity_class": "OBSERVATION", "constraints": {}},
            {"kind": "WEB_SOURCE", "identity": "public-web",
             "activity_class": "OBSERVATION", "constraints": {}},
        ]
        proposal = _make_proposal(work_profile="RESEARCH",
                                  resource_scope=scope)
        payload = compile_proposal(proposal, _make_env(), _clock())
        self.assertEqual(payload["work_profile"], "RESEARCH")
        self.assertEqual(payload["execution_profile"], "luna-research/v1")
        self.assertEqual(len(payload["resource_scope"]), 2)
        kinds = [e["kind"] for e in payload["resource_scope"]]
        self.assertIn("WEB_SOURCE", kinds)

    def test_invalid_scope_rejected(self):
        from intelligence.compiler import compile_proposal
        scope = [{"kind": "INVALID_KIND", "identity": "x",
                  "activity_class": "OBSERVATION"}]
        proposal = _make_proposal(resource_scope=scope)
        with self.assertRaises(Exception):
            compile_proposal(proposal, _make_env(), _clock())


if __name__ == "__main__":
    unittest.main()
