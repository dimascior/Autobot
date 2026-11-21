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


@dataclass(frozen=True)
class CoordinatorDecisionView:
    decision_id: str
    objective_id: str
    elevation_mode: str
    features: Dict[str, Any] = field(default_factory=dict)
    route: Optional[str] = None


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


@dataclass(frozen=True)
class SeatView:
    seat_id: str
    specialist_id: str
    specialist_handle: str
    capability: str
    role: str
    status: str
    qualifying_count: int = 0


@dataclass(frozen=True)
class TaskforceView:
    member_count: int = 0
    seat_count: int = 0
    established_count: int = 0
    seats: List[SeatView] = field(default_factory=list)


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

    # activity stream
    recent_events: List[ActivityEvent] = field(default_factory=list)
