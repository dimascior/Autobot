"""Model routing: the execution route is replaceable; no identity is.

A work requirement states a capability and a role. Staffing decides the
specialist. Routing decides, last, the provider, the model and the
session configuration. These tests change the route — between two
organizations, between two pieces of work, between two attempts of the
same work — and assert that every engineering and organizational identity
is what it was: the TaskContractRecord, the ProjectAssignment, the
specialist, the TaskforceMembership, the TaskforceSeat and the
CapabilityDemonstration. They also pin what does change: the
AttemptContract, the ExecutionManifest and the call.

The routes here are the production policy's (Luna 6 Max by default,
Opus 4.6 1M for the Engineer and for escalation, Sonnet as fallback,
Haiku as utility), driven through the real Registry, Sequencer and
Collector with a scripted provider.
"""

import json
import os
import shlex
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contract_library import compute_tiers
from intelligence import (
    ChiefEngineerPolicy,
    compile_proposal,
    preflight,
    supervision,
)
from intelligence.planstore import PLAN_SCHEMA_VERSION, PlanStore
from intelligence.policy import INSTRUCTIONS, _without_routing
from intelligence.proposals import ProposalError, validate_staffing
from intelligence.routing import (
    ESCALATION_CLASSES,
    RoutingError,
    RoutingPolicy,
    route_identity,
)
from intelligence.runtime import ConfigError, build_pool
from intelligence.workforce import (
    assignment_identity,
    membership_identity,
    seat_identity,
    specialist_identity,
)
from test_architecture_conformance import WorkGraphPolicy
from test_autonomous_pilot import QueueRunner, ScriptedTransport, result
from test_taskforce_orchestration import (
    RetryPolicy,
    TwoTaskPolicy,
    WorkforceTestCase,
    _Boom,
    _fail_once,
    assign,
    complete,
    investigate,
    provider_failure,
)

ENGINEER_MODEL = "claude-opus-4-6[1m]"
LUNA = {"provider": "codex", "model": "gpt-6-luna", "effort": "max"}
SONNET = {"provider": "claude-code", "model": "sonnet"}
OPUS = {"provider": "claude-code", "model": ENGINEER_MODEL}
LUNA_ARGS = ["--sandbox", "workspace-write", "--ask-for-approval",
             "on-request"]


def routing_config(default="luna-6-max", specialists=None, **routing):
    """The production routing policy, with the default route chosen."""
    section = {
        "default": default,
        "escalation": "opus-4-6-1m",
        "fallback": ["sonnet"],
        "utility": "haiku",
        "routes": {
            "luna-6-max": {"agent": "codex", "model": "gpt-6-luna",
                           "effort": "max",
                           "provider_args": list(LUNA_ARGS)},
            "opus-4-6-1m": {"agent": "claude-code",
                            "model": ENGINEER_MODEL},
            "sonnet": {"agent": "claude-code", "model": "sonnet"},
            "haiku": {"agent": "claude-code", "model": "haiku"},
        },
    }
    section.update(routing)
    return {
        "routing": section,
        "engineer": {"model": ENGINEER_MODEL},
        "specialists": specialists or {
            "alpha": {"offers": ["research:investigator"]},
            "beta": {"offers": ["research:investigator"]},
        },
    }


def route_everyone(pool, route):
    """The organization replaces how every worker is executed — what a
    change of ``[routing] default`` does at the next start."""
    for profile in pool.profiles():
        pool.reroute(profile["handle"], provider=route["provider"],
                     model=route["model"], effort=route["effort"],
                     provider_args=route["provider_args"],
                     route_name=route["name"])


class RouteRecordingTransport(ScriptedTransport):
    """The scripted provider, also recording the session configuration
    (reasoning effort, native arguments) the Taskforce stages beside each
    call's manifest."""

    def __init__(self):
        ScriptedTransport.__init__(self)
        self.session = {}
        self.session_provider = {}

    def stage_session(self, call_id, provider_args=(), effort=None,
                      provider=None):
        self.session[call_id] = {"effort": effort,
                                 "args": list(provider_args)}
        self.session_provider[call_id] = provider

    @property
    def session_args(self):
        return {call_id: staged["args"]
                for call_id, staged in self.session.items()}


class RoutingTestCase(WorkforceTestCase):
    DEFAULT = "luna-6-max"
    SPECIALISTS = None

    def setUp(self):
        WorkforceTestCase.setUp(self)
        self.transport = RouteRecordingTransport()
        self.pool = build_pool(routing_config(
            self.DEFAULT, specialists=self.SPECIALISTS))
        self.alpha = specialist_identity("alpha")
        self.beta = specialist_identity("beta")

    def _run(self, policy):
        self._objective()
        self.driver = self._driver(policy, runner=QueueRunner())
        return self.driver.run_until_stable()

    def _manifest(self, call_id):
        """The ExecutionManifest a call was launched from, read back from
        the content store by the address the launch carried."""
        entry = [e for e in self.transport.launch_log
                 if e["call_id"] == call_id][-1]
        return json.loads(self.rt.store.get(entry["manifest_address"]))

    def _route_of(self, assignment):
        """The route a call was launched on: the provider and the model
        in its ExecutionManifest, and the reasoning effort staged beside
        it."""
        launched = dict(
            self._manifest(assignment["call_id"])["provider_assignment"])
        effort = self.transport.session.get(
            assignment["call_id"], {}).get("effort")
        if effort:
            launched["effort"] = effort
        return launched

    def _records(self):
        return [dict(r) for r in self.rt.db.conn.execute(
            "SELECT sequence_id, record_hash, contract_digest FROM"
            " sequences ORDER BY rowid").fetchall()]

    def _attempts(self):
        return [dict(r) for r in self.rt.db.conn.execute(
            "SELECT attempt_id, sequence_id, state, attempt_contract_hash"
            " FROM attempts ORDER BY rowid").fetchall()]

    def _switch_to(self, name):
        return lambda: route_everyone(
            self.pool, self.pool.routing.route(name))


# =========================================================== the policy

