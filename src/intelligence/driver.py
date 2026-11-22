"""The persistent loop driver.

    read ProjectState -> identify planning events -> invoke the Engineer
    -> validate typed proposals -> compile -> Registry admission ->
    project assignment (a retained member the Taskforce resolves, or an
    outside specialist the Project Manager selected) -> PromptProjection
    -> transport dispatch -> supervise -> collect outcomes -> registered
    authority arcs -> capability demonstrations -> wait for change ->
    repeat.

Restart safety is layered, never clever: the authority ledger is the
only truth (D-69 restart discipline); planning events are keyed by
journal seq; workforce events by their own identity; proposals converge
on deterministic identity and Registry DUPLICATE; reasoning is replayed
from its durable record; staffing converges on the assignment identity;
dispatch idempotency rides the Sequencer-minted call_id and the Transport
contract; witness and verification admissions are idempotent on content.
Killing this driver anywhere and starting a new one continues the same
loop without duplicating proposals, contracts, memberships, assignments
or specialist launches.

Stable means nothing is left to do AND nothing is still coming.
``run_until_stable`` is the bounded form the deterministic suites use: it
stops after consecutive quiet steps. ``run`` is the production form: a
quiet step while specialists are still executing is a WAIT, not
stability — the driver sleeps, looks again (answering permission requests
and collecting results as they arrive) and only reports a stable quiet
state once no attempt is in flight.

One driver reasons at a time (the plan store's driver lease). The
authority side needs no such guard: the Sequencer's instance lease and
single-writer transactions already fence it.
"""

import time
from datetime import timedelta

from autobot import project_state
from autobot.models import instant

from .engineer import EngineerService
from .coordinator import (
    CoordinatorService,
    PassthroughCoordinatorPolicy,
)
from .orchestrator import (
    OrchestratorService,
    OrchestrationApplicator,
    PassthroughOrchestratorPolicy,
)
from .workstream_bridge import workstream_to_proposal, node_to_proposal
from .planstore import readonly_view
from .taskforce import Taskforce


def _any_activity(planning, sweep, orchestration=None, coordinator=None):
    if coordinator and coordinator.get("admitted"):
        return True
    if orchestration and orchestration.get("had_activity"):
        return True
    if orchestration:
        for key in ("admitted_nodes", "admitted_workstreams",
                     "engineer_nodes"):
            for entry in (orchestration.get(key) or []):
                if isinstance(entry, dict) and entry.get("result") \
                        and entry["result"].get("sequence_id"):
                    return True
                if isinstance(entry, dict) and entry.get("decision_id"):
                    return True
    if planning["cycle_id"] is not None:
        return True
    for key in ("allocated", "adopted", "collected", "verified",
                "supervised", "settled"):
        if sweep.get(key):
            return True
    arcs = sweep["arcs"]
    return any(arcs[key] for key in arcs)


