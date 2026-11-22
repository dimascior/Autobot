"""Frozen dataclasses — the abstraction contract between readers and renderers.

Every view is a plain snapshot of one domain object at one point in time.
No SQLite rows, no raw dicts, no mutation. Presentation reads these;
readers produce these; the model assembles these.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ObjectiveView:
    objective_id: str
    text: str
    status: str
    created_utc: str
    completed_utc: Optional[str] = None
    # derived from the objective's work items (project workstreams and the
    # Orchestrator's tasks) and their authority sequences: HALTED | working
    # | blocked | queued | planned | work finished | no work | None
    work_state: Optional[str] = None
    work_note: Optional[str] = None
    work_since_utc: Optional[str] = None


@dataclass(frozen=True)
class CoordinatorDecisionView:
    decision_id: str
    objective_id: str
    elevation_mode: str
    features: Dict[str, Any] = field(default_factory=dict)
    route: Optional[str] = None
    created_utc: Optional[str] = None
    policy_id: Optional[str] = None
    # derived, never recorded: for each feature, the policy keywords found
    # in the objective text, or "context" when none was (rule A13)
    feature_sources: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeView:
    node_id: str
    orchestration_id: str
    node_key: str
    status: str
    realization_kind: Optional[str] = None
    work_profile: Optional[str] = None
    capability: Optional[str] = None
    # from authority binding
    sequence_id: Optional[str] = None
    record_hash: Optional[str] = None
    binding_status: Optional[str] = None
    # from node output
    output_kind: Optional[str] = None
    output_sha256: Optional[str] = None
    # from assignment (what actually executed)
    provider: Optional[str] = None
    model: Optional[str] = None
    specialist_handle: Optional[str] = None


@dataclass(frozen=True)
class EdgeView:
    edge_id: str
    source_node_id: str
    target_node_id: str
    dependency_kind: str
    status: str
    acceptance_sha256: Optional[str] = None


@dataclass(frozen=True)
class RunView:
    orchestration_id: str
    objective_id: str
    mode: str
    status: str
    nodes: List[NodeView] = field(default_factory=list)
    edges: List[EdgeView] = field(default_factory=list)
    # ``status`` is the recorded orchestration_runs.status; these say what
    # the run's objective and work actually are: working | stuck | done |
    # waiting
    created_utc: Optional[str] = None
    objective_status: Optional[str] = None
    effective_state: Optional[str] = None
    effective_note: Optional[str] = None
    effective_since_utc: Optional[str] = None
    # the Orchestrator policy that planned this run's objective (the
    # Coordinator decision's route) and its reasoning cycles over it
    planned_by: Optional[str] = None
    reasoning_cycles: int = 0


@dataclass(frozen=True)
class OrchestratorView:
    """The Orchestrator policy's reasoning (orchestration_reasoning): it
    runs on the Engineer's configured model (runtime.py)."""
    policy_id: str
    model: Optional[str] = None
    cycles: int = 0
    decisions: int = 0
    last_utc: Optional[str] = None
    # decisions the applicator did not apply (status SKIPPED, REJECTED,
    # FAILED or INVALID in application_results), by decision class
    not_applied: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SequenceView:
    sequence_id: str
    state: str
    record_hash: Optional[str] = None
    contract_digest: Optional[str] = None
    node_id: Optional[str] = None


@dataclass(frozen=True)
class AttemptView:
    attempt_id: str
    sequence_id: str
    state: str


@dataclass(frozen=True)
class AssignmentView:
    assignment_id: str
    specialist_id: str
    specialist_handle: str
    sequence_id: str
    capability: str
    role: str
    status: str
    selection_path: str
    provider: Optional[str] = None
    model: Optional[str] = None
    outcome: Optional[str] = None
    workstream_key: Optional[str] = None
    selected_utc: Optional[str] = None
    settled_utc: Optional[str] = None


@dataclass(frozen=True)
class SeatView:
    seat_id: str
    specialist_id: str
    specialist_handle: str
    capability: str
    role: str
    status: str
    # every QUALIFYING demonstration of this capability and role by the
    # holder, through the seat or not (the agent view's "Nq")
    qualifying_count: int = 0
    # demonstrations recorded through this seat, and those recorded without
    # it (typically the outside work the retention decision rested on)
    seat_qualifying: int = 0
    seat_not_qualifying: int = 0
    other_qualifying: int = 0
    other_not_qualifying: int = 0
    since_utc: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    current_work: Optional[str] = None
    stuck_projects: int = 0
    # the configured route the holder's sessions run on now (a handle is a
    # name, not a model), and the records it earned on other models:
    # [model label, qualifying, not qualifying, last recorded]
    route_name: Optional[str] = None
    earlier_records: List[Any] = field(default_factory=list)
    # the names in current_work, one per item (a task name may hold ", ")
    current_list: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TaskforceView:
    member_count: int = 0
    seat_count: int = 0
    established_count: int = 0
    seats: List[SeatView] = field(default_factory=list)
    retained_count: int = 0
    outside_pool: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActivityEvent:
    timestamp: str
    layer: str      # coordinator | orchestrator | engineer | taskforce | authority
    kind: str       # admitted | decided | created | assigned | dispatched | collected | verified | accepted | released | completed
    subject_id: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthorityCensus:
    sequences_by_state: Dict[str, int] = field(default_factory=dict)
    attempts_by_state: Dict[str, int] = field(default_factory=dict)
    cas_object_count: int = 0
    released_edge_count: int = 0