class RoutingPolicyTests(unittest.TestCase):
    """The declared policy and what each class of work resolves to."""

    def setUp(self):
        self.policy = RoutingPolicy.from_config(
            routing_config(escalate=["architecture",
                                     "verification:auditor"])["routing"])

    def test_each_class_resolves_to_its_declared_route(self):
        default = self.policy.resolve("default")
        self.assertEqual(route_identity(default), dict(
            LUNA, name="luna-6-max", served_as="default"))
        self.assertEqual(default["provider_args"], LUNA_ARGS)
        self.assertEqual(route_identity(self.policy.resolve("escalation")),
                         dict(OPUS, name="opus-4-6-1m",
                              served_as="escalation"))
        self.assertEqual(self.policy.resolve("utility")["model"], "haiku")
        self.assertEqual(self.policy.resolve("fallback")["model"], "sonnet")

    def test_the_fallback_is_used_only_while_the_default_is_disabled(self):
        self.assertEqual(self.policy.resolve("default")["name"],
                         "luna-6-max")
        config = routing_config()["routing"]
        config["routes"]["luna-6-max"]["enabled"] = False
        policy = RoutingPolicy.from_config(config)
        fallback = policy.resolve("default")
        self.assertEqual((fallback["name"], fallback["served_as"]),
                         ("sonnet", "fallback"))
        config["routes"]["sonnet"]["enabled"] = False
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config(config).resolve("default")

    def test_a_disabled_escalation_route_falls_back_to_standard_work(self):
        config = routing_config()["routing"]
        config["routes"]["opus-4-6-1m"]["enabled"] = False
        resolved = RoutingPolicy.from_config(config).resolve("escalation")
        self.assertEqual((resolved["name"], resolved["served_as"]),
                         ("luna-6-max", "default"))

    def test_the_organizations_rule_escalates_by_capability_and_role(self):
        self.assertEqual(self.policy.class_for("architecture", "anyone"),
                         "escalation")
        self.assertEqual(self.policy.class_for("verification", "auditor"),
                         "escalation")
        self.assertEqual(self.policy.class_for("verification", "reviewer"),
                         "default")
        self.assertEqual(self.policy.class_for("research", "investigator"),
                         "default")

    def test_a_policy_that_contradicts_itself_is_refused(self):
        base = routing_config()["routing"]
        for change, message in (
                ({"default": "nowhere"}, "not declared"),
                ({"escalation": None, "escalate": ["architecture"]},
                 "no escalation route"),
                ({"fallback": ["nowhere"]}, "not declared"),
                ({"routes": {}}, "at least one route")):
            config = dict(base, **change)
            with self.assertRaises(RoutingError) as raised:
                RoutingPolicy.from_config(config)
            self.assertIn(message, str(raised.exception))
        broken = routing_config()["routing"]
        broken["routes"]["luna-6-max"]["provider_args"] = [
            "-c", "roots=['{somewhere_else}']"]
        with self.assertRaises(RoutingError) as raised:
            RoutingPolicy.from_config(broken)
        self.assertIn("{somewhere_else}", str(raised.exception))

    def test_a_placeholder_no_session_can_fill_is_refused_however_spelled(
            self):
        """F-TF-28: a name in braces that is not one of the four a session
        fills would stay in the arguments as literal text. It is refused
        whatever its case, digits or hyphens; a provider's own braces are
        not placeholders."""
        def policy(argument):
            section = routing_config()["routing"]
            section["routes"]["luna-6-max"]["provider_args"] = [
                "-c", argument]
            return RoutingPolicy.from_config(section)

        for token in ("{Workspace}", "{WORKSPACE}", "{gate-pending}",
                      "{workspace2}", "{repoGit}", "{unknown}"):
            with self.assertRaises(RoutingError) as raised:
                policy("roots=['%s']" % token)
            self.assertIn(token, str(raised.exception))
        route = policy("roots=['{workspace}', '{gate_pending}',"
                       " '{gate_pending_real}', '{repo_git}']").route(
                           "luna-6-max")
        self.assertEqual(len(route["provider_args"]), 2)
        policy("table={a=1, b='x y'}")
        policy("empty={}")

    def test_the_description_names_every_route_and_what_it_serves(self):
        described = self.policy.describe()
        classes = {r["name"]: r["classes"] for r in described["routes"]}
        self.assertEqual(classes, {
            "luna-6-max": ["default"], "opus-4-6-1m": ["escalation"],
            "sonnet": ["fallback"], "haiku": ["utility"]})
        self.assertEqual(described["escalate"],
                         ["architecture", "verification:auditor"])


class CatalogRoutingTests(unittest.TestCase):
    """How configuration routes the specialists in the catalog."""

    def test_a_specialist_takes_the_default_route_unless_it_names_one(self):
        pool = build_pool(routing_config(specialists={
            "alpha": {"offers": ["research:investigator"]},
            "beta": {"offers": ["research:investigator"],
                     "route": "sonnet"},
            "gamma": {"offers": ["research:investigator"],
                      "agent": "claude-code", "model": "haiku"}}))
        routed = {p["handle"]: (p["provider"], p["model"], p["effort"],
                                p["route_name"]) for p in pool.profiles()}
        self.assertEqual(routed, {
            "alpha": ("codex", "gpt-6-luna", "max", "luna-6-max"),
            "beta": ("claude-code", "sonnet", None, "sonnet"),
            "gamma": ("claude-code", "haiku", None, None)})
        self.assertEqual(pool.profile("alpha")["provider_args"], LUNA_ARGS)

    def test_a_specialist_is_the_same_specialist_on_any_route(self):
        """Specialist identity is a function of the handle. Two
        organizations configured with different default routes hold the
        same specialists."""
        on_sonnet = build_pool(routing_config("sonnet"))
        on_luna = build_pool(routing_config("luna-6-max"))
        self.assertEqual(
            [p["specialist_id"] for p in on_sonnet.profiles()],
            [p["specialist_id"] for p in on_luna.profiles()])
        self.assertEqual(on_luna.profile("alpha")["specialist_id"],
                         specialist_identity("alpha"))
        self.assertNotEqual(on_sonnet.profile("alpha")["model"],
                            on_luna.profile("alpha")["model"])

    def test_a_catalog_that_cannot_be_routed_is_refused(self):
        offers = {"offers": ["research:investigator"]}
        with self.assertRaises(ConfigError) as raised:
            build_pool({"specialists": {"alpha": dict(offers)}})
        self.assertIn("needs an agent", str(raised.exception))
        with self.assertRaises(ConfigError) as raised:
            build_pool({"specialists": {
                "alpha": dict(offers, route="luna-6-max")}})
        self.assertIn("no [routing] is configured", str(raised.exception))
        with self.assertRaises(ConfigError) as raised:
            build_pool(routing_config(specialists={
                "alpha": dict(offers, route="sonnet", agent="codex")}))
        self.assertIn("routed one way", str(raised.exception))
        with self.assertRaises(ConfigError) as raised:
            build_pool(routing_config(specialists={
                "alpha": dict(offers, route="nowhere")}))
        self.assertIn("nowhere", str(raised.exception))
        config = routing_config(specialists={
            "alpha": dict(offers, route="sonnet")})
        config["routing"]["routes"]["sonnet"]["enabled"] = False
        with self.assertRaises(ConfigError) as raised:
            build_pool(config)
        self.assertIn("disabled", str(raised.exception))

    def test_a_catalog_without_a_policy_routes_as_it_always_did(self):
        pool = build_pool({"specialists": {"alpha": {
            "agent": "claude-code", "model": "sonnet",
            "offers": ["research:investigator"]}}})
        self.assertIsNone(pool.routing)
        profile = pool.profile("alpha")
        self.assertEqual((profile["provider"], profile["model"],
                          profile["route_name"], profile["provider_args"]),
                         ("claude-code", "sonnet", None, []))


# ================================================ the ten routing proofs

class SwitchingTwoTask(TwoTaskPolicy):
    """Work A by an outside specialist, retained; then work B from the
    Taskforce. The organization replaces its default execution route when
    it decides work B."""

    def __init__(self, switch):
        TwoTaskPolicy.__init__(self, "retain")
        self.switch = switch
        self.switched = False

    def plan(self, view):
        proposals = TwoTaskPolicy.plan(self, view)
        if not self.switched and view.authority["sequences"] and any(
                p["class"] == "INVESTIGATE" for p in proposals):
            self.switch()
            self.switched = True
        return proposals


class SwitchingRetry(RetryPolicy):
    """One investigation whose first attempt the provider fails. The
    organization replaces its default route when it orders the repair."""

    def __init__(self, switch):
        RetryPolicy.__init__(self, mode="same_member")
        self.switch = switch
        self.switched = False

    def plan(self, view):
        proposals = RetryPolicy.plan(self, view)
        if not self.switched and any(
                p["class"] == "REPAIR_TASK" for p in proposals):
            self.switch()
            self.switched = True
        return proposals