class ContinuousDriver:
    def __init__(self, plan, registry, sequencer, store, environment,
                 clock, policy, transport, runner, pool,
                 authority_db_path, repo_path=None,
                 repository_context=None, holder="chief-engineer",
                 lease_seconds=3600, max_proposals_per_cycle=16,
                 freeze_prompts=False, established_threshold=3,
                 gate_root=None, isolate_read_only=False,
                 orchestrator_policy=None,
                 coordinator_policy=None):
        """``pool`` is the specialist catalog (``SpecialistPool``): the
        workers that exist, whether or not any is a Taskforce member.
        ``freeze_prompts`` makes every contract
        declare its composed specialist prompt as a specification input;
        ``isolate_read_only`` gives READ_ONLY specialists a frozen source
        revision in an isolated worktree, as SOURCE_MODIFYING ones always
        have. Both are the production settings; the deterministic suites
        that script provider witnesses leave them off."""
        self.plan = plan
        self.clock = clock
        self.holder = holder
        self.lease_seconds = lease_seconds
        self.ro = readonly_view(authority_db_path)
        self.taskforce = Taskforce(
            plan, sequencer, store, transport, runner, pool,
            clock, self.ro, repo_path=repo_path,
            repository_context=repository_context,
            established_threshold=established_threshold,
            gate_root=gate_root, isolate_read_only=isolate_read_only)
        self.engineer = EngineerService(
            plan, registry, environment, clock, policy,
            max_proposals_per_cycle=max_proposals_per_cycle,
            store=store if freeze_prompts else None,
            roster=self.taskforce.roster,
            repository_context=repository_context,
            evidence=store)
        # The attribute this service was reached by before the
        # organizational model was aligned.
        self.chief = self.engineer
        self.orchestrator = OrchestratorService(
            plan=plan,
            policy=orchestrator_policy or PassthroughOrchestratorPolicy(),
            clock=clock)
        self.coordinator = CoordinatorService(
            plan=plan,
            policy=coordinator_policy or PassthroughCoordinatorPolicy(),
            clock=clock)
        self._acquire()
        # The organization the Engineer reasons over exists before its
        # first cycle: mirror the specialist catalog now.
        self.taskforce.roster.sync_pool(instant(self.clock.now()))

    def _acquire(self):
        now_dt = self.clock.now()
        self.plan.acquire_driver_lease(
            self.holder, instant(now_dt),
            instant(now_dt + timedelta(seconds=self.lease_seconds)))

    def _renew_authority_lease(self):
        """Keep the Sequencer's instance lease alive across a long run.
        The lease is renewed only once less than half of it remains —
        each renewal is a journaled authority fact, so a loop that waits
        for hours renews a handful of times, not once per look."""
        sequencer = self.taskforce.sequencer
        if getattr(sequencer, "epoch", None) is None:
            return
        row = self.ro.execute(
            "SELECT holder, epoch, expires_utc FROM instance_lease"
            " WHERE lease_key = 'sequencer'").fetchone()
        if row is None or row["holder"] != sequencer.holder \
                or row["epoch"] != sequencer.epoch:
            return  # not ours to renew; the next transition will say so
        threshold = instant(self.clock.now()
                            + timedelta(seconds=self.lease_seconds / 2.0))
        if row["expires_utc"] <= threshold:
            sequencer.renew_lease(ttl_seconds=self.lease_seconds)

    def close(self):
        self.plan.release_driver_lease(self.holder)
        self.ro.close()

    # -------------------------------------------------------------- steps

    def step(self):
        """One loop iteration: Coordinator admission, an orchestration
        cycle, a planning cycle (when something new woke the planner),
        and one Taskforce sweep. Returns the step report; ``quiet``
        means the step changed nothing and proposed nothing."""
        self._acquire()  # renew; refuses if another driver took over
        self._renew_authority_lease()
        now = instant(self.clock.now())
        coordinator_report = self._coordinator_admit(now)
        orchestration = self._run_orchestration_cycles(now)
        self._repair_incomplete_bindings(now)
        engineer_nodes = self._realize_engineer_nodes(now)
        admitted = self._admit_ready_workstreams(now)
        node_admitted = self._admit_ready_nodes(now)
        if engineer_nodes:
            orchestration["engineer_nodes"] = engineer_nodes
        if admitted:
            orchestration["admitted_workstreams"] = admitted
        if node_admitted:
            orchestration["admitted_nodes"] = node_admitted
        planning = self.engineer.run_cycle(self.ro, project_state, now)
        sweep = self.taskforce.advance(now)
        released = self._release_accepted_dependencies(now)
        edge_releases = self._release_orchestration_edges(now)
        return {"coordinator": coordinator_report,
                "orchestration": orchestration,
                "planning": planning, "sweep": sweep,
                "dependency_releases": released,
                "edge_releases": edge_releases,
                "quiet": not _any_activity(
                    planning, sweep, orchestration=orchestration,
                    coordinator=coordinator_report)}

    def _coordinator_admit(self, now):
        """Admit ACTIVE objectives that have no current OrchestrationRun.

        For each unadmitted objective:
        1. CoordinatorService.admit_objective() → decision + run
        2. create_initial_node() for DIRECT and ENGINEER_ELEVATED
        3. ORCHESTRATOR_ELEVATED leaves node creation to the Orchestrator

        When the PassthroughCoordinatorPolicy is active, skip admission
        entirely — the existing Engineer path handles everything.

        Returns a report of admissions performed."""
        if isinstance(self.coordinator.policy,
                      PassthroughCoordinatorPolicy):
            return {}
        admissions = []
        for objective in self.plan.active_objectives():
            oid = objective["objective_id"]
            existing_runs = self.plan.active_orchestration_runs(oid)
            if existing_runs:
                continue
            existing_proposals = self.plan.proposals_by_status(
                "ADMITTED", "DUPLICATE", "QUEUED_FOR_YOU",
                "WAITING", "APPLIED")
            has_prior_work = any(
                p.get("objective_id") == oid
                for p in existing_proposals)
            if has_prior_work:
                continue
            try:
                result = self.coordinator.admit_objective(oid, now)
                if not result.get("new"):
                    continue
                run = result.get("run")
                if run is None:
                    continue
                node = self.coordinator.create_initial_node(
                    run, objective, now)
                admissions.append({
                    "objective_id": oid,
                    "decision_id": result["decision"]["decision_id"],
                    "run_id": run["orchestration_id"],
                    "mode": run["mode"],
                    "initial_node": node["node_id"] if node else None,
                })
            except Exception as exc:
                admissions.append({
                    "objective_id": oid,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                })
        return {"admitted": admissions} if admissions else {}

    def _run_orchestration_cycles(self, now):
        """Run the Orchestrator per active ORCHESTRATOR_ELEVATED run.

        Each run gets its own scoped view and a fresh applicator so
        decisions from simultaneous objectives never land in the wrong
        run."""
        combined = {
            "orchestrator_cycle": True,
            "decisions": [],
            "had_activity": False,
            "delegate_to_engineer": [],
        }
        for run in self.plan.active_orchestration_runs():
            if run["mode"] != "ORCHESTRATOR_ELEVATED":
                continue
            run_id = run["orchestration_id"]
            obj = self.plan.objective(run["objective_id"])
            if obj is None:
                continue
            run_nodes = self.plan.orchestration_nodes(run_id)
            run_edges = []
            run_bindings = []
            run_outputs = []
            for nd in run_nodes:
                run_edges.extend(
                    self.plan.edges_for_node(nd["node_id"], "inbound"))
                ab = self.plan.authority_binding_for_node(nd["node_id"])
                if ab:
                    run_bindings.append(ab)
                run_outputs.extend(
                    self.plan.outputs_for_node(nd["node_id"]))
            orch = self.orchestrator.run_cycle(
                objectives=[obj],
                projects=self.plan.projects(run["objective_id"]),
                taskforce_state=self.taskforce.roster.snapshot(),
                runs=[run], nodes=run_nodes, edges=run_edges,
                authority_bindings=run_bindings,
                node_outputs=run_outputs)
            if orch.get("decisions") and \
                    not orch.get("already_applied"):
                applicator = OrchestrationApplicator(
                    self.plan, orchestration_id=run_id,
                    ro=self.ro)
                applied = applicator.apply(orch["decisions"], now)
                orch["applied"] = applied
                if hasattr(self.plan,
                           "persist_orchestration_application"):
                    self.plan.persist_orchestration_application(
                        orch.get("input_digest"), applied)
            combined["decisions"].extend(
                orch.get("decisions", []))
            if orch.get("had_activity"):
                combined["had_activity"] = True
            combined["delegate_to_engineer"].extend(
                orch.get("delegate_to_engineer", []))
        return combined

    def _realize_engineer_nodes(self, now):
        """Realize ENGINEER_REASONING nodes through the Engineer.

        When the Coordinator creates an ENGINEER_ELEVATED run, the
        initial node has realization_kind=ENGINEER_REASONING. This
        method detects pending ENGINEER_REASONING nodes, invokes the
        Engineer's reasoning, stores the decision as the node's output,
        and completes the node.

        Returns a list of realized nodes."""
        results = []
        for run in self.plan.active_orchestration_runs():
            if run["mode"] != "ENGINEER_ELEVATED":
                continue
            run_id = run["orchestration_id"]
            objective = self.plan.objective(run["objective_id"])
            if objective is None:
                continue
            nodes = self.plan.orchestration_nodes(
                run_id, status="PENDING")
            for node in nodes:
                if node.get("realization_kind") != "ENGINEER_REASONING":
                    continue
                binding = self.plan.engineering_binding_for_node(
                    node["node_id"])
                if binding is not None and binding.get("adopted_utc"):
                    continue
                from .proposals_util import canonical_json_digest
                import hashlib
                req_id = "er_" + canonical_json_digest({
                    "node": node["node_id"],
                    "run": run_id,
                })[:32]
                question = node.get("description") or objective["text"]
                if binding is None:
                    with self.plan.transaction() as cur:
                        self.plan.create_engineering_binding(
                            cur, req_id, node["node_id"], run_id,
                            question, now)
                try:
                    policy = self.engineer.policy
                    if hasattr(policy, "reasoner") and \
                            policy.reasoner is not None:
                        response = policy.reasoner(question)
                        content = response.encode("utf-8") \
                            if isinstance(response, str) \
                            else response
                        content_sha = hashlib.sha256(
                            content).hexdigest()
                        decision_id = "ed_" + content_sha[:32]
                        with self.plan.transaction() as cur:
                            self.plan.adopt_engineering_decision(
                                cur, req_id, decision_id,
                                content_sha, now)
                        output_id = "out_" + canonical_json_digest({
                            "node": node["node_id"],
                            "decision": decision_id,
                        })[:32]
                        with self.plan.transaction() as cur:
                            self.plan.record_node_output(
                                cur, output_id, node["node_id"],
                                "ENGINEERING_DECISION", now,
                                content_sha256=content_sha,
                                engineering_decision_id=decision_id)
                            self.plan.update_node_status(
                                cur, node["node_id"],
                                "COMPLETED", now)
                        results.append({
                            "node_id": node["node_id"],
                            "decision_id": decision_id,
                            "content_sha256": content_sha,
                        })
                except Exception as exc:
                    results.append({
                        "node_id": node["node_id"],
                        "error": "%s: %s" % (
                            type(exc).__name__, exc),
                    })
        return results

    def _repair_incomplete_bindings(self, now):
        """One-time repair: backfill missing identity tiers on existing
        authority bindings. Runs during the step that detects them."""
        import json as _json
        for run in self.plan.active_orchestration_runs():
            for node in self.plan.orchestration_nodes(
                    run["orchestration_id"]):
                binding = self.plan.authority_binding_for_node(
                    node["node_id"])
                if binding is None:
                    continue
                if binding.get("contract_digest") and \
                        binding.get("requirement_digest"):
                    continue
                seq_id = binding.get("sequence_id")
                if not seq_id:
                    continue
                updates = {}
                seq_row = self.ro.execute(
                    "SELECT contract_digest FROM sequences"
                    " WHERE sequence_id = ?",
                    (seq_id,)).fetchone()
                if seq_row and not binding.get("contract_digest"):
                    updates["contract_digest"] = seq_row[
                        "contract_digest"]
                rh = binding.get("record_hash")
                if rh and not binding.get("requirement_digest"):
                    rec = self.ro.execute(
                        "SELECT content_address FROM records"
                        " WHERE record_hash = ?",
                        (rh,)).fetchone()
                    if rec:
                        try:
                            payload = _json.loads(
                                self.taskforce.store.get(
                                    rec["content_address"]
                                ).decode("utf-8"))
                            from contract_library.digests import \
                                requirement_digest as _rd
                            updates["requirement_digest"] = _rd(payload)
                        except Exception:
                            pass
                if updates:
                    with self.plan.transaction() as cur:
                        self.plan.update_authority_binding(
                            cur, binding["binding_id"], **updates)

    def _release_accepted_dependencies(self, now):
        """When a predecessor sequence is accepted, release the
        corresponding project_workstream_dependency and bind the
        accepted artifact address. Returns list of releases."""
        releases = []
        for project in self.plan.active_projects():
            for ws in self.plan.workstreams(project["project_id"]):
                deps = self.plan.dependencies_for_workstream(
                    ws["workstream_id"])
                for dep in deps:
                    if dep.get("released_utc") is not None:
                        continue
                    source_ws = self.plan.generalized_workstream(
                        dep["source_workstream_id"])
                    if source_ws is None:
                        continue
                    source_proposal = self.plan.proposal(
                        source_ws["proposal_id"])
                    if source_proposal is None:
                        continue
                    seq_id = source_proposal.get("sequence_id")
                    if not seq_id:
                        continue
                    seq_row = self.ro.execute(
                        "SELECT state FROM sequences WHERE sequence_id = ?",
                        (seq_id,)).fetchone()
                    if seq_row is None:
                        continue
                    if seq_row["state"] not in ("ACCEPTED", "COMPLETED"):
                        continue
                    acceptance = self.ro.execute(
                        "SELECT acceptance_sha256 FROM released_edges"
                        " WHERE predecessor_sequence_id = ? LIMIT 1",
                        (seq_id,)).fetchone()
                    artifact_addr = None
                    if acceptance:
                        artifact_addr = acceptance["acceptance_sha256"]
                    with self.plan.transaction() as cur:
                        self.plan.release_workstream_dependency(
                            cur, dep["source_workstream_id"],
                            dep["target_workstream_id"],
                            now, artifact_addr)
                    releases.append({
                        "source": dep["source_workstream_id"],
                        "target": dep["target_workstream_id"],
                        "artifact": artifact_addr,
                    })
        return releases

    def _admit_ready_workstreams(self, now):
        """Find Orchestrator-created workstreams that have no authority
        sequence yet, build proposals via the bridge, compile and admit.

        Returns a list of admission results, empty when nothing was ready."""
        results = []
        for project in self.plan.active_projects():
            objective = self.plan.objective(project["objective_id"])
            if objective is None:
                continue
            for ws in self.plan.workstreams(project["project_id"]):
                if ws.get("work_profile") is None:
                    continue
                existing = self.plan.workstream_by_proposal(
                    ws["proposal_id"])
                if existing is None:
                    continue
                proposal_row = self.plan.proposal(ws["proposal_id"])
                if proposal_row and proposal_row.get("sequence_id"):
                    continue
                deps = self.plan.dependencies_for_workstream(
                    ws["workstream_id"])
                all_released = all(
                    d.get("released_utc") is not None for d in deps)
                if deps and not all_released:
                    continue
                pred_edges = []
                for dep in deps:
                    addr = dep.get("accepted_artifact_address")
                    if addr:
                        pred_edges.append({
                            "edge_id": "on-" + dep[
                                "source_workstream_id"][:16],
                            "edge_kind": "RECORD",
                            "target": addr,
                            "observation_requirement": "ACCEPTANCE",
                        })
                proposal = workstream_to_proposal(
                    ws, objective["text"], project["project_id"],
                    predecessors=pred_edges if pred_edges else None)
                proposal["objective_id"] = project["objective_id"]
                try:
                    result = self.engineer._dispose_contract(
                        proposal, None, self.ro, now)
                    results.append({
                        "workstream_id": ws["workstream_id"],
                        "result": result})
                except Exception:
                    pass
        return results

    def _admit_ready_nodes(self, now):
        """Admit TASK_CONTRACT nodes whose predecessor authority identities
        are available. Nodes are admitted as BLOCKED in the authority
        plane when predecessors exist; t-s41 releases them when
        predecessors accept.

        Eligibility: every inbound edge's source node has an authority
        binding (so its identity tier is available for predecessor
        compilation). The orchestration edge may still be PENDING —
        authority readiness, not organizational release, gates admission.

        Returns a list of admission results."""
        results = []
        for run in self.plan.active_orchestration_runs():
            run_id = run["orchestration_id"]
            objective = self.plan.objective(run["objective_id"])
            if objective is None:
                continue
            nodes = self.plan.orchestration_nodes(
                run_id, status="PENDING")
            for node in nodes:
                if node.get("realization_kind") != "TASK_CONTRACT":
                    continue
                binding = self.plan.authority_binding_for_node(
                    node["node_id"])
                if binding is not None:
                    continue

                inbound = self.plan.edges_for_node(
                    node["node_id"], direction="inbound")
                all_bound = all(
                    self.plan.authority_binding_for_node(
                        e["source_node_id"]) is not None
                    for e in inbound)
                if not all_bound:
                    continue

                pred_edges = self._compile_node_predecessors(node)

                proposal = node_to_proposal(
                    node, objective["text"],
                    predecessors=pred_edges if pred_edges else None)
                proposal["objective_id"] = run["objective_id"]

                try:
                    result = self.engineer._dispose_contract(
                        proposal, None, self.ro, now)
                    if result and result.get("sequence_id"):
                        self._create_full_authority_binding(
                            node, result, now)
                    results.append({
                        "node_id": node["node_id"],
                        "result": result})
                except Exception as exc:
                    results.append({
                        "node_id": node["node_id"],
                        "error": "%s: %s" % (type(exc).__name__, exc),
                    })
        return results

    def _compile_node_predecessors(self, node):
        """Compile orchestration edges into authority predecessor edges.

        Uses the source node's authority binding to determine the
        correct identity tier (record_hash, contract_digest, or
        requirement_digest). Compiles from all inbound edges regardless
        of organizational release status — authority readiness is
        determined by binding availability, not edge RELEASED state."""
        pred_edges = []
        inbound = self.plan.edges_for_node(
            node["node_id"], direction="inbound")
        for edge in inbound:
            source_binding = self.plan.authority_binding_for_node(
                edge["source_node_id"])
            if source_binding is None:
                continue
            edge_kind = edge.get("authority_edge_kind") or "RECORD"
            if edge_kind == "RECORD" and source_binding.get("record_hash"):
                target = source_binding["record_hash"]
            elif edge_kind == "CONTRACT" and \
                    source_binding.get("contract_digest"):
                target = source_binding["contract_digest"]
            elif edge_kind == "REQUIREMENT" and \
                    source_binding.get("requirement_digest"):
                target = source_binding["requirement_digest"]
            elif source_binding.get("record_hash"):
                target = source_binding["record_hash"]
                edge_kind = "RECORD"
            else:
                continue
            pred_edges.append({
                "edge_id": edge["edge_id"],
                "edge_kind": edge_kind,
                "target": target,
                "observation_requirement": "ACCEPTANCE",
            })
        return pred_edges

    def _create_full_authority_binding(self, node, result, now):
        """Create a complete authority binding with all six identity tiers.

        Recovers every tier from the authority plane:
            proposal_id      — the intelligence-side proposal
            task_contract_id — from the TaskContractRecord payload
            requirement_digest — from the TaskContractRecord payload
            contract_digest  — from the TaskContractRecord payload
            record_hash      — from sequences
            sequence_id      — from admission result"""
        import json as _json
        from .proposals_util import canonical_json_digest
        sequence_id = result["sequence_id"]
        binding_id = "ab_" + canonical_json_digest({
            "node": node["node_id"],
            "sequence": sequence_id,
        })[:32]
        proposal_id = result.get("proposal_id")
        record_hash = None
        task_contract_id = None
        requirement_digest = None
        contract_digest = None
        seq_row = self.ro.execute(
            "SELECT record_hash, contract_digest"
            " FROM sequences WHERE sequence_id = ?",
            (sequence_id,)).fetchone()
        if seq_row:
            record_hash = seq_row["record_hash"]
            contract_digest = seq_row["contract_digest"]
        if record_hash:
            rec_row = self.ro.execute(
                "SELECT content_address FROM records"
                " WHERE record_hash = ?",
                (record_hash,)).fetchone()
            if rec_row:
                try:
                    addr = rec_row["content_address"]
                    payload = _json.loads(
                        self.taskforce.store.get(addr).decode("utf-8"))
                    task_contract_id = payload.get("task_contract_id")
                    if not contract_digest:
                        contract_digest = payload.get(
                            "contract_digest")
                    from contract_library.digests import \
                        requirement_digest as _rd
                    requirement_digest = _rd(payload)
                except Exception:
                    pass
        if not task_contract_id:
            task_contract_id = result.get("task_contract_id")
        if not contract_digest:
            proposal_row = self.plan.proposal(proposal_id)
            if proposal_row:
                contract_digest = proposal_row.get("contract_digest")
        with self.plan.transaction() as cur:
            self.plan.create_authority_binding(
                cur, binding_id, node["node_id"], now,
                proposal_id=proposal_id,
                task_contract_id=task_contract_id,
                requirement_digest=requirement_digest,
                contract_digest=contract_digest,
                record_hash=record_hash,
                sequence_id=sequence_id)
            self.plan.update_node_status(
                cur, node["node_id"], "EXECUTING")

    def _release_orchestration_edges(self, now):
        """Project authority acceptance onto orchestration edges.

        When a source node's authority sequence is accepted/completed,
        release its outbound orchestration edges, capture receipt
        provenance, and record the accepted node output."""
        releases = []
        for run in self.plan.active_orchestration_runs():
            run_id = run["orchestration_id"]
            for node in self.plan.orchestration_nodes(run_id):
                binding = self.plan.authority_binding_for_node(
                    node["node_id"])
                if binding is None or not binding.get("sequence_id"):
                    continue
                seq_id = binding["sequence_id"]
                seq_row = self.ro.execute(
                    "SELECT state FROM sequences WHERE sequence_id = ?",
                    (seq_id,)).fetchone()
                if seq_row is None:
                    continue
                if seq_row["state"] not in ("DEPENDENCIES_RELEASED",
                                             "COMPLETED"):
                    continue

                released_row = self.ro.execute(
                    "SELECT acceptance_sha256, release_sha256"
                    " FROM released_edges"
                    " WHERE predecessor_sequence_id = ? LIMIT 1",
                    (seq_id,)).fetchone()
                if released_row is None:
                    continue
                acc_sha = released_row["acceptance_sha256"]
                rel_sha = released_row["release_sha256"]

                outbound = self.plan.edges_for_node(
                    node["node_id"], direction="outbound")
                for edge in outbound:
                    if edge.get("status") != "PENDING":
                        continue
                    with self.plan.transaction() as cur:
                        self.plan.release_orchestration_edge(
                            cur, edge["edge_id"], now,
                            acceptance_receipt_sha256=acc_sha,
                            dependency_release_receipt_sha256=rel_sha)
                    releases.append({
                        "edge_id": edge["edge_id"],
                        "source_node": node["node_id"],
                        "acceptance": acc_sha,
                        "release": rel_sha,
                    })

                if node["status"] not in ("COMPLETED", "FAILED"):
                    artifact_sha = self._resolve_accepted_artifact(
                        seq_id, binding)
                    if artifact_sha:
                        from .proposals_util import canonical_json_digest
                        output_id = "out_" + canonical_json_digest({
                            "node": node["node_id"],
                            "artifact": artifact_sha,
                        })[:32]
                        output_kind = "RESEARCH_ARTIFACT" \
                            if node.get("work_profile") == "RESEARCH" \
                            else "ACCEPTED_ARTIFACT"
                        with self.plan.transaction() as cur:
                            self.plan.record_node_output(
                                cur, output_id, node["node_id"],
                                output_kind, now,
                                content_sha256=artifact_sha,
                                sequence_id=seq_id)
                    with self.plan.transaction() as cur:
                        self.plan.update_node_status(
                            cur, node["node_id"], "COMPLETED", now)
                        self.plan.update_authority_binding(
                            cur, binding["binding_id"],
                            status="COMPLETED")
        return releases

    def _resolve_accepted_artifact(self, sequence_id, binding):
        """Resolve the artifact from the specific accepted attempt.

        Uses the acceptance receipt to identify the exact attempt
        authority accepted, so retries/repairs don't substitute a
        different attempt's artifact. Falls back to the latest artifact
        when the receipt cannot be resolved."""
        try:
            accepted_attempt = None
            rel_row = self.ro.execute(
                "SELECT acceptance_sha256 FROM released_edges"
                " WHERE predecessor_sequence_id = ? LIMIT 1",
                (sequence_id,)).fetchone()
            if rel_row and rel_row["acceptance_sha256"]:
                try:
                    import json as _json
                    receipt_bytes = self.taskforce.store.get(
                        rel_row["acceptance_sha256"])
                    if receipt_bytes:
                        receipt = _json.loads(
                            receipt_bytes.decode("utf-8"))
                        accepted_attempt = receipt.get("attempt_id")
                except Exception:
                    pass
            if accepted_attempt:
                art_row = self.ro.execute(
                    "SELECT content_sha256 FROM attempt_artifacts"
                    " WHERE attempt_id = ? LIMIT 1",
                    (accepted_attempt,)).fetchone()
                if art_row:
                    return art_row["content_sha256"]
            row = self.ro.execute(
                "SELECT content_sha256 FROM attempt_artifacts"
                " WHERE sequence_id = ? ORDER BY rowid DESC LIMIT 1",
                (sequence_id,)).fetchone()
            return row["content_sha256"] if row else None
        except Exception:
            return None

    def _closing(self, steps, **extra):
        report = {
            "steps": len(steps),
            "reports": steps,
            "active_objectives": self.plan.active_objectives(),
            "waiting": self.plan.proposals_by_status(
                "WAITING", "QUEUED_FOR_YOU"),
            "in_flight": self.taskforce.in_flight(),
        }
        report.update(extra)
        return report

    def run_until_stable(self, max_steps=64, quiet_steps=2):
        """Repeat ``step`` until the loop is stable: ``quiet_steps``
        consecutive steps with no planning cycle and no Taskforce
        activity. Returns the closing report with what remains: active
        objectives and proposals waiting on YOUR registered authority.

        This form never waits: an attempt still executing at a provider
        simply leaves the loop quiet (``in_flight`` names it). ``run`` is
        the form that waits for it."""
        steps = []
        quiet = 0
        while len(steps) < max_steps:
            report = self.step()
            steps.append(report)
            quiet = quiet + 1 if report["quiet"] else 0
            if quiet >= quiet_steps:
                break
        else:
            raise RuntimeError(
                "no stable state within %d steps (runaway loop guard)"
                % max_steps)
        return self._closing(steps)

    def run(self, max_wall_seconds=3600.0, poll_seconds=2.0,
            quiet_steps=2, max_active_steps=512, sleep=None,
            on_step=None):
        """The production loop: step until a STABLE QUIET STATE — nothing
        left to do and nothing in flight.

        A quiet step with attempts still in flight is a wait: the driver
        sleeps ``poll_seconds`` and steps again, which is also what
        answers a specialist's permission requests and collects its
        result. ``max_wall_seconds`` bounds the whole run on the injected
        clock; ``max_active_steps`` is the runaway guard on steps that DID
        something (waiting does not count against it). ``sleep`` is
        injectable so the deterministic suites advance a manual clock
        instead of the wall. ``on_step`` observes each step report.

        Returns the closing report; ``stable`` says whether the quiet
        state was reached, ``timed_out`` whether the wall bound ended the
        run first (work then remains in flight and a later run resumes
        it)."""
        sleep = sleep or time.sleep
        started = self.clock.now()
        steps = []
        quiet = 0
        active = 0
        waited = 0
        while True:
            report = self.step()
            steps.append(report)
            if on_step is not None:
                on_step(report)
            if not report["quiet"]:
                quiet = 0
                active += 1
                if active > max_active_steps:
                    raise RuntimeError(
                        "no stable state within %d active steps (runaway"
                        " loop guard)" % max_active_steps)
                continue
            if self.taskforce.in_flight():
                quiet = 0
                elapsed = (self.clock.now() - started).total_seconds()
                if elapsed >= max_wall_seconds:
                    return self._closing(steps, stable=False,
                                         timed_out=True, waits=waited)
                waited += 1
                sleep(poll_seconds)
                continue
            quiet += 1
            if quiet >= quiet_steps:
                return self._closing(steps, stable=True, timed_out=False,
                                     waits=waited)