@dataclass(frozen=True)
class RoutingView:
    default_route: str = "?"
    escalation_route: str = "?"
    planner_model: str = "?"
    routes: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActiveSessionView:
    """A specialist session currently executing through AutoSwarm."""
    call_id: str
    sequence_id: Optional[str] = None
    native_session_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    status: str = "UNKNOWN"
    started_utc: Optional[str] = None
    workspace: Optional[str] = None
    stdout_lines: int = 0
    stdout_tail: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SessionView:
    """One attempt the authority plane holds in flight, joined to the
    assignment that staffed it and to its AutoSwarm call directory."""
    attempt_id: str
    sequence_id: str
    attempt_state: str
    attempt_since_utc: Optional[str] = None
    call_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    role: Optional[str] = None
    capability: Optional[str] = None
    specialist_handle: Optional[str] = None
    selection_path: Optional[str] = None
    workstream_key: Optional[str] = None
    objective_id: Optional[str] = None
    pid: Optional[int] = None
    started_utc: Optional[str] = None
    phase: Optional[str] = None
    finalization_class: Optional[str] = None
    # the call's stdout.log: lines, the last one, and when it last grew
    output_lines: int = 0
    last_line: Optional[str] = None
    output_utc: Optional[str] = None
    # the configured route the provider and model resolve to
    route_name: Optional[str] = None
    # running, or finished: the session wrote result.json (its end) while
    # the attempt still waits to be collected
    session_state: str = "running"
    finished_utc: Optional[str] = None
    # what the session's own record says (a Codex rollout): events, tool
    # calls, the newest event, what it says it is doing, task complete
    activity_events: int = 0
    activity_tools: int = 0
    activity_utc: Optional[str] = None
    activity_doing: Optional[str] = None
    activity_done: bool = False


@dataclass(frozen=True)
class LivenessView:
    """Heartbeats: the driver renews its lease on every step; the
    sequencer holds an epoch lease; the journal head is the newest
    authority transition."""
    driver_holder: Optional[str] = None
    driver_step_utc: Optional[str] = None
    driver_expires_utc: Optional[str] = None
    sequencer_holder: Optional[str] = None
    sequencer_epoch: Optional[int] = None
    sequencer_expires_utc: Optional[str] = None
    journal_seq: Optional[int] = None
    journal_utc: Optional[str] = None


@dataclass(frozen=True)
class CycleView:
    cycle_id: int
    started_utc: str
    status: str
    closed_utc: Optional[str] = None
    proposals: int = 0
    note: Optional[str] = None


@dataclass(frozen=True)
class ProposalView:
    proposal_id: str
    proposal_class: str
    status: str
    objective_id: Optional[str] = None
    rationale: str = ""
    sequence_id: Optional[str] = None
    created_utc: Optional[str] = None
    workstream_key: Optional[str] = None


@dataclass(frozen=True)
class StaffingView:
    """An OPEN staffing decision: who the next attempt of a workstream
    goes to (the Taskforce, or a named outside specialist)."""
    decision_id: str
    sequence_id: str
    mode: str
    objective_id: Optional[str] = None
    project_id: Optional[str] = None
    attempt_ordinal: Optional[int] = None
    specialist_handle: Optional[str] = None
    decided_utc: Optional[str] = None
    workstream_key: Optional[str] = None


@dataclass(frozen=True)
class EngineeringDecisionView:
    """An ENGINEER_REASONING binding: a request to the Engineer and
    its adopted decision."""
    request_id: str
    node_id: Optional[str] = None
    decision_id: Optional[str] = None
    content_sha256: Optional[str] = None
    created_utc: Optional[str] = None
    adopted_utc: Optional[str] = None


@dataclass(frozen=True)
class RecentProposalView:
    """A recently disposed proposal: what the Engineer produced."""
    proposal_class: str
    status: str
    objective_id: Optional[str] = None
    workstream_key: Optional[str] = None
    created_utc: Optional[str] = None


