"""The production Engineer policy: reasoning behind the seam.

``ReasoningEngineerPolicy`` is an ``EngineerPolicy``: ``PlanningView`` in,
typed proposals out. Between the two it renders a structured projection
of the view, asks a reasoning model what should happen next, and turns
the answer into typed proposals. It holds no authority handle and no
database handle; the model it calls is given no tools. Whatever the model
says, the only thing that leaves this module is a list of proposals that
passed ``validate_proposal`` — and the Registry, the Sequencer and the
Taskforce still decide what any of them is allowed to do.

The model answers in the Engineer's two roles. As ENGINEER it solves the
technical problem: what work exists and why, how it decomposes, what
depends on what, which findings need follow-up. As PROJECT MANAGER it
realizes the solution: it opens the project, and for each workstream it
either requests the capability from the Taskforce (which resolves a
retained member) or explicitly selects an outside specialist for the
project; it decides whether a specialist's verified work earns Taskforce
membership, and when the project is complete.

What is deterministic here, and what is reasoned.

- REASONED: the solution and the work it requires, the dependency graph,
  which capability each piece needs, who is on the project team, whether
  demonstrated work earns retention, who should repair, how the plan
  changes when verified findings arrive, and when the project is
  complete.
- DETERMINISTIC: the envelope of every proposal (the snapshot it reasoned
  over, the objective it serves), resolving the model's references to the
  identities in the view (a review target, a dependency or a workstream
  named by key becomes the frozen record edge or the sequence), refusing
  anything that names an identity the view does not contain — a
  specialist that does not exist or does not offer the work, a provider
  or a model anywhere — the order proposals are applied in, the F-CE-1
  completion guard, and one protocol reflex: work that reached your
  publication permit boundary is reported as WAIT_FOR_AUTHORITY without
  asking the model.

Not every stimulus needs reasoning. Routine progress the Engineer itself
set in motion (a contract admitted, work made ready, a specialist it just
assigned or retained) wakes the cycle but is answered without a model
call (``needs_reasoning``); findings, concluded or failed work,
capability demonstrations, a workstream waiting for a staffing decision
and anything waiting on a decision are reasoned over.

The reasoner is a callable ``prompt -> text``. ``ClaudeCliReasoner`` is
the production one: the subscription ``claude`` CLI in headless print
mode with every tool disabled — the same login the organization's
specialists run under, never a billed API key.
"""

import hashlib
import json
import shutil
import subprocess

from .capabilities import CAPABILITIES
from .engineer import EngineerPolicy
from .findings import FINDING_CLASSES
from .proposals import (
    CONTRACT_CLASSES,
    PROPOSAL_CLASSES,
    SEAT_CLASSES,
    WORKFORCE_CLASSES,
    WORKSTREAM_KEY_RE,
    ProposalError,
    validate_proposal,
)
from .query import completion_obligations_from_work
from .closure import can_complete_objective
from .routing import ESCALATION_CLASSES
from .workforce import RELEASE_REASONS, STAFFING_MODES

POLICY_ID = "reasoning-engineer/v1"

# Authority events that are routine progress of work the Engineer already
# decided: they wake the cycle and are consumed without a model call.
ROUTINE_EVENTS = frozenset((
    "contract_admitted", "admission_duplicate", "work_ready",
    "work_blocked", "authority_object_admitted", "sequence_verified",
    "sequence_committed", "sequence_accepted", "dependencies_released",
))
# Workforce events that echo a decision already taken or a staffing
# already made.
ROUTINE_WORKFORCE_EVENTS = frozenset((
    "outside_specialist_assigned", "retained_seat_assigned",
    "seat_retained", "seat_released", "seat_deactivated",
    "seat_established", "outside_specialist_released",
))

# The order a batch is applied in, whatever order the model wrote it in:
# the project exists before its workstreams, a retention is in force
# before work that should be resolved to the new member, and a project
# assignment follows the work it staffs.
_APPLY_ORDER = {"OPEN_PROJECT": 0, "RETAIN_SPECIALIST": 1,
                "RELEASE_SPECIALIST": 1, "CREATE_TASK": 2,
                "DECOMPOSE_TASK": 2, "INVESTIGATE": 2, "REQUEST_REVIEW": 2,
                "REPAIR_TASK": 2, "ASSIGN_SPECIALIST": 3,
                "WAIT_FOR_AUTHORITY": 4, "PROJECT_COMPLETE": 5}

SYSTEM_PROMPT = (
    "You are the Engineer of Autobot, an autonomous engineering"
    " organization. You solve technical problems, and when a solution"
    " needs coordinated work you act as its Project Manager. You answer"
    " with typed proposals only. You do not execute work, you do not"
    " choose providers or models, you do not certify your own conclusions"
    " and you cannot change authority state: specialists execute through"
    " AutoSwarm, registered verifiers verify, and the Registry and"
    " Sequencer own every authoritative decision. Answer with exactly one"
    " JSON object and nothing else."
)