class RouteReplacementTests(RoutingTestCase):
    """The organization starts on Sonnet and moves to Luna 6 Max."""

    DEFAULT = "sonnet"

    def _two_attempts(self):
        """Attempt 1 on Sonnet fails at the provider; the same specialist
        does attempt 2 of the SAME work on Luna 6 Max."""
        self.transport.scripts = {"investigator": [
            provider_failure("usage window exhausted"), result()]}
        final = self._run(SwitchingRetry(self._switch_to("luna-6-max")))
        self.assertEqual(final["active_objectives"], [])
        return self._assignments()

    def _retained_then_reused(self):
        """Work A on Sonnet by an outside specialist, retained; work B on
        Luna 6 Max by the same specialist through its retained seat."""
        self.transport.scripts = {"investigator": [result(), result()]}
        final = self._run(SwitchingTwoTask(self._switch_to("luna-6-max")))
        self.assertEqual(final["active_objectives"], [])
        return self._assignments()

    def test_task_contract_identity_does_not_change_with_the_route(self):
        """One requirement, one TaskContractRecord: attempted on Sonnet,
        then on Luna 6 Max, it is the same record under the same sequence;
        and compiling the same requirement in an organization routed
        either way yields the same contract digest."""
        first, second = self._two_attempts()
        records = self._records()
        self.assertEqual(len(records), 1)
        self.assertEqual(first["sequence_id"], second["sequence_id"])
        self.assertEqual(self._route_of(first)["model"], "sonnet")
        self.assertEqual(self._route_of(second)["model"], "gpt-6-luna")
        record = json.loads(self.rt.store.get(self.rt.db.conn.execute(
            "SELECT content_address FROM records").fetchone()[
                "content_address"]))
        text = json.dumps(record)
        for word in ("sonnet", "gpt-6-luna", "codex", "claude-code",
                     "luna-6-max", "effort"):
            self.assertNotIn(word, text)
        body = json.loads(self._rows(
            "SELECT body FROM proposals WHERE class = 'INVESTIGATE'")[0][
                "body"])
        _rd, digest, _rh = compute_tiers(
            compile_proposal(body, self.env, self.clock))
        self.assertEqual(digest, records[0]["contract_digest"])

    def test_assignment_identity_does_not_depend_on_the_route(self):
        """A ProjectAssignment is identified by the attempt it staffs —
        the sequence and the attempt ordinal — and by nothing about how
        that attempt is executed."""
        first, second = self._two_attempts()
        sequence_id = first["sequence_id"]
        self.assertEqual(first["assignment_id"],
                         assignment_identity(sequence_id, 1))
        self.assertEqual(second["assignment_id"],
                         assignment_identity(sequence_id, 2))
        self.assertEqual(second["specialist_id"], first["specialist_id"])

    def test_the_route_may_change_between_attempts_of_the_same_work(self):
        """What changes with the route is exactly the execution: a new
        AttemptContract, a new ExecutionManifest, a new call. Each
        assignment keeps the route its attempt was dispatched on."""
        first, second = self._two_attempts()
        attempts = self._attempts()
        self.assertEqual([a["state"] for a in attempts],
                         ["FAILED", "VERIFIED"])
        self.assertNotEqual(attempts[0]["attempt_contract_hash"],
                            attempts[1]["attempt_contract_hash"])
        self.assertNotEqual(first["attempt_id"], second["attempt_id"])
        self.assertNotEqual(first["call_id"], second["call_id"])
        self.assertEqual(self._route_of(first), dict(
            SONNET, selected_by="agent_factory"))
        self.assertEqual(self._route_of(second), dict(
            LUNA, selected_by="agent_factory"))
        self.assertNotEqual(
            self.transport.launch_log[0]["manifest_address"],
            self.transport.launch_log[-1]["manifest_address"])
        self.assertEqual(
            json.loads(first["execution"])["route"],
            dict(SONNET, name="sonnet", served_as="default",
                 provider_args=[]))
        self.assertEqual(
            json.loads(second["execution"])["route"],
            dict(LUNA, name="luna-6-max", served_as="default",
                 provider_args=LUNA_ARGS))
        self.assertEqual((first["provider"], first["model"]),
                         ("claude-code", "sonnet"))
        self.assertEqual((second["provider"], second["model"]),
                         ("codex", "gpt-6-luna"))
        # The manifest names the provider and the model (its schema is
        # shared with the execution substrate and unchanged); the route's
        # effort and session arguments travel beside it.
        self.assertEqual(
            self._manifest(second["call_id"])["provider_assignment"],
            {"provider": "codex", "model": "gpt-6-luna",
             "selected_by": "agent_factory"})
        self.assertEqual(self.transport.session[second["call_id"]],
                         {"effort": "max", "args": LUNA_ARGS})
        self.assertNotIn(first["call_id"], self.transport.session_args)
        self.assertEqual(self.transport.session_args[second["call_id"]],
                         LUNA_ARGS)

    def test_membership_identity_does_not_depend_on_the_route(self):
        """The membership earned on Sonnet is the membership the
        specialist holds on Luna 6 Max: same row, same identity, a
        function of the specialist alone."""
        first, _second = self._retained_then_reused()
        memberships = self._memberships()
        self.assertEqual(len(memberships), 1)
        self.assertEqual(memberships[0]["membership_id"],
                         membership_identity(first["specialist_id"]))
        self.assertEqual(memberships[0]["status"], "ACTIVE")
        specialist = self._rows(
            "SELECT * FROM specialists WHERE specialist_id = ?",
            (first["specialist_id"],))[0]
        self.assertEqual((specialist["provider"], specialist["model"]),
                         ("codex", "gpt-6-luna"))
        self.assertEqual(specialist["specialist_id"],
                         specialist_identity(specialist["handle"]))

    def test_seat_identity_does_not_depend_on_the_route(self):
        first, second = self._retained_then_reused()
        seats = self._seats()
        self.assertEqual(len(seats), 1)
        self.assertEqual(seats[0]["seat_id"], seat_identity(
            first["specialist_id"], "research", "investigator"))
        self.assertEqual(second["seat_id"], seats[0]["seat_id"])
        self.assertIsNone(first["seat_id"])

    def test_demonstrations_stay_with_the_specialist_across_routes(self):
        """The demonstration recorded for work done on Sonnet is still
        that specialist's, bound by the retention decision, after the
        route changes; the work done on Luna 6 Max adds a second one to
        the same specialist and the same seat."""
        first, second = self._retained_then_reused()
        earned, later = self._demonstrations()
        self.assertEqual(earned["assignment_id"], first["assignment_id"])
        self.assertEqual(later["assignment_id"], second["assignment_id"])
        self.assertEqual(
            {earned["specialist_id"], later["specialist_id"]},
            {first["specialist_id"]})
        self.assertEqual((earned["outcome"], later["outcome"]),
                         ("QUALIFYING", "QUALIFYING"))
        self.assertEqual(later["seat_id"], self._seats()[0]["seat_id"])
        text = json.dumps([earned, later])
        for word in ("sonnet", "gpt-6-luna", "codex", "claude-code"):
            self.assertNotIn(word, text)
        retained = self._rows(
            "SELECT * FROM retention_decisions WHERE kind = 'RETAIN'")[0]
        self.assertEqual(json.loads(retained["demonstration_ids"]),
                         [earned["demonstration_id"]])

    def test_a_retained_member_does_later_work_on_a_different_route(self):
        """First participation outside, on Sonnet; later participation as
        a retained member the Taskforce resolved, on Luna 6 Max. Same
        specialist; a new record, a new attempt and full verification."""
        first, second = self._retained_then_reused()
        self.assertEqual(second["specialist_id"], first["specialist_id"])
        self.assertEqual(
            (first["selection_path"], first["decided_by"]),
            ("OUTSIDE_SPECIALIST", "PROJECT_MANAGER"))
        self.assertEqual(
            (second["selection_path"], second["decided_by"]),
            ("RETAINED_SEAT", "TASKFORCE"))
        self.assertEqual(self._route_of(first)["model"], "sonnet")
        self.assertEqual(self._route_of(second), dict(
            LUNA, selected_by="agent_factory"))
        self.assertNotEqual(second["sequence_id"], first["sequence_id"])
        self.assertEqual([a["state"] for a in self._attempts()],
                         ["VERIFIED", "VERIFIED"])
        verified = self.rt.db.conn.execute(
            "SELECT attempt_id, COUNT(*) AS n FROM attempt_verifications"
            " WHERE outcome = 'PASS' GROUP BY attempt_id").fetchall()
        self.assertEqual(len(verified), 2)
        self.assertEqual(verified[0]["n"], verified[1]["n"])