@dataclass
class EngineerView:
    """The Project Manager's live state: its cycles, what waits, what it
    has not staffed yet, and whether its reasoning is retrievable."""
    cycles: List[CycleView] = field(default_factory=list)
    waiting: List[ProposalView] = field(default_factory=list)
    staffing_open: List[StaffingView] = field(default_factory=list)
    engineering_decisions: List[EngineeringDecisionView] = field(
        default_factory=list)
    recent_proposals: List[RecentProposalView] = field(default_factory=list)
    decisions_total: int = 0
    decisions_stored: int = 0


@dataclass(frozen=True)
class EdgeRefView:
    """One predecessor edge of a work item: a frozen record's edge, or an
    Orchestrator dependency between two of its tasks."""
    edge_id: str
    edge_kind: str
    predecessor_sequence_id: Optional[str] = None
    predecessor_key: Optional[str] = None
    predecessor_state: Optional[str] = None
    released_utc: Optional[str] = None
    predecessor_item_id: Optional[str] = None


@dataclass(frozen=True)
class WorkItemView:
    """One unit of work joined to its authority sequence, its holder, and
    its predecessor edges: a project workstream, or a task (node) of an
    Orchestrator plan, which may have no sequence yet (state PLANNED)."""
    workstream_key: str
    sequence_id: Optional[str] = None
    proposal_class: Optional[str] = None
    state: Optional[str] = None
    since_utc: Optional[str] = None
    holder: Optional[str] = None
    selection_path: Optional[str] = None
    assignment_status: Optional[str] = None
    assignment_outcome: Optional[str] = None
    attempt_state: Optional[str] = None
    call_id: Optional[str] = None
    predecessors: List[EdgeRefView] = field(default_factory=list)
    halt_rule: Optional[str] = None
    halt_reason: Optional[str] = None
    halt_completeness: Optional[str] = None
    next_staffing: Optional[str] = None
    # derived: the halt in words (what a t-s12 reason code means)
    halt_cause: Optional[str] = None
    # workstream | node; item_id links predecessor edges inside a tree
    kind: str = "workstream"
    item_id: Optional[str] = None


@dataclass(frozen=True)
class ProjectView:
    """A project and its work items; or, kind "plan", the tasks of an
    Orchestrator run that are bound to no project."""
    project_id: str
    objective_id: str
    title: str
    status: str
    opened_utc: Optional[str] = None
    closed_utc: Optional[str] = None
    items: List[WorkItemView] = field(default_factory=list)
    kind: str = "project"


@dataclass(frozen=True)
class TrustView:
    receipts_by_completeness: Dict[str, int] = field(default_factory=dict)
    verifications_by_outcome: Dict[str, int] = field(default_factory=dict)
    effects_by_state: Dict[str, int] = field(default_factory=dict)
    witnesses: int = 0
    approvals_by_decision: Dict[str, int] = field(default_factory=dict)
    demonstrations_by_outcome: Dict[str, int] = field(default_factory=dict)
    findings_by_significance: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AttentionItem:
    """A derived flag. ``rule`` names the attention rule (A1-A13) that
    raised it, so it can be checked against the rows underneath."""
    rule: str
    severity: str  # critical | warning | info
    subject: str
    detail: str
    since_utc: Optional[str] = None
    whose_move: Optional[str] = None


@dataclass(frozen=True)
class CASObjectView:
    """A content-addressed artifact summary."""
    address: str
    kind: Optional[str] = None
    size_bytes: int = 0


@dataclass(frozen=True)
class ActivitySnapshot:
    captured_utc: str

    # organizational
    objectives: List[ObjectiveView] = field(default_factory=list)
    coordinator_decisions: List[CoordinatorDecisionView] = field(default_factory=list)
    runs: List[RunView] = field(default_factory=list)

    # authority
    authority: AuthorityCensus = field(default_factory=AuthorityCensus)
    active_sequences: List[SequenceView] = field(default_factory=list)
    active_attempts: List[AttemptView] = field(default_factory=list)

    # workforce
    taskforce: TaskforceView = field(default_factory=TaskforceView)
    recent_assignments: List[AssignmentView] = field(default_factory=list)

    # execution
    active_sessions: List[ActiveSessionView] = field(default_factory=list)
    routing: RoutingView = field(default_factory=RoutingView)
    sessions: List[SessionView] = field(default_factory=list)

    # activity stream
    recent_events: List[ActivityEvent] = field(default_factory=list)

    # live state of the organization and the control plane
    liveness: LivenessView = field(default_factory=LivenessView)
    engineer: EngineerView = field(default_factory=EngineerView)
    projects: List[ProjectView] = field(default_factory=list)
    trust: TrustView = field(default_factory=TrustView)
    attention: List[AttentionItem] = field(default_factory=list)

    # the Orchestrator's plans bound to no project, its reasoning, and the
    # attempts in flight that have no live session: past it (by state)
    # and not yet dispatched
    plans: List[ProjectView] = field(default_factory=list)
    orchestrator: Optional[OrchestratorView] = None
    after_session: Dict[str, int] = field(default_factory=dict)
    before_session: int = 0