INSTRUCTIONS = """\
You are given the structured state of the organization (PROJECTION,
below). Decide what should happen next and answer with typed proposals.

Your two roles
- As ENGINEER you solve the technical problem: investigate, develop the
  solution, weigh tradeoffs and determine the work it requires.
- When realizing the solution needs coordinated work you act as its
  PROJECT MANAGER: open the project (OPEN_PROJECT), lay out its
  workstreams and their dependencies, and compose a temporary team.

How to reason
- Work from the projection only. It holds the objectives, the current
  project (workstreams, dependencies, assignments, team, workstreams that
  need a staffing decision), findings from specialists, verified and
  accepted results, failed assertions, the Taskforce (members, retained
  seats and their availability, demonstrations), the outside specialists
  a project may draw on, and each objective's budget.
- Propose only what the objective needs. Each workstream must be one
  coherent engineering activity a single specialist can complete.
- Independent activities should be proposed together so they run
  concurrently. An activity that needs another's ACCEPTED result must
  declare the dependency; it then waits until authority releases it.
  When it starts, each predecessor's accepted result is in its working
  directory as a file under .autobot/inputs/ (named after the
  predecessor workstream), and a review is given the result it reviews
  the same way: write its guidance to use them.
- A finding is information. If it calls for follow-up that the objective
  needs, propose that work and cite the finding in provenance.findings.
  When verified findings change what the project should do, adjust the
  plan — add, repair or re-scope workstreams — and say why.
- Write guidance a specialist can carry out with what it has. A
  READ_ONLY specialist can only READ FILES, in a checkout of the
  repository at the latest accepted revision: no shell, no network, no
  writes. A SOURCE_MODIFYING specialist works in an isolated worktree,
  may run governed shell commands, and may change only its
  allowed_paths. Name the files to read and exactly what to establish.

The work, and who does it, are separate
- A work requirement names a capability and a role. It NEVER names a
  provider, a model or a specialist: it is frozen into the task contract.
- Who executes a workstream is a PROJECT ASSIGNMENT, decided by you as
  Project Manager on one of two paths:
  1. Request the capability from the Taskforce: propose the work and add
     nothing. The Taskforce resolves an available retained member that
     holds a seat for that capability and role (taskforce.retained_seats).
  2. Explicitly select an outside specialist: add an ASSIGN_SPECIALIST
     for that workstream naming the specialist_id
     (taskforce.outside_specialists lists who exists, what each offers,
     what each has verifiably demonstrated and whether it is busy).
- The Taskforce never brings an outside specialist onto a project. When
  no retained seat covers a workstream's capability and role — or its
  member is busy and the work should not wait — YOU select an outside
  specialist. A workstream under project.needs_staffing is waiting for
  exactly that decision. Staff a workstream in the same answer that
  creates it whenever no retained seat covers it.
- A specialist works on one assignment at a time: to run two workstreams
  concurrently, staff them with different people. The specialist the
  Taskforce resolves for a capability request is the holder of that
  retained seat (taskforce.retained_seats): do not also select that
  specialist as an outside specialist for work that should run at the
  same time.
- A review is staffed independently of whoever did the reviewed work:
  that specialist may not be put on it, by the Taskforce or by you
  (independent_of names who is excluded).
- Never name a provider or a model anywhere.
- Use only a (capability, role) pair listed in taskforce.staffable.

The project team is temporary; the Taskforce is persistent
- Working on a project does NOT make a specialist a Taskforce member. An
  outside specialist stays outside, however well the work went.
- taskforce.retention_candidates lists outside specialists whose
  verified, qualifying work no decision has considered yet. Decide
  explicitly, from the demonstrations:
  RETAIN_SPECIALIST when the demonstrated capability is useful to the
  organization — in particular when remaining or future work needs that
  capability again. Retention creates Taskforce membership and a
  retained seat; later capability requests are then resolved to that
  member by the Taskforce.
  RELEASE_SPECIALIST (reason "task_only") when the capability was needed
  once and is not needed again: the specialist leaves the project and no
  membership changes.
- After you retain a specialist, do NOT assign later work for that
  capability explicitly: request the capability and let the Taskforce
  resolve the retained member. A workstream that has not started and
  still carries your earlier explicit selection for a capability a
  member now holds should be handed back to the Taskforce:
  ASSIGN_SPECIALIST for it with {"mode": "taskforce"} (a new decision for
  a workstream replaces your earlier open one).
- A NOT_QUALIFYING demonstration or a failed verification calls for a
  repair decision (REPAIR_TASK on the same sequence, with a staffing
  mode) and possibly a release (reason "poor_performance").
- An attempt can fail before the specialist did any work. The workstream
  then lists it under failed_attempts with a provider_report — the
  execution substrate's own words (a usage window exhausted, a session
  that could not start) — and its demonstration carries the same report.
  That is a failure of the provider, not evidence about the specialist:
  release nobody for it. Authority returns the workstream to READY while
  its frozen retry budget lasts, and it runs again only on your word:
  REPAIR_TASK, normally with the staffing it had ({"mode": "taskforce"}
  for work a retained member did, {"mode": "same_member"} for an outside
  specialist's).
- retry_exhausted means the frozen contract allows that workstream no
  further attempt: the work can continue only as a new workstream.

Execution depth
- Specialists work at the organization's standard execution depth. That
  is right for almost all work, and you say nothing about it.
- When ONE assignment's engineering problem genuinely needs more, ask
  for it in that workstream's staffing decision with "escalation":
  "high_context" when the problem spans more of the system than standard
  work holds in view (architecture across many modules, a review of a
  large or subtle change), "deep_reasoning" when it needs deeper
  reasoning (a hard defect, a subtle design, a repair after standard
  work failed verification). Say in the rationale what about the problem
  requires it. The Taskforce resolves how the assignment is executed:
  you never name a provider or a model, and you are shown none.
- Escalation is scarce and slower. Do not ask for it for routine
  investigation, documentation or review, and never for every workstream
  of a project. A workstream shown with "escalation" already has it.

Completion
- Propose PROJECT_COMPLETE only when every workstream of the project is
  COMPLETED, nothing needs follow-up, and you propose no further work for
  that objective (a closing RETAIN_SPECIALIST or RELEASE_SPECIALIST may
  accompany it). Completing the project dissolves its team; Taskforce
  membership persists.
- When every workstream is COMPLETED and nothing more is needed, do not
  leave the project open: complete it.
- A review counts only when it examined the work. A review workstream
  shows "review_verdict": "EXAMINED" when the reviewer read the work and
  the source it must agree with, "NOT_EXAMINED" when the reviewer says it
  could not, and "NOT_STATED" when it did not say. A COMPLETED review
  that is NOT_EXAMINED or NOT_STATED means its verified result exists,
  not that the work was reviewed: the work is still unreviewed. Request
  another independent review of the same workstream (REQUEST_REVIEW with
  the same "review_of", under a new workstream key, with guidance that
  avoids what stopped the first one) before you complete the project.
  PROJECT_COMPLETE is refused while a reviewed workstream has only
  NOT_EXAMINED reviews.
- A workstream shown as REJECTED with no sequence_id was refused at
  admission and never became work: it is nothing to complete. Decide
  whether the objective still needs that work in another form (under a
  new workstream key) or not at all.
- If there is nothing to decide right now (work is in progress), answer
  with an empty proposals list.

Answer format — exactly one JSON object:
{"reasoning": "<two to four sentences: the technical solution or what
changed, and why these decisions>", "proposals": [ ... ]}

Every proposal has: "class", "objective_id", "rationale" (why this, now)
and may have "provenance": {"findings": [finding_id, ...],
"demonstrations": [demonstration_id, ...]}.

Classes and their extra fields:
- OPEN_PROJECT (Project Manager; once, before the first workstream of an
    objective): "project": {"title": "<short name>", "solution": "<the
    technical solution this project realizes: the approach, the tradeoff
    you weighed, the work it requires>"}
- INVESTIGATE (READ_ONLY investigation):
    "workstream": "<short-lowercase-key you choose, unique in the
      project>",
    "requirement": {"objective": "<what must be established>",
      "capability": "<capability>", "role": "<role>",
      "guidance": "<complete, self-contained instructions for the
        specialist: what to read, what to determine, what to report>",
      "subsystem": "<short subsystem name, optional>",
      "depends_on_workstreams": ["<key of a workstream ALREADY in the
        project>", ...]}
- CREATE_TASK: as INVESTIGATE, plus in the requirement
    "finalization_class": "READ_ONLY" | "SOURCE_MODIFYING", and for
    SOURCE_MODIFYING "allowed_paths": ["<exact repo-relative file>", ...]
    (only those files may change; keep it as narrow as the task).
- REQUEST_REVIEW (READ_ONLY review of other work): as INVESTIGATE, plus
    "review_of": {"workstream": "<key of the workstream to review>"}. The
    reviewed workstream must already be in the project; the review waits
    for its acceptance automatically.
- DECOMPOSE_TASK (several workstreams at once, with dependencies BETWEEN
    them): "parts": [{"part_id": "<short-lowercase-key: it is the
    workstream key>", "requirement": {as CREATE_TASK},
    "depends_on_parts": ["<earlier part_id>", ...]}, ...]
    A part with "review_of_part": "<earlier part_id>" is an independent
    READ_ONLY review of that part (its requirement is as INVESTIGATE): it
    waits, blocked, until the reviewed part is accepted, and it is staffed
    independently of whoever did the reviewed work. Use it to lay out
    work and its review in one decision.
- ASSIGN_SPECIALIST (Project Manager: who executes a workstream's next
    attempt): "workstream": "<key>",
    "staffing": {"mode": "outside_specialist", "specialist_id": "<id>"}
    (or {"mode": "taskforce"} to leave it to the Taskforce). Either form
    may add "escalation": one of %(escalation_classes)s (see Execution
    depth).
- REPAIR_TASK (another attempt of a workstream after a failed
    verification or a failed attempt): "workstream": "<key>", "guidance"
    (why this attempt),
    "staffing": {"mode": "taskforce" | "same_member" |
    "different_member"} or {"mode": "outside_specialist",
    "specialist_id": "<id>"}, each optionally with "escalation"
- RETAIN_SPECIALIST: "specialist_id", "capability", "role",
    "demonstrations": [demonstration_id, ...]
- RELEASE_SPECIALIST: "specialist_id", "capability", "role", "reason":
    one of %(release_reasons)s, "disposition": "RELEASE" | "DEACTIVATE"
- WAIT_FOR_AUTHORITY: "reason", "subject_id"
- PROJECT_COMPLETE: no extra fields

Capabilities: %(capabilities)s
Finding classes you may see: %(finding_classes)s
"""