class RouteAcrossAStopTests(RoutingTestCase):
    """F-TF-28. The route of an attempt is recorded with its assignment
    before the ledger binds the call to a provider, and it is what a
    re-entry repeats — session arguments included — whatever the routing
    policy says when the process comes back."""

    def _start(self):
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())

    def _stop_in(self, target, name):
        restore = _fail_once(target, name)
        with self.assertRaises(_Boom):
            for _ in range(8):
                self.driver.step()
        restore()
        return self._assignments()[0]

    def _come_back_routed(self, config):
        self.pool = build_pool(config)
        self._restart(TwoTaskPolicy("none"), "engineer-b",
                      runner=QueueRunner())
        for _ in range(30):
            self.driver.step()
            if self.transport.launch_log:
                return self._assignments()[0]
        raise AssertionError("the attempt was not relaunched")

    def test_a_stop_after_dispatch_relaunches_on_the_recorded_route(self):
        """The ledger has dispatched the attempt to one provider; the
        process stops before the assignment is bound to the call; the
        organization changes its default route; the process comes back.
        The session is launched on the route the attempt was dispatched
        on."""
        self._start()
        row = self._stop_in(self.driver.taskforce, "_bind_slot")
        dispatched = self.rt.db.conn.execute(
            "SELECT a.state, c.provider FROM attempts a JOIN attempt_calls"
            " c ON c.attempt_id = a.attempt_id").fetchone()
        self.assertEqual((dispatched["state"], dispatched["provider"]),
                         ("DISPATCHED", "codex"))
        # Not bound to its call yet — and already holding its route.
        self.assertEqual((row["status"], row["call_id"]),
                         ("SELECTED", None))
        recorded = dict(LUNA, name="luna-6-max", served_as="default",
                        provider_args=LUNA_ARGS)
        self.assertEqual(json.loads(row["execution"])["route"], recorded)
        self.assertEqual(self.transport.launch_log, [])

        row = self._come_back_routed(routing_config("sonnet"))
        self.assertEqual(self._route_of(row), dict(
            LUNA, selected_by="agent_factory"))
        self.assertEqual(self.transport.session[row["call_id"]],
                         {"effort": "max", "args": LUNA_ARGS})
        self.assertEqual(self.transport.session_provider[row["call_id"]],
                         "codex")
        self.assertEqual((row["provider"], row["model"]),
                         ("codex", "gpt-6-luna"))
        self.assertEqual(json.loads(row["execution"])["route"], recorded)
        # Work staffed after the change runs on the new default.
        self.driver.run_until_stable()
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        later = self._assignments()[1]
        self.assertEqual(self._route_of(later), dict(
            SONNET, selected_by="agent_factory"))

    def test_a_relaunch_repeats_the_recorded_session_arguments(self):
        """Dispatched and bound, never launched; the operator edits the
        route's session arguments; the process comes back. The relaunch
        of that attempt carries the arguments it was dispatched with; the
        next attempt carries the new ones."""
        self._start()
        row = self._stop_in(self.transport, "launch_manifest")
        self.assertEqual(row["status"], "DISPATCHED")
        self.transport.session.clear()
        edited = routing_config()
        edited["routing"]["routes"]["luna-6-max"]["provider_args"] = [
            "--sandbox", "read-only"]
        relaunched = self._come_back_routed(edited)
        self.assertEqual(relaunched["call_id"], row["call_id"])
        self.assertEqual(self.transport.session[row["call_id"]],
                         {"effort": "max", "args": LUNA_ARGS})
        self.driver.run_until_stable()
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        later = self._assignments()[1]
        self.assertEqual(self.transport.session[later["call_id"]]["args"],
                         ["--sandbox", "read-only"])
        self.assertEqual(
            json.loads(later["execution"])["route"]["provider_args"],
            ["--sandbox", "read-only"])


class LunaDefaultTests(RoutingTestCase):
    """The organization as it is configured for production."""

    def test_an_outside_specialist_executes_on_luna_6_max(self):
        """The Project Manager selects an outside specialist; routing,
        downstream of that decision, puts the session on Luna 6 Max. The
        assignment creates no membership."""
        self.transport.scripts = {"investigator": [result()]}

        class OneTask(ChiefEngineerPolicy):
            def plan(self, view):
                oid = view.objectives[0]["objective_id"]
                if not view.authority["sequences"]:
                    return [investigate(view, oid, "A")]
                return []

        self._run(OneTask())
        assignment = self._assignments()[0]
        self.assertEqual(assignment["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(assignment["outcome"], "VERIFIED_ACCEPTED")
        self.assertEqual(self._route_of(assignment), dict(
            LUNA, selected_by="agent_factory"))
        self.assertEqual(
            self.transport.session_args[assignment["call_id"]], LUNA_ARGS)
        self.assertEqual(self._memberships(), [])

    def test_the_engineer_stays_on_opus_whatever_specialists_run_on(self):
        """The Engineer's model is its own configuration. No specialist
        route is the Engineer's, and changing the specialists' default
        changes nothing about it."""
        reports = [preflight.routing_report(
            build_pool(routing_config(default)), ENGINEER_MODEL)
            for default in ("luna-6-max", "sonnet")]
        for report in reports:
            self.assertEqual(report["engineer"], {
                "provider": "claude-code", "model": ENGINEER_MODEL,
                "roles": ["ENGINEER", "PROJECT_MANAGER"]})
            self.assertEqual(report["problems"], [])
        self.assertEqual(reports[0]["resolved"]["default"]["model"],
                         "gpt-6-luna")
        self.assertEqual(reports[1]["resolved"]["default"]["model"],
                         "sonnet")
        self.assertEqual(reports[0]["resolved"]["escalation"]["model"],
                         ENGINEER_MODEL)
        missing = preflight.routing_report(
            build_pool(routing_config()), None)
        self.assertIn("the Engineer's model is not configured",
                      missing["problems"])


class IndependentReviewOnOneRouteTests(RoutingTestCase):
    SPECIALISTS = {
        "alpha": {"offers": ["research:investigator",
                             "verification:reviewer"]},
        "beta": {"offers": ["verification:reviewer"]},
    }

    def test_an_independent_review_is_a_distinct_session_on_luna(self):
        """The work and its independent review both run on Luna 6 Max.
        Independence is a property of the staffing and of the execution —
        a different specialist, a different call, a different session —
        not of the model."""
        self.transport.scripts = {"investigator": [result()],
                                  "reviewer": [result()]}
        final = self._run(WorkGraphPolicy())
        self.assertEqual(final["active_objectives"], [])
        work, review = self._assignments()
        self.assertEqual((work["role"], review["role"]),
                         ("investigator", "reviewer"))
        self.assertNotEqual(review["specialist_id"], work["specialist_id"])
        self.assertNotEqual(review["call_id"], work["call_id"])
        self.assertNotEqual(review["attempt_id"], work["attempt_id"])
        sessions = self.transport.sessions
        self.assertNotEqual(
            sessions[review["call_id"]]["native_session_id"],
            sessions[work["call_id"]]["native_session_id"])
        for assignment in (work, review):
            self.assertEqual(self._route_of(assignment), dict(
                LUNA, selected_by="agent_factory"))


# ============================================================ escalation

def escalated(view, objective_id, sequence_id, handle, escalation,
              **extra):
    p = assign(view, objective_id, sequence_id, handle, **extra)
    p["staffing"]["escalation"] = escalation
    p["rationale"] = ("the defect spans the whole dispatch path and needs"
                      " deeper reasoning than standard work")
    return p


class EscalatingPolicy(ChiefEngineerPolicy):
    """One investigation. The Project Manager selects its specialist and
    asks for escalated execution of that one assignment."""

    def __init__(self, escalation="deep_reasoning"):
        self.escalation = escalation

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        sequences = view.authority["sequences"]
        if not sequences:
            return [investigate(view, oid, "E")]
        proposals = [escalated(view, oid, need["sequence_id"], "alpha",
                               self.escalation)
                     for need in view.projects[oid]["needs_staffing"]]
        if not proposals and all(s["state"] == "COMPLETED"
                                 for s in sequences) \
                and view.projects[oid]["workstreams"]:
            proposals.append(complete(view, oid))
        return proposals


class EscalationTests(RoutingTestCase):
    def test_the_project_manager_escalates_one_assignment(self):
        """The Project Manager asks for deeper reasoning on one
        assignment, in its own words. Routing resolves the ask to the
        escalation route. The specialist, the assignment identity and the
        TaskContractRecord are what they would have been without it."""
        self.transport.scripts = {"investigator": [result()]}
        final = self._run(EscalatingPolicy("deep_reasoning"))
        self.assertEqual(final["active_objectives"], [])
        assignment = self._assignments()[0]
        self.assertEqual(assignment["specialist_id"], self.alpha)
        self.assertEqual(assignment["assignment_id"], assignment_identity(
            assignment["sequence_id"], 1))
        execution = json.loads(assignment["execution"])
        self.assertEqual(execution["escalation"], "deep_reasoning")
        self.assertEqual(execution["route"], dict(
            OPUS, name="opus-4-6-1m", served_as="escalation",
            provider_args=[]))
        self.assertEqual(self._route_of(assignment), dict(
            OPUS, selected_by="agent_factory"))
        decision = self._rows("SELECT * FROM assignment_decisions")[0]
        self.assertEqual((decision["mode"], decision["escalation"],
                          decision["status"]),
                         ("outside_specialist", "deep_reasoning",
                          "APPLIED"))
        proposal = self._rows(
            "SELECT * FROM proposals WHERE class = 'ASSIGN_SPECIALIST'")[0]
        self.assertIn("deeper reasoning", proposal["rationale"])
        body = json.loads(self._rows(
            "SELECT body FROM proposals WHERE class = 'INVESTIGATE'")[0][
                "body"])
        _rd, digest, _rh = compute_tiers(
            compile_proposal(body, self.env, self.clock))
        self.assertEqual(digest, self._records()[0]["contract_digest"])
        # The Engineer sees that the assignment was escalated, in its own
        # word, and nothing about how.
        project = self._projects()[0]["project_id"]
        from intelligence import query
        state = query.project_state(self.plan, self.driver.ro, project,
                                    store=self.rt.store)
        shown = state["workstreams"][0]["assignments"][0]
        self.assertEqual(shown["escalation"], "deep_reasoning")
        text = json.dumps(_without_routing(state))
        for word in ("opus", "claude-code", "codex", "gpt-6-luna",
                     "luna-6-max"):
            self.assertNotIn(word, text)

    def test_only_the_escalated_assignment_leaves_the_default_route(self):
        self.transport.scripts = {"investigator": [result(), result()]}

        class OneOfTwo(ChiefEngineerPolicy):
            def plan(self, view):
                oid = view.objectives[0]["objective_id"]
                sequences = view.authority["sequences"]
                if not sequences:
                    return [investigate(view, oid, "A"),
                            investigate(view, oid, "B")]
                needs = view.projects[oid]["needs_staffing"]
                proposals = []
                for index, need in enumerate(sorted(
                        needs, key=lambda n: n["sequence_id"])):
                    if index == 0 and len(needs) == 2:
                        proposals.append(escalated(
                            view, oid, need["sequence_id"], "alpha",
                            "high_context"))
                    elif len(needs) == 2:
                        proposals.append(assign(
                            view, oid, need["sequence_id"], "beta"))
                return proposals

        self._run(OneOfTwo())
        by_specialist = {a["specialist_id"]: a for a in self._assignments()}
        self.assertEqual(self._route_of(by_specialist[self.alpha])["model"],
                         ENGINEER_MODEL)
        self.assertEqual(self._route_of(by_specialist[self.beta]), dict(
            LUNA, selected_by="agent_factory"))
        self.assertNotIn("escalation", json.loads(
            by_specialist[self.beta]["execution"]))

    def test_the_organizations_rule_escalates_a_kind_of_work(self):
        self.pool = build_pool(routing_config(
            escalate=["architecture"],
            specialists={"alpha": {"offers": [
                "architecture:investigator"]}}))
        self.transport.scripts = {"investigator": [result()]}

        class Architecture(ChiefEngineerPolicy):
            def plan(self, view):
                oid = view.objectives[0]["objective_id"]
                if not view.authority["sequences"]:
                    return [investigate(view, oid, "A",
                                        capability="architecture")]
                return []

        self._run(Architecture())
        assignment = self._assignments()[0]
        self.assertEqual(self._route_of(assignment), dict(
            OPUS, selected_by="agent_factory"))
        self.assertNotIn("escalation", json.loads(assignment["execution"]))
        self.assertEqual(json.loads(assignment["execution"])["route"][
            "served_as"], "escalation")

    def test_escalation_is_refused_where_no_route_can_resolve_it(self):
        """Asked of an organization that declares no escalation route, the
        decision is refused with the reason, and the work is not silently
        run at standard depth under it."""
        config = routing_config()
        del config["routing"]["escalation"]
        self.pool = build_pool(config)
        self.transport.scripts = {"investigator": [result()]}
        self._run(EscalatingPolicy("deep_reasoning"))
        refused = self._rows(
            "SELECT * FROM proposals WHERE class = 'ASSIGN_SPECIALIST'")
        self.assertTrue(refused)
        self.assertEqual({p["status"] for p in refused}, {"REJECTED"})
        self.assertIn("no escalation route", refused[0]["note"])
        self.assertEqual(self._rows("SELECT * FROM assignment_decisions"),
                         [])

    def test_an_escalation_is_a_word_about_the_need_never_a_model(self):
        for escalation in ESCALATION_CLASSES:
            for staffing in ({"mode": "taskforce"},
                             {"mode": "same_member"},
                             {"mode": "outside_specialist",
                              "specialist_id": self.alpha}):
                validate_staffing(dict(staffing, escalation=escalation),
                                  "ASSIGN_SPECIALIST")
        for staffing in (
                {"mode": "taskforce", "escalation": "opus"},
                {"mode": "taskforce", "escalation": ENGINEER_MODEL},
                {"mode": "taskforce", "escalation": True},
                {"mode": "taskforce", "model": ENGINEER_MODEL},
                {"mode": "taskforce", "provider": "claude-code"},
                {"mode": "taskforce", "route": "opus-4-6-1m"},
                {"mode": "taskforce", "effort": "max"}):
            with self.assertRaises(ProposalError):
                validate_staffing(staffing, "ASSIGN_SPECIALIST")
        for word in ESCALATION_CLASSES:
            self.assertIn(word, INSTRUCTIONS)
        for word in ("opus", "sonnet", "haiku", "luna", "codex", "gpt-"):
            self.assertNotIn(word, INSTRUCTIONS.lower())


# ================================================ supervision of Codex

WINDOWS = os.name == "nt"


def system_shell():
    """The shell a Codex command request arrives wrapped in on this
    platform: (program, the flag that introduces the script)."""
    if WINDOWS:
        return (os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                             "System32", "WindowsPowerShell", "v1.0",
                             "powershell.exe"), "-Command")
    return ("/bin/bash", "-lc")


def shell_command(script, program=None, flag=None):
    """What Codex sends as ``command``: the shell's argv joined with
    POSIX quoting, whatever the shell is."""
    default_program, default_flag = system_shell()
    return " ".join(shlex.quote(word) for word in (
        program or default_program, flag or default_flag, script))


def codex_request(script="git add docs/TARGET.md", cwd=None,
                  method=supervision.CODEX_COMMAND, **extra):
    native = {"method": method, "kind": "command",
              "command": shell_command(script), "cwd": cwd,
              "commandActions": [{"type": "unknown", "command": script}]}
    native.update(extra)
    return {"kind": "approval_required", "request_id": "approval_x",
            "provider": "codex", "native_request": native}


def make_link(link, target):
    """A directory link (a junction on Windows, which needs no
    privilege; a symbolic link elsewhere). False where the platform will
    not make one."""
    try:
        if WINDOWS:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, ImportError, AttributeError):
        return False
    return True