class ReasonerError(Exception):
    """The reasoning model could not be asked, or did not answer."""


def _sha256(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def extract_json_object(text):
    """The single JSON object in a model's answer: the whole text, a
    fenced block, or the outermost braces. Returns None when there is
    none."""
    if not isinstance(text, str):
        return None
    candidates = [text.strip()]
    if "```" in text:
        for chunk in text.split("```")[1::2]:
            chunk = chunk.strip()
            if chunk.lower().startswith("json"):
                chunk = chunk[4:].strip()
            candidates.append(chunk)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class ClaudeCliReasoner:
    """Ask the subscription ``claude`` CLI, headless, with no tools.

    ``model`` comes from configuration (the organization's planner
    model); this class never picks one. The call spends a model turn and
    touches nothing: every tool is disabled, the session is not
    persisted, and the prompt travels on stdin."""

    def __init__(self, model, cli=None, timeout_seconds=900, cwd=None):
        if not (isinstance(model, str) and model):
            raise ReasonerError("the Engineer's model is not configured")
        self.model = model
        self.cli = cli or shutil.which("claude")
        self.timeout_seconds = timeout_seconds
        self.cwd = cwd
        self.last_meta = None

    def __call__(self, prompt):
        if not self.cli:
            raise ReasonerError("the `claude` CLI is not on PATH")
        argv = [self.cli, "-p", "--output-format", "json",
                "--model", self.model, "--tools", "",
                "--no-session-persistence",
                "--system-prompt", SYSTEM_PROMPT]
        try:
            proc = subprocess.run(
                argv, input=prompt, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=self.timeout_seconds, cwd=self.cwd)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReasonerError("claude CLI call failed: %s" % exc)
        if proc.returncode != 0:
            raise ReasonerError(
                "claude CLI exited %d: %s"
                % (proc.returncode, (proc.stderr or proc.stdout)[:400]))
        text = proc.stdout
        envelope = _loads(text, None)
        self.last_meta = None
        if isinstance(envelope, dict) and "result" in envelope:
            self.last_meta = {
                key: envelope.get(key) for key in (
                    "session_id", "duration_ms", "num_turns", "is_error",
                    "subtype") if key in envelope}
            # The model identifiers the provider itself reports having
            # served this call with (the configured name may be an alias).
            usage = envelope.get("modelUsage")
            if isinstance(usage, dict) and usage:
                self.last_meta["models"] = sorted(usage)
            if envelope.get("is_error"):
                raise ReasonerError(
                    "claude CLI reported an error result: %s"
                    % str(envelope.get("result"))[:400])
            text = envelope["result"]
        return text


# ------------------------------------------------------------ projection

def _staffable(taskforce):
    """Every (capability, role) somebody can be staffed for: a retained
    seat the Taskforce can resolve, or an outside specialist the Project
    Manager can select."""
    pairs = set()
    for seat in taskforce.get("retained_seats", []):
        pairs.add((seat["capability"], seat["role"]))
    for specialist in taskforce.get("outside_specialists", []):
        for offer in specialist.get("offers", []):
            pairs.add((offer["capability"], offer["role"]))
    return sorted(pairs)


def _without_routing(value):
    """The same data with every worker handle removed. A handle is how a
    worker is routed (it may spell a provider or a model); the Engineer
    selects people by identity, capability and verified record — routing
    stays downstream of staffing."""
    if isinstance(value, dict):
        return {key: _without_routing(item) for key, item in value.items()
                if key not in ("handle", "provider", "model", "route")}
    if isinstance(value, list):
        return [_without_routing(item) for item in value]
    return value


def render_projection(view, context=None):
    """The structured state the model reasons over — compact,
    deterministic, and containing no handle of any kind."""
    taskforce = view.taskforce or {}
    objectives = []
    for objective in view.objectives:
        objective_id = objective["objective_id"]
        engineering = dict(view.engineering.get(objective_id, {}))
        # The project section carries the workstreams and their
        # dependencies; the engineering section keeps the results.
        engineering.pop("workstreams", None)
        engineering.pop("dependencies", None)
        objectives.append({
            "objective_id": objective_id,
            "text": objective["text"],
            "constraints": _loads(objective.get("constraints"), []),
            "new": objective.get("consumed_in_cycle") is None,
            "budget": view.budget.get(objective_id),
            "project": _without_routing(
                view.projects.get(objective_id)
                or {"project_id": None, "workstreams": [], "team": [],
                    "needs_staffing": []}),
            "engineering": engineering,
        })
    events = []
    for event in view.events:
        entry = {"seq": event["journal_seq"], "kind": event["event_kind"],
                 "subject": event["subject_id"]}
        if event["event_kind"] not in ROUTINE_EVENTS:
            facts = _loads(event.get("facts"), None)
            if facts:
                entry["facts"] = facts
        events.append(entry)
    findings = []
    for finding in view.findings:
        findings.append({
            "finding_id": finding["finding_id"],
            "sequence_id": finding["sequence_id"],
            "finding_class": finding["finding_class"],
            "affected_component": finding["affected_component"],
            "statement": finding.get("statement"),
            "significance": finding["significance"],
            "confidence": finding["confidence"],
            "relation_to_requirement": finding["relation_to_requirement"],
            "suggested_follow_up": finding["suggested_follow_up"],
            "evidence_refs": _loads(finding.get("evidence_refs"), []),
        })
    workforce_events = [
        {"event_id": e["event_id"], "kind": e["event_kind"],
         "subject": e["subject_id"],
         "facts": _without_routing(_loads(e.get("facts"), None))}
        for e in view.workforce_events]
    authority = view.authority
    obligations = authority.get("obligations", {})
    projection = {
        "objectives": objectives,
        "what_is_new": {
            "authority_events": events,
            "findings": findings,
            "workforce_events": workforce_events,
        },
        "taskforce": _without_routing({
            "staffable": [{"capability": c, "role": r}
                          for c, r in _staffable(taskforce)],
            "members": taskforce.get("members", []),
            "retained_seats": taskforce.get("retained_seats", []),
            "inactive_seats": taskforce.get("inactive_seats", []),
            "outside_specialists": [
                {"specialist_id": s["specialist_id"],
                 "is_member": s.get("is_member"),
                 "busy": s.get("busy"), "available": s.get("available"),
                 "offers": s["offers"]}
                for s in taskforce.get("outside_specialists", [])],
            "available_capabilities":
                taskforce.get("available_capabilities", {}),
            "active_assignments": taskforce.get("active_assignments", []),
            "recent_demonstrations":
                taskforce.get("recent_demonstrations", []),
            "retention_candidates":
                taskforce.get("retention_candidates", []),
            "established_threshold":
                taskforce.get("established_threshold"),
        }),
        "authority": {
            "sequences_by_state": authority.get("sequences_by_state", {}),
            "halted": obligations.get("halted_sequences", []),
            "awaiting_your_permit":
                obligations.get("verified_awaiting_finalization", []),
            "queued_for_you": obligations.get("queued_admissions", []),
            "permits": authority.get("permits", []),
        },
        "waiting_proposals": [
            {"proposal_id": p["proposal_id"], "class": p["class"],
             "status": p["status"],
             "subject_id": _loads(p.get("body"), {}).get("subject_id")
             or p.get("sequence_id")}
            for p in view.open_proposals],
    }
    if context:
        projection["environment"] = context
    return projection


# ---------------------------------------------------------------- policy

class ReasoningEngineerPolicy(EngineerPolicy):
    policy_id = POLICY_ID

    def __init__(self, reasoner, context=None, max_attempts=2,
                 max_proposals=12):
        """``reasoner`` is a callable ``prompt -> text``. ``context`` is
        static plain data the model should know about the environment
        (the repository, the delegated scope) — never a handle."""
        self.reasoner = reasoner
        self.context = dict(context or {})
        self.max_attempts = max_attempts
        self.max_proposals = max_proposals
        self.last_exchange = None

    # ---------------------------------------------------- deterministic

    def staff(self, view, proposals=()):
        """A reasoning Engineer makes its own project assignments (they
        are part of ``plan``'s answer): the standing rule never selects
        for it."""
        return []

    def needs_reasoning(self, view):
        """Whether this stimulus batch holds anything to decide."""
        if any(o.get("consumed_in_cycle") is None for o in view.objectives):
            return True
        if view.findings:
            return True
        if any(e["event_kind"] not in ROUTINE_EVENTS for e in view.events):
            return True
        if any(e["event_kind"] not in ROUTINE_WORKFORCE_EVENTS
               for e in view.workforce_events):
            return True
        # A workstream waiting for the Project Manager's staffing decision
        # is something to decide, whatever woke the cycle.
        if any((project or {}).get("needs_staffing")
               for project in view.projects.values()):
            return True
        # So is a project whose every workstream is COMPLETED while its
        # objective is still open: completion (or more work) is the
        # Engineer's to declare. A cycle opens only on a stimulus, so
        # this asks once per change, never in a loop.
        for objective in view.objectives:
            streams = self._work(view, objective["objective_id"])
            if streams and all(w["state"] == "COMPLETED" for w in streams):
                return True
        return False

    def _snapshot(self, view):
        return {"entries": view.snapshot["entries"],
                "head_hash": view.snapshot["head_hash"]}

    def _workstreams(self, view, objective_id=None):
        rows = []
        for oid, engineering in view.engineering.items():
            if objective_id is not None and oid != objective_id:
                continue
            for row in engineering.get("workstreams", []):
                entry = dict(row)
                entry["objective_id"] = oid
                rows.append(entry)
        return rows

    def _work(self, view, objective_id):
        """The objective's workstreams that exist as work: admitted by the
        Registry, or waiting for your admission. A proposal the Registry
        or the objective budget REFUSED never became work — it has no
        sequence — so it is nothing to complete and nothing that keeps a
        project open."""
        return [w for w in self._workstreams(view, objective_id)
                if not (w.get("sequence_id") is None
                        and w.get("state") == "REJECTED")]

    def _project_workstreams(self, view, objective_id):
        """The objective's project workstreams by the Project Manager's
        key."""
        project = view.projects.get(objective_id) or {}
        return {w["workstream"]: w for w in project.get("workstreams", [])
                if w.get("workstream")}

    def _permit_reflex(self, view):
        """Work at your publication permit boundary (cap-36) is reported,
        not reasoned about: one WAIT_FOR_AUTHORITY per such sequence."""
        permits = {row["sequence_id"]: row
                   for row in view.authority.get("permits", [])}
        # A wait already on record for a subject is not reported again.
        waiting = {_loads(p.get("body"), {}).get("subject_id")
                   for p in view.open_proposals
                   if p["class"] == "WAIT_FOR_AUTHORITY"}
        by_sequence = {w["sequence_id"]: w for w in self._workstreams(view)
                       if w["sequence_id"]}
        proposals = []
        for sequence_id in view.workstreams.get("finalization_queue", []):
            permit = permits.get(sequence_id)
            headroom = permit is not None and (
                permit["consumed_uses"] + permit["reserved_uses"]
                < permit["permitted_uses"])
            workstream = by_sequence.get(sequence_id)
            if headroom or workstream is None or sequence_id in waiting:
                continue
            if workstream.get("finalization_class") != "SOURCE_MODIFYING":
                continue
            proposals.append({
                "class": "WAIT_FOR_AUTHORITY",
                "objective_id": workstream["objective_id"],
                "rationale": "verified SOURCE_MODIFYING work needs a"
                             " publication permit only you grant",
                "reason": "publication permit required (cap-36)",
                "subject_id": sequence_id,
                "snapshot": self._snapshot(view),
                "provenance": {"findings": [], "events": [
                    e["journal_seq"] for e in view.events
                    if e["subject_id"] == sequence_id]},
            })
        return proposals

    # ------------------------------------------------------- conversion

    def _requirement(self, raw, view, objective_id, cls, errors, label):
        if not isinstance(raw, dict):
            errors.append("%s: requirement must be an object" % label)
            return None
        named = [key for key in ("provider", "model", "specialist_id",
                                 "specialist", "seat_id", "handle",
                                 "staffing") if key in raw]
        if named:
            errors.append(
                "%s: a work requirement names %s. It states a capability"
                " and a role only — who executes it is a separate"
                " ASSIGN_SPECIALIST decision, and provider and model are"
                " never named" % (label, ", ".join(named)))
            return None
        capability = raw.get("capability")
        role = raw.get("role")
        if capability not in CAPABILITIES:
            errors.append("%s: capability %r is not one of %s"
                          % (label, capability, list(CAPABILITIES)))
            return None
        staffable = _staffable(view.taskforce or {})
        if (capability, role) not in staffable:
            errors.append(
                "%s: no retained seat holds and no outside specialist"
                " offers (%s, %s); staffable pairs are %s"
                % (label, capability, role, staffable))
            return None
        requirement = {
            "objective": raw.get("objective"),
            "capability": capability,
            "roles": {role: {"required": True,
                             "success_outcomes": ["success"]}},
            "guidance": raw.get("guidance"),
        }
        if isinstance(raw.get("subsystem"), str) and raw["subsystem"]:
            requirement["subsystem"] = raw["subsystem"]
        if cls in ("INVESTIGATE", "REQUEST_REVIEW"):
            fin = "READ_ONLY"
        else:
            fin = raw.get("finalization_class")
            requirement["finalization_class"] = fin
        if fin == "SOURCE_MODIFYING":
            paths = raw.get("allowed_paths")
            if not (isinstance(paths, list) and paths and all(
                    isinstance(p, str) and p for p in paths)):
                errors.append(
                    "%s: SOURCE_MODIFYING work must name allowed_paths"
                    " (the exact files that may change)" % label)
                return None
            requirement["allowed_scope"] = {
                "paths": sorted(set(paths)), "artifact_paths": []}
        by_sequence = {w["sequence_id"]: w for w in self._workstreams(
            view, objective_id) if w["sequence_id"]}
        by_key = self._project_workstreams(view, objective_id)
        targets = []
        for sequence_id in raw.get("depends_on_sequences") or []:
            targets.append((sequence_id, sequence_id))
        for key in raw.get("depends_on_workstreams") or []:
            workstream = by_key.get(key)
            targets.append((key, workstream["sequence_id"]
                            if workstream else None))
        edges = []
        for named_as, sequence_id in targets:
            target = by_sequence.get(sequence_id)
            if target is None or not target.get("record_hash"):
                errors.append(
                    "%s: the dependency %r is not a workstream already in"
                    " this project (dependencies between workstreams"
                    " proposed in the same answer use DECOMPOSE_TASK and"
                    " depends_on_parts)" % (label, named_as))
                return None
            edges.append({
                "edge_id": "on-" + str(sequence_id)[3:19],
                "edge_kind": "RECORD",
                "target": target["record_hash"],
                "observation_requirement": "ACCEPTANCE"})
        if edges:
            requirement["predecessors"] = edges
        return requirement

    def _provenance(self, raw, view, sequence_id=None):
        given = raw.get("provenance") if isinstance(
            raw.get("provenance"), dict) else {}
        known_findings = {f["finding_id"] for f in view.findings}
        for engineering in view.engineering.values():
            for question in engineering.get("open_questions", {}).get(
                    "unaddressed_findings", []):
                known_findings.add(question["finding_id"])
        findings = [f for f in given.get("findings") or []
                    if f in known_findings]
        events = [e["journal_seq"] for e in view.events
                  if sequence_id and e["subject_id"] == sequence_id]
        provenance = {"findings": findings, "events": events}
        demonstrations = [d for d in given.get("demonstrations") or []
                          if isinstance(d, str)]
        workforce = {}
        if demonstrations:
            workforce["demonstrations"] = demonstrations
        if view.workforce_events:
            workforce["events"] = [e["event_id"]
                                   for e in view.workforce_events]
        if workforce:
            provenance["workforce"] = workforce
        return provenance

    def _workstream_key(self, raw, view, objective_id, batch, label,
                        errors, capability, role, key=None):
        """Register the key a new workstream is proposed under."""
        key = key if key is not None else raw.get("workstream")
        if key is None:
            return True, None
        taken = set(self._project_workstreams(view, objective_id)) \
            | set(batch["keys"].get(objective_id, {}))
        if not (isinstance(key, str) and WORKSTREAM_KEY_RE.match(key)):
            errors.append("%s: workstream %r must be a short lowercase key"
                          % (label, key))
            return False, None
        if key in taken:
            errors.append("%s: workstream key %r is already used in this"
                          " project; choose another" % (label, key))
            return False, None
        batch["keys"].setdefault(objective_id, {})[key] = {
            "capability": capability, "role": role}
        return True, key

    def _staffing(self, raw, view, objective_id, label, errors,
                  capability, role, independent_of):
        """The model's staffing word, checked against the view: an
        explicit selection must name a specialist that exists, offers the
        work and may do it."""
        staffing = raw.get("staffing") or {}
        if not isinstance(staffing, dict):
            errors.append("%s: staffing must be an object" % label)
            return None
        routed = [key for key in ("provider", "model") if key in staffing]
        if routed:
            errors.append("%s: staffing names %s; a project assignment"
                          " names a specialist, never a provider or a"
                          " model" % (label, ", ".join(routed)))
            return None
        mode = staffing.get("mode") or (
            "outside_specialist" if staffing.get("specialist_id")
            else "taskforce")
        if mode not in STAFFING_MODES:
            errors.append("%s: staffing.mode %r is not one of %s"
                          % (label, mode, list(STAFFING_MODES)))
            return None
        escalation = staffing.get("escalation")
        if escalation is not None and escalation not in ESCALATION_CLASSES:
            errors.append(
                "%s: staffing.escalation %r is not one of %s; it states"
                " what the problem needs and never names a provider or a"
                " model" % (label, escalation, list(ESCALATION_CLASSES)))
            return None
        extra = {"escalation": escalation} if escalation else {}
        if mode != "outside_specialist":
            return dict({"mode": mode}, **extra)
        specialist_id = staffing.get("specialist_id")
        offering = [
            s["specialist_id"] for s in (view.taskforce or {}).get(
                "outside_specialists", [])
            if any((o["capability"], o["role"]) == (capability, role)
                   and not o.get("released_for_cause")
                   for o in s.get("offers", []))]
        if specialist_id not in offering:
            errors.append(
                "%s: specialist %r cannot be selected as an outside"
                " specialist for (%s, %s); the outside specialists that"
                " offer it are %s" % (label, specialist_id, capability,
                                      role, offering))
            return None
        if specialist_id in (independent_of or []):
            errors.append(
                "%s: specialist %s did the work under review; a review is"
                " staffed independently of its subject"
                % (label, specialist_id))
            return None
        return dict({"mode": mode, "specialist_id": specialist_id}, **extra)

    def _convert(self, raw, view, index, errors, batch):
        label = "proposal %d" % index
        if not isinstance(raw, dict):
            errors.append("%s: not an object" % label)
            return None
        cls = raw.get("class")
        if cls not in PROPOSAL_CLASSES:
            errors.append("%s: class %r is not one of %s"
                          % (label, cls, list(PROPOSAL_CLASSES)))
            return None
        label = "%s (%s)" % (label, cls)
        active = [o["objective_id"] for o in view.objectives]
        objective_id = raw.get("objective_id")
        if objective_id is None and len(active) == 1:
            objective_id = active[0]
        if objective_id not in active:
            errors.append("%s: objective_id %r is not an active objective"
                          " %s" % (label, objective_id, active))
            return None
        proposal = {"class": cls, "objective_id": objective_id,
                    "rationale": raw.get("rationale"),
                    "snapshot": self._snapshot(view)}
        workstreams = {w["sequence_id"]: w for w in self._workstreams(
            view, objective_id) if w["sequence_id"]}
        by_key = self._project_workstreams(view, objective_id)
        needs = {n["sequence_id"]: n for n in (view.projects.get(
            objective_id) or {}).get("needs_staffing", [])}
        sequence_id = None

        if cls == "OPEN_PROJECT":
            project = raw.get("project") if isinstance(
                raw.get("project"), dict) else {}
            proposal["project"] = {"title": project.get("title"),
                                   "solution": project.get("solution")}
            batch["opens"].add(objective_id)
        elif cls in CONTRACT_CLASSES:
            requirement = self._requirement(
                raw.get("requirement"), view, objective_id, cls, errors,
                label)
            if requirement is None:
                return None
            proposal["requirement"] = requirement
            role = sorted(requirement["roles"])[0]
            ok, key = self._workstream_key(
                raw, view, objective_id, batch, label, errors,
                requirement["capability"], role)
            if not ok:
                return None
            if key:
                proposal["workstream"] = key
            if cls == "REQUEST_REVIEW":
                review_of = raw.get("review_of") or {}
                reviewed = review_of.get("sequence_id")
                if reviewed is None and review_of.get("workstream"):
                    reviewed = (by_key.get(review_of["workstream"])
                                or {}).get("sequence_id")
                target = workstreams.get(reviewed)
                if target is None or not target.get("record_hash"):
                    errors.append(
                        "%s: review_of %r is not a workstream already in"
                        " this project" % (label, review_of))
                    return None
                proposal["review_of"] = {
                    "record_hash": target["record_hash"]}
            batch["work"].add(objective_id)
        elif cls == "DECOMPOSE_TASK":
            parts = []
            earlier = []
            for number, part in enumerate(raw.get("parts") or []):
                part_label = "%s part %d" % (label, number)
                if not isinstance(part, dict):
                    errors.append("%s: not an object" % part_label)
                    return None
                reviewed = part.get("review_of_part")
                if reviewed is not None and reviewed not in earlier:
                    errors.append(
                        "%s: review_of_part %r is not an earlier part of"
                        " this decomposition %s"
                        % (part_label, reviewed, earlier))
                    return None
                requirement = self._requirement(
                    part.get("requirement"), view, objective_id,
                    "REQUEST_REVIEW" if reviewed else "CREATE_TASK",
                    errors, part_label)
                if requirement is None:
                    return None
                ok, _key = self._workstream_key(
                    part, view, objective_id, batch, part_label, errors,
                    requirement["capability"],
                    sorted(requirement["roles"])[0],
                    key=part.get("part_id"))
                if not ok:
                    return None
                entry = {"part_id": part.get("part_id"),
                         "requirement": requirement}
                dependencies = [d for d in part.get("depends_on_parts")
                                or [] if d != reviewed]
                unknown = [d for d in dependencies if d not in earlier]
                if unknown:
                    errors.append(
                        "%s: depends_on_parts %s are not earlier parts of"
                        " this decomposition %s"
                        % (part_label, unknown, earlier))
                    return None
                if dependencies:
                    entry["depends_on_parts"] = dependencies
                if reviewed is not None:
                    entry["review_of_part"] = reviewed
                earlier.append(part.get("part_id"))
                parts.append(entry)
            proposal["parts"] = parts
            batch["work"].add(objective_id)
        elif cls in SEAT_CLASSES:
            sequence_id = raw.get("sequence_id")
            key = raw.get("workstream")
            planned = batch["keys"].get(objective_id, {}).get(key)
            if sequence_id is None and key in by_key:
                sequence_id = by_key[key].get("sequence_id")
            if sequence_id is not None:
                if sequence_id not in workstreams:
                    errors.append("%s: %r is not a workstream of this"
                                  " objective"
                                  % (label, key or sequence_id))
                    return None
                target = workstreams[sequence_id]
                capability = target.get("capability")
                role = (target.get("roles") or [None])[0]
                proposal["sequence_id"] = sequence_id
                proposal["attempt_ordinal"] = int(
                    target.get("attempts") or 0) + 1
            elif planned is not None and cls == "ASSIGN_SPECIALIST":
                # Work proposed in this same answer: the service resolves
                # the key once the work is admitted.
                capability, role = planned["capability"], planned["role"]
                proposal["workstream"] = key
                proposal["attempt_ordinal"] = 1
            else:
                errors.append("%s: names no workstream of this objective"
                              " (workstream %r, sequence_id %r)"
                              % (label, key, raw.get("sequence_id")))
                return None
            independent = (needs.get(sequence_id) or {}).get(
                "independent_of", [])
            staffing = self._staffing(raw, view, objective_id, label,
                                      errors, capability, role,
                                      independent)
            if staffing is None:
                return None
            proposal["staffing"] = staffing
            if cls == "REPAIR_TASK":
                proposal["capability"] = raw.get("capability") or capability
                proposal["guidance"] = raw.get("guidance")
            else:
                proposal["capability"] = capability
                if isinstance(raw.get("guidance"), str) and raw["guidance"]:
                    proposal["guidance"] = raw["guidance"]
        elif cls in WORKFORCE_CLASSES:
            taskforce = view.taskforce or {}
            seats = {}
            for group in ("retained_seats", "inactive_seats"):
                for seat in taskforce.get(group, []):
                    seats[seat["seat_id"]] = seat
            seat = seats.get(raw.get("seat_id"))
            specialist_id = raw.get("specialist_id") or (
                seat["specialist_id"] if seat else None)
            capability = raw.get("capability") or (
                seat["capability"] if seat else None)
            role = raw.get("role") or (seat["role"] if seat else None)
            known = {s["specialist_id"] for s in taskforce.get(
                "outside_specialists", [])}
            known.update(s["specialist_id"] for s in seats.values())
            if specialist_id not in known:
                errors.append("%s: specialist %r is not known to the"
                              " organization" % (label, specialist_id))
                return None
            proposal["specialist_id"] = specialist_id
            proposal["capability"] = capability
            proposal["role"] = role
            qualifying = []
            for candidate in taskforce.get("retention_candidates", []):
                if (candidate["specialist_id"], candidate["capability"],
                        candidate["role"]) == (specialist_id, capability,
                                               role):
                    qualifying = candidate["qualifying_demonstrations"]
            if cls == "RETAIN_SPECIALIST":
                named = [d for d in raw.get("demonstrations") or []
                         if isinstance(d, str)]
                # Binding the evidence is mechanical: an omitted list is
                # filled from the specialist's qualifying demonstrations.
                # A demonstration that does not exist or does not qualify
                # is refused by the Taskforce, whatever is named here.
                proposal["demonstrations"] = (
                    [d for d in named if d in qualifying]
                    or list(qualifying) or named)
            else:
                proposal["reason"] = raw.get("reason")
                proposal["disposition"] = raw.get("disposition") \
                    or "RELEASE"
                if qualifying:
                    # The work this decision considered, on the record.
                    proposal["demonstrations"] = list(qualifying)
        elif cls == "WAIT_FOR_AUTHORITY":
            proposal["reason"] = raw.get("reason")
            proposal["subject_id"] = raw.get("subject_id")
            sequence_id = raw.get("subject_id")
        proposal["provenance"] = self._provenance(raw, view, sequence_id)
        if cls == "RETAIN_SPECIALIST":
            proposal["provenance"].setdefault("workforce", {})[
                "demonstrations"] = list(proposal["demonstrations"])
        try:
            validate_proposal(proposal)
        except ProposalError as exc:
            errors.append("%s: %s" % (label, exc))
            return None
        return proposal

    def _completion_guard(self, proposals, view, errors):
        """F-CE-1: completion is declared only by a batch that proposes no
        further WORK for the objective, and only when the shared
        organizational closure predicate says the objective is eligible.

        A closing workforce decision (retain/release) may accompany it;
        it is not work and is applied first."""
        kept = []
        for proposal in proposals:
            if proposal["class"] != "PROJECT_COMPLETE":
                kept.append(proposal)
                continue
            objective_id = proposal["objective_id"]
            others = [p for p in proposals if p is not proposal
                      and p["objective_id"] == objective_id
                      and p["class"] not in WORKFORCE_CLASSES]
            if others:
                errors.append(
                    "PROJECT_COMPLETE dropped: the batch proposes other"
                    " work for %s" % objective_id)
                continue
            obligations = completion_obligations_from_work(
                self._work(view, objective_id))
            if not obligations["eligible"]:
                reasons = []
                if obligations["unfinished_work"]:
                    reasons.append(
                        "%d workstream(s) not COMPLETED"
                        % len(obligations["unfinished_work"]))
                if obligations["unexamined_reviews"]:
                    reasons.append(
                        "%d reviewed workstream(s) have only"
                        " NOT_EXAMINED reviews"
                        % len(obligations["unexamined_reviews"]))
                if not obligations["has_work"]:
                    reasons.append("no admitted work")
                errors.append(
                    "PROJECT_COMPLETE refused for %s: %s"
                    % (objective_id, "; ".join(reasons)))
            else:
                kept.append(proposal)
        return kept

    # ------------------------------------------------------------- plan

    def build_prompt(self, view):
        projection = render_projection(view, self.context)
        header = INSTRUCTIONS % {
            "release_reasons": list(RELEASE_REASONS),
            "escalation_classes": list(ESCALATION_CLASSES),
            "capabilities": list(CAPABILITIES),
            "finding_classes": list(FINDING_CLASSES),
        }
        return header + "\nPROJECTION\n" + json.dumps(
            projection, sort_keys=True, indent=1, default=str)

    def plan(self, view):
        reflex = self._permit_reflex(view)
        exchange = {"policy_id": self.policy_id, "attempts": [],
                    "reflex": [p["class"] for p in reflex]}
        self.last_exchange = exchange
        if not self.needs_reasoning(view):
            exchange["reasoned"] = False
            return reflex
        exchange["reasoned"] = True
        base_prompt = self.build_prompt(view)
        exchange["prompt_sha256"] = _sha256(base_prompt)
        prompt = base_prompt
        proposals = []
        for attempt in range(self.max_attempts):
            errors = []
            entry = {"prompt_sha256": _sha256(prompt)}
            exchange["attempts"].append(entry)
            try:
                answer = self.reasoner(prompt)
            except ReasonerError as exc:
                entry["error"] = str(exc)
                continue
            entry["response_sha256"] = _sha256(answer or "")
            entry["response"] = (answer or "")[:20000]
            meta = getattr(self.reasoner, "last_meta", None)
            if meta:
                entry["meta"] = meta
            parsed = extract_json_object(answer)
            if parsed is None or not isinstance(
                    parsed.get("proposals"), list):
                errors.append("the answer is not one JSON object with a"
                              " proposals list")
                proposals = []
            else:
                entry["reasoning"] = str(parsed.get("reasoning", ""))[:4000]
                raw = parsed["proposals"]
                if len(raw) > self.max_proposals:
                    errors.append(
                        "%d proposals exceed the limit of %d per cycle;"
                        " propose the most important" % (
                            len(raw), self.max_proposals))
                    raw = raw[:self.max_proposals]
                # Work is converted before the decisions that refer to it
                # (an assignment may name a workstream of this answer).
                batch = {"keys": {}, "opens": set(), "work": set()}
                ordered = sorted(
                    enumerate(raw), key=lambda item: (
                        _APPLY_ORDER.get(item[1].get("class"), 9)
                        if isinstance(item[1], dict) else 9, item[0]))
                proposals = [p for p in (
                    self._convert(item, view, index, errors, batch)
                    for index, item in ordered) if p is not None]
                proposals = self._completion_guard(proposals, view, errors)
                unopened = sorted(
                    objective_id for objective_id in batch["work"]
                    if objective_id not in batch["opens"]
                    and not (view.projects.get(objective_id)
                             or {}).get("project_id"))
                if unopened and attempt == 0:
                    # Asked once; an answer that still opens no project
                    # has its work organized under an implicit one.
                    errors.append(
                        "work is proposed for %s but no project is open:"
                        " put OPEN_PROJECT first — it states the solution"
                        " the project realizes" % unopened)
            entry["errors"] = errors
            if not errors:
                break
            prompt = (base_prompt + "\n\nYOUR PREVIOUS ANSWER WAS NOT"
                      " USABLE AS GIVEN. Problems:\n- " + "\n- ".join(errors)
                      + "\nAnswer again with one corrected JSON object.")
        exchange["proposals"] = len(proposals)
        if reflex:
            # A batch that still waits on your authority is not a
            # completed project.
            proposals = [p for p in proposals
                         if p["class"] != "PROJECT_COMPLETE"]
        return reflex + proposals


# The names this policy carried before the organizational model was
# aligned. Kept for existing callers.
ReasoningChiefEngineerPolicy = ReasoningEngineerPolicy