# The two permission requests production raised (Pilot 4, 2026-11-18,
# call_43514ac8729b53c3102727c3835433e4), as AutoSwarm recorded them. Only
# the worktree path differs here: that worktree is gone.
PRODUCTION_REQUESTS = (
    {"method": "item/commandExecution/requestApproval", "kind": "command",
     "itemId": "exec-3b3c7af7-2add-467e-88b8-80b88a8c9181",
     "environmentId": "local",
     "command": "\"C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0"
                "\\\\powershell.exe\" -Command 'git add docs/architecture"
                "/orchestration/RUNBOOK.md'",
     "commandActions": [{"type": "unknown", "command":
                         "git add docs/architecture/orchestration"
                         "/RUNBOOK.md"}],
     "proposedExecpolicyAmendment": [
         "git", "add", "docs/architecture/orchestration/RUNBOOK.md"],
     "availableDecisions": ["accept", "cancel"]},
    {"method": "item/commandExecution/requestApproval", "kind": "command",
     "itemId": "exec-01d66394-97dc-468f-a7ff-98a5d626a16e",
     "environmentId": "local",
     "command": "\"C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0"
                "\\\\powershell.exe\" -Command 'git commit -m \"docs:"
                " update orchestration runbook\"'",
     "commandActions": [{"type": "unknown", "command":
                         "git commit -m \"docs: update orchestration"
                         " runbook\""}],
     "proposedExecpolicyAmendment": [
         "git", "commit", "-m", "docs: update orchestration runbook"],
     "availableDecisions": ["accept", "cancel"]},
)


class CodexSupervisionTests(unittest.TestCase):
    """A Codex session runs inside its sandbox and asks only to leave
    it. The policy grants one kind of exit — staging and committing the
    frozen scope of a SOURCE_MODIFYING assignment — and judges the
    command that would run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.workspace = self.repo / "autobot-wt-x"
        self.registry = self.repo / ".git" / "worktrees" / "autobot-wt-x"
        self.outside = root / "elsewhere"
        for path in (self.workspace / "docs", self.registry, self.outside):
            path.mkdir(parents=True)
        self.pointer = self.workspace / ".git"
        self.pointer.write_text("gitdir: %s\n" % self.registry.as_posix(),
                                encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _context(self, finalization_class="SOURCE_MODIFYING",
                 scope=("docs/TARGET.md",), repo=True):
        return supervision.assignment_context(
            {"finalization_class": finalization_class,
             "allowed_scope": {"paths": list(scope)},
             "evidence_requirements": []},
            str(self.workspace),
            repo_path=str(self.repo) if repo else None)

    def _decide(self, script="git add docs/TARGET.md", context=None,
                **request):
        request.setdefault("cwd", str(self.workspace))
        return supervision.decide(codex_request(script, **request),
                                  context or self._context())

    def _allowed(self, script, **kwargs):
        decision = self._decide(script, **kwargs)
        self.assertEqual(decision["decision"], "allow",
                         (script, decision["reason"]))
        return decision

    def _denied(self, script, **kwargs):
        decision = self._decide(script, **kwargs)
        self.assertEqual(decision["decision"], "deny", script)
        return decision

    def test_staging_and_committing_the_frozen_scope_may_leave(self):
        for script in ("git add docs/TARGET.md",
                       "git add -- docs/TARGET.md",
                       "git add -A -- docs/TARGET.md",
                       "git add ./docs/TARGET.md",
                       'git commit -m "route check"',
                       "git commit -m 'route check (a; b & c | d > e)'",
                       'git commit -q -m "one" -m "two"',
                       'git commit --message="route check"',
                       'git commit -m "route check" -- docs/TARGET.md'):
            decision = self._allowed(script)
            self.assertEqual(decision["tool"], "codex.command")
            self.assertEqual(decision["subject"], script)
        if WINDOWS:
            self._allowed("git add docs\\TARGET.md")

    @unittest.skipUnless(
        WINDOWS and os.path.normcase(os.environ.get("SystemRoot", ""))
        == os.path.normcase(r"C:\Windows"),
        "the recorded requests name Windows PowerShell under C:\\Windows")
    def test_the_requests_production_raised_are_still_granted(self):
        context = self._context(
            scope=("docs/architecture/orchestration/RUNBOOK.md",))
        for recorded, command in zip(PRODUCTION_REQUESTS,
                                     ("add", "commit")):
            native = dict(recorded, cwd=str(self.workspace))
            decision = supervision.decide(
                {"kind": "approval_required", "provider": "codex",
                 "native_request": native}, context)
            self.assertEqual(decision["decision"], "allow",
                             decision["reason"])
            self.assertEqual(decision["subject"],
                             recorded["commandActions"][0]["command"])
            self.assertIn("git %s" % command, decision["reason"])

    def test_one_command_means_one_command(self):
        scripts = [
            "git status && curl https://example.invalid/collect",
            "git add docs/TARGET.md; git push",
            "git add docs/TARGET.md | Out-Null",
            "git add docs/TARGET.md > out.txt",
            "git add docs/TARGET.md & calc",
            'git commit -m "$(whoami)"',
            'git commit -m "`whoami`"',
            "git commit -m $env:SECRET",
            "git add (Get-Item docs/TARGET.md)",
            "& git add docs/TARGET.md",
            "git add docs/TARGET.md\ngit push",
            "git add docs/TARGET.md # note",
            "git commit -m 'a''b'",
            'git commit -m "a""b"',
            "git commit -m 'unterminated",
            u"git commit -m \u201cmessage\u201d",
        ]
        if WINDOWS:
            # One PowerShell argument, three for the program it starts.
            scripts.append("git commit -m 'x\" --amend \"y'")
            scripts.append('git commit -m "ends with a backslash\\"')
        for script in scripts:
            decision = self._denied(script)
            self.assertIn("only two commands", decision["reason"])

    def test_only_git_add_and_git_commit(self):
        for script in ("git status --porcelain",
                       "git push origin HEAD:main",
                       "git -C .. add docs/TARGET.md",
                       "git --git-dir=../.git commit -m x",
                       "git update-ref refs/heads/main HEAD",
                       "git reset --hard",
                       "git rm docs/TARGET.md",
                       "git restore --staged docs/TARGET.md",
                       "git mv docs/TARGET.md docs/MOVED.md",
                       "C:/tools/git.exe add docs/TARGET.md",
                       "./git add docs/TARGET.md",
                       "Remove-Item -Recurse ..",
                       'python -c "print(1)"'):
            decision = self._denied(script)
            self.assertIn("only two commands", decision["reason"])

    def test_git_add_stages_the_frozen_scope_and_nothing_else(self):
        for script in ("git add .",
                       "git add -A",
                       "git add docs",
                       "git add docs/OTHER.md",
                       "git add docs/TARGET.md src/other.py",
                       "git add ../elsewhere/x.md",
                       "git add docs/../src/x.py",
                       "git add /docs/TARGET.md",
                       "git add C:/docs/TARGET.md",
                       "git add :/",
                       "git add 'docs/*.md'",
                       "git add -f docs/TARGET.md",
                       "git add --pathspec-from-file=list.txt",
                       "git add -- -A"):
            self._denied(script)
        whole_directory = self._context(scope=("docs",))
        for script in ("git add docs", "git add -A -- docs/",
                       "git add docs/sub/new.md"):
            self._allowed(script, context=whole_directory)
        self._denied("git add src/x.py", context=whole_directory)
        self._denied("git add docs/TARGET.md",
                     context=self._context(scope=()))

    def test_git_commit_takes_a_message_and_nothing_that_widens_it(self):
        for script in ("git commit",
                       "git commit -m",
                       "git commit -a -m x",
                       "git commit -am x",
                       "git commit --amend -m x",
                       "git commit -m x --no-verify",
                       "git commit -F message.txt",
                       "git commit -m x src/other.py",
                       "git commit -m x -- src/other.py"):
            self._denied(script)

    def test_the_request_is_the_system_shell_running_that_command(self):
        program, flag = system_shell()
        script = "git add docs/TARGET.md"

        def decide(command, actions="same"):
            native = {"method": supervision.CODEX_COMMAND,
                      "command": command, "cwd": str(self.workspace)}
            if actions == "same":
                actions = [{"type": "unknown", "command": script}]
            if actions is not None:
                native["commandActions"] = actions
            return supervision.decide(
                {"kind": "approval_required", "native_request": native},
                self._context())["decision"]

        self.assertEqual(decide(shell_command(script)), "allow")
        # The argv itself, and a request with no description, are the
        # same command.
        self.assertEqual(decide([program, flag, script]), "allow")
        self.assertEqual(decide(shell_command(script), actions=None),
                         "allow")
        # Not a shell running one script.
        self.assertEqual(decide(script), "deny")
        self.assertEqual(decide(["git", "add", "docs/TARGET.md"]), "deny")
        self.assertEqual(decide([program, flag, script, "more"]), "deny")
        self.assertEqual(decide([program, "-File", script]), "deny")
        self.assertEqual(decide([program, "-EncodedCommand", script]),
                         "deny")
        self.assertEqual(decide("%s '%s" % (program, script)), "deny")
        self.assertEqual(decide(None), "deny")
        # A shell the session could have put there.
        planted = self.workspace / os.path.basename(program)
        self.assertEqual(decide([str(planted), flag, script]), "deny")
        self.assertEqual(decide([str(self.outside / "bash"), "-lc",
                                 script]), "deny")
        if WINDOWS:
            self.assertEqual(decide(["powershell.exe", flag, script]),
                             "deny")
        # The description must be the command that would run.
        self.assertEqual(decide(shell_command(script), actions=[
            {"type": "unknown", "command": script},
            {"type": "unknown", "command": "git push"}]), "deny")
        self.assertEqual(decide(shell_command(script), actions=[
            {"type": "unknown", "command": "git status"}]), "deny")
        self.assertEqual(decide(shell_command(script), actions="git add"),
                         "deny")

    def test_a_command_runs_from_the_worktree_root_or_not_at_all(self):
        decision = self._denied("git add docs/TARGET.md",
                                cwd=str(self.outside))
        self.assertIn("workspace", decision["reason"])
        self._denied("git add docs/TARGET.md", cwd=None)
        self._denied("git add docs/TARGET.md",
                     cwd=str(self.workspace / "docs"))
        # Another spelling of the root is the root.
        self._allowed("git add docs/TARGET.md",
                      cwd=str(self.workspace / "docs" / ".."))

    def test_a_link_out_of_the_worktree_is_not_the_worktree(self):
        inside_link = self.workspace / "docs" / "link"
        alias = Path(self.tmp.name) / "alias"
        if not (make_link(inside_link, self.outside)
                and make_link(alias, self.workspace)):
            self.skipTest("this platform would not create a directory"
                          " link")
        # Spelled inside the workspace, somewhere else on disk.
        decision = self._denied("git add docs/TARGET.md",
                                cwd=str(inside_link))
        self.assertIn("workspace", decision["reason"])
        # Spelled somewhere else, the worktree root on disk.
        self._allowed("git add docs/TARGET.md", cwd=str(alias))

    def test_the_worktree_is_the_one_the_repository_made(self):
        self._allowed("git add docs/TARGET.md")
        # A pointer relative to the worktree is the same registry entry.
        self.pointer.write_text(
            "gitdir: ../.git/worktrees/autobot-wt-x\n", encoding="utf-8")
        self._allowed("git add docs/TARGET.md")
        # A Git directory the session made for itself.
        own = self.workspace / "docs" / "gitdir"
        own.mkdir()
        for text in ("gitdir: %s\n" % own.as_posix(),
                     "gitdir: %s\n" % (self.repo / ".git").as_posix(),
                     "gitdir: %s\n" % (self.registry / "deeper").as_posix(),
                     "not a pointer\n"):
            self.pointer.write_text(text, encoding="utf-8")
            decision = self._denied("git add docs/TARGET.md")
            self.assertIn("worktree", decision["reason"])
        self.pointer.unlink()
        self._denied("git add docs/TARGET.md")
        self.pointer.mkdir()
        self._denied("git add docs/TARGET.md")
        self.pointer.rmdir()
        self.pointer.write_text("gitdir: %s\n" % self.registry.as_posix(),
                                encoding="utf-8")
        self._allowed("git add docs/TARGET.md")
        # Without a repository to anchor the worktree to, nothing leaves.
        self._denied("git add docs/TARGET.md",
                     context=self._context(repo=False))

    def test_a_read_only_assignment_never_leaves_the_sandbox(self):
        for finalization_class in ("READ_ONLY",):
            decision = self._decide(
                context=self._context(finalization_class))
            self.assertEqual(decision["decision"], "deny")
            self.assertIn("no command outside the sandbox",
                          decision["reason"])
        governed = supervision.assignment_context(
            {"finalization_class": "READ_ONLY", "allowed_scope": {},
             "evidence_requirements": ["COMMAND_GOVERNANCE"]},
            str(self.workspace))
        self.assertTrue(governed["shell_allowed"])
        self.assertEqual(self._decide(context=governed)["decision"],
                         "deny")

    def test_file_changes_and_permission_changes_are_denied(self):
        for method, tool in (
                (supervision.CODEX_FILE_CHANGE, "codex.fileChange"),
                (supervision.CODEX_PERMISSIONS, "codex.permissions")):
            decision = self._decide(method=method)
            self.assertEqual((decision["decision"], decision["tool"]),
                             ("deny", tool))

    def test_an_unknown_request_shape_is_still_denied(self):
        decision = supervision.decide(
            {"kind": "approval_required", "native_request": {
                "method": "item/somethingNew/requestApproval"}},
            self._context())
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("unrecognized", decision["reason"])


# ============================================================== preflight

class ProbeTransport:
    """A provider that answers the preflight task: one result per call,
    after one poll, with the model and effort it was launched with."""

    def __init__(self, workspace, answers=None, requests=None):
        self.workspace = workspace
        self.answers = answers or {}
        self.requests = requests or {}
        self.launched = []
        self.decided = []
        self.released = []
        self.polls = {}

    def providers(self):
        return {"codex": True, "claude-code": True, "opencode": False}

    def probe_route(self, route, prompt):
        call_id = "call_%032x" % (len(self.launched) + 1)
        self.launched.append((call_id, route, prompt))
        return {"call_id": call_id, "workspace": str(self.workspace)}

    def pending_requests(self, call_id):
        return self.requests.pop(call_id, [])

    def decide_request(self, call_id, request_id, decision, reason=""):
        self.decided.append((call_id, request_id, decision))

    def probe_result(self, call_id):
        self.polls[call_id] = self.polls.get(call_id, 0) + 1
        if self.polls[call_id] < 2:
            return None
        route = [r for c, r, _p in self.launched if c == call_id][0]
        return self.answers.get(call_id, {
            "outcome": "completed",
            "content": "PREFLIGHT # Autobot",
            "native_session_id": "session-" + call_id[-4:],
            "requested_selection": {"model": route["model"],
                                    "effort": route["effort"]}})

    def release_workspace(self, call_id):
        self.released.append(call_id)
        return True


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        (self.workspace / "README.md").write_text("# Autobot\n\ntext\n",
                                                  encoding="utf-8")
        self.pool = build_pool(routing_config())

    def tearDown(self):
        self.tmp.cleanup()

    def _probe(self, transport, reasoner=None):
        return preflight.probe_routes(
            self.pool, transport, ENGINEER_MODEL, reasoner=reasoner,
            timeout_seconds=5, poll_seconds=0, sleep=lambda _s: None)

    def test_the_report_resolves_every_route_and_every_specialist(self):
        report = preflight.routing_report(
            self.pool, ENGINEER_MODEL,
            {"codex": True, "claude-code": True})
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["resolved"]["default"], dict(
            LUNA, name="luna-6-max", served_as="default"))
        self.assertEqual(report["resolved"]["escalation"], dict(
            OPUS, name="opus-4-6-1m", served_as="escalation"))
        self.assertEqual(
            [(s["handle"], s["route"], s["model"], s["effort"])
             for s in report["specialists"]],
            [("alpha", "luna-6-max", "gpt-6-luna", "max"),
             ("beta", "luna-6-max", "gpt-6-luna", "max")])
        down = preflight.routing_report(
            self.pool, ENGINEER_MODEL, {"codex": False,
                                        "claude-code": True})
        self.assertEqual(down["problems"],
                         ["AutoSwarm cannot launch the provider 'codex'"])

    def test_a_probe_exercises_each_role_in_its_own_session(self):
        """A specialist and an independent reviewer on the default route
        (two sessions), one session on the escalation route, and one
        question to the Engineer's model."""
        transport = ProbeTransport(self.workspace)

        class Reasoner:
            last_meta = {"models": [ENGINEER_MODEL], "session_id": "eng-1"}

            def __call__(self, prompt):
                return "PREFLIGHT ENGINEER"

        records = self._probe(transport, Reasoner())
        self.assertEqual([r["label"] for r in records],
                         ["specialist", "independent reviewer",
                          "escalation", "engineer"])
        self.assertTrue(all(r["ok"] for r in records))
        specialist, reviewer, escalation, engineer = records
        self.assertEqual(specialist["route"], reviewer["route"])
        self.assertEqual(specialist["launched_with"],
                         {"model": "gpt-6-luna", "effort": "max"})
        self.assertEqual(escalation["launched_with"]["model"],
                         ENGINEER_MODEL)
        self.assertEqual(engineer["served_by"], [ENGINEER_MODEL])
        self.assertTrue(preflight.independent_sessions(records))
        # Every session ran on the READ_ONLY contract production work
        # gets, and its worktree was released.
        for _call, _route, prompt in transport.launched:
            self.assertIn("READ_ONLY EXECUTION CONTRACT", prompt)
        self.assertEqual(len(transport.released), 3)

    def test_a_probes_permission_requests_meet_the_real_policy(self):
        first = "call_%032x" % 1
        transport = ProbeTransport(self.workspace, requests={first: [
            {"kind": "approval_required", "request_id": "approval_read",
             "native_request": {"subtype": "can_use_tool",
                                "tool_name": "Read", "input": {
                                    "file_path": str(
                                        self.workspace / "README.md")}}},
            {"kind": "approval_required", "request_id": "approval_write",
             "native_request": {"subtype": "can_use_tool",
                                "tool_name": "Write", "input": {
                                    "file_path": str(
                                        self.workspace / "x.txt")}}}]})
        records = self._probe(transport)
        self.assertEqual(transport.decided, [
            (first, "approval_read", "allow"),
            (first, "approval_write", "deny")])
        self.assertEqual(records[0]["requests"], [
            {"tool": "Read", "decision": "allow"},
            {"tool": "Write", "decision": "deny"}])

    def test_a_route_that_does_not_answer_fails_the_preflight(self):
        second = "call_%032x" % 2
        transport = ProbeTransport(self.workspace, answers={second: {
            "outcome": "failed", "error": "usage window exhausted",
            "content": "", "native_session_id": None}})
        records = self._probe(transport)
        self.assertEqual([r["ok"] for r in records], [True, False, True])
        self.assertIn("usage window exhausted", records[1]["error"])
        self.assertFalse(preflight.independent_sessions(records))

        class Silent(ProbeTransport):
            def probe_result(self, call_id):
                return None

        ticks = iter(range(0, 1000, 3))
        records = preflight.probe_routes(
            self.pool, Silent(self.workspace), ENGINEER_MODEL,
            timeout_seconds=5, poll_seconds=0, sleep=lambda _s: None,
            clock=lambda: next(ticks))
        self.assertTrue(all(not r["ok"] for r in records))
        self.assertIn("no result within", records[0]["error"])


# ======================================================= store migration

class EscalationColumnMigrationTests(unittest.TestCase):
    def test_a_v2_store_gains_the_escalation_column_and_keeps_its_rows(
            self):
        """Schema v3 is additive: an existing organization's decisions
        are untouched and simply ask for no escalation."""
        from unittest.mock import patch
        import intelligence.planstore as planstore
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "intelligence.db"
            # A store as the v2 code left it: migrated to v2 and no
            # further, holding one staffing decision.
            with patch.object(planstore, "PLAN_SCHEMA_VERSION", 2):
                store = PlanStore(path)
                columns = [r[1] for r in store.conn.execute(
                    "PRAGMA table_info(assignment_decisions)")]
                self.assertNotIn("escalation", columns)
                with store.transaction() as cur:
                    cur.execute(
                        "INSERT INTO assignment_decisions (decision_id,"
                        " objective_id, project_id, sequence_id,"
                        " attempt_ordinal, mode, specialist_id, status,"
                        " decided_utc) VALUES ('prop:one', 'obj', NULL, ?,"
                        " 1, 'taskforce', NULL, 'OPEN',"
                        " '2026-11-18T00:00:00.000Z')",
                        ("sq_" + "1" * 32,))
                store.close()
            raw = sqlite3.connect(str(path))
            self.assertEqual(raw.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0], "2")
            raw.close()
            store = PlanStore(path)
            try:
                self.assertEqual(PLAN_SCHEMA_VERSION, 3)
                version = store.conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()["value"]
                self.assertEqual(int(version), PLAN_SCHEMA_VERSION)
                row = dict(store.conn.execute(
                    "SELECT * FROM assignment_decisions").fetchone())
                self.assertEqual((row["decision_id"], row["mode"],
                                  row["escalation"]),
                                 ("prop:one", "taskforce", None))
                with store.transaction() as cur:
                    store.record_assignment_decision(
                        cur, "prop:two", "obj", None, "sq_" + "2" * 32, 1,
                        "taskforce", None, "2026-11-18T00:00:01.000Z",
                        escalation="high_context")
                self.assertEqual(store.open_staffing_decision(
                    "sq_" + "2" * 32)["escalation"], "high_context")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
