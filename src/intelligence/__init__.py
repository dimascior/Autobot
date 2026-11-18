"""The engineering-intelligence plane (integration layer, D-63/D-64).

Autobot is an autonomous engineering and orchestration system. This
package is its Engineer, the projects the Engineer runs, and the
Taskforce:

    AUTOBOT
        |
        Engineer
        |
        +-- technical problem solving
        |
        +-- Project Manager role when coordination is required
                |
                +-- project / work graph
                +-- retained Taskforce participants
                +-- outside project-specific specialists

    Taskforce
        +-- persistent retained membership
        +-- retained seats
        +-- capability demonstrations
        +-- availability
        +-- organizational learning

    AutoSwarm
        +-- execution substrate

    Authority Plane
        +-- independently verifies and accepts work

    project_state() -> Engineer reasoning -> typed proposals ->
    compiled TaskContractRecords -> Registry admission -> project
    assignment (a retained member the Taskforce resolves, or an outside
    specialist the Project Manager selects) -> PromptProjection ->
    AutoSwarm execution -> the deterministic authority arcs (evidence,
    verification, finalization, acceptance, dependency release) ->
    capability demonstrations and findings -> project_state() ->
    reasoning again.

Six identities stay separate:

    TaskContractRecord    frozen work authority; independent of provider,
                          model and specialist identity
    Project               a temporary coordinated engineering undertaking
    ProjectAssignment     temporary participation in one workstream
    TaskforceMembership   persistent organizational membership
    TaskforceSeat         a retained organizational capability and role
    AttemptContract       one authority-bound execution

The Engineer solves problems and may become the Project Manager needed to
realize a solution. A project may use both retained Taskforce members and
outside specialists. Project participation is temporary; Taskforce
membership is persistent and earned through explicit evidence-backed
retention. Autobot coordinates the project, AutoSwarm executes the
sessions, and the authority plane independently determines what work
becomes accepted system state.

Modules: the ENGINEER (``engineer``, ``policy``, ``proposals``,
``compiler``, ``query``), the TASKFORCE (``workforce``,
``demonstrations``), EXECUTION through AutoSwarm (``taskforce``,
``supervision``, ``prompts``) and AUTHORITY (``src/autobot``, consumed
only through its public APIs).

Boundary (the precise split the registries assign):

- ``src/autobot`` is the authority plane (D-64). Registry and Sequencer
  own every authoritative mutation (D-63); cap-43/cap-51 forbid the
  planner and the task_force from acceptance and from applying
  transitions. Nothing in this package writes an authoritative table:
  its reads of the authority ledger go through a READ-ONLY SQLite
  connection (``readonly_view``), and its writes go only through the
  public Registry/Sequencer APIs it is handed.
- This package owns engineering reasoning, project state and Taskforce
  state: objectives (cap-01, authored by you), planning cycles, typed
  proposals (cap-13), compiled candidate contracts (cap-07, inside an
  active delegation), specialist findings, projects with their
  workstreams, assignments and teams, and the Taskforce — its members,
  the seats they hold and what specialists have verifiably demonstrated.
  That state lives in its OWN SQLite file (``PlanStore``), never in
  autobot.db; losing it loses no authority.
- Identity still belongs to the protocol: a compiled proposal's
  deduplication key IS the Chapter 1 contract_digest, so duplicate
  submission converges through Registry admission (obj-32), and
  execution idempotency rides the Sequencer-minted call_id (t-a02).
  Project, assignment, specialist, membership, seat and demonstration
  identities are intelligence-plane identities and never substitute for
  them.
- Names. The registered principal behind the Engineer is ``planner``;
  the registered principal behind staffing is ``agent_factory``. Both are
  frozen wire identities (Chapter 0) and stay on the records that carry
  them. Before the organizational model was aligned the Engineer was
  called the Chief Engineer: the ``ChiefEngineer*`` names remain as
  aliases.
"""

from .capabilities import CAPABILITIES
from .compiler import RepositoryEnvironment, compile_proposal
from .demonstrations import DemonstrationRecorder
from .driver import ContinuousDriver
from .engineer import (
    ChiefEngineerPolicy,
    ChiefEngineerService,
    EngineerPolicy,
    EngineerService,
    PlanningView,
    standing_staffing_rule,
)
from .events import pump_authority_events
from .findings import admit_finding
from .planstore import PlanStore, readonly_view
from .policy import (
    ClaudeCliReasoner,
    ReasoningChiefEngineerPolicy,
    ReasoningEngineerPolicy,
)
from .proposals import (
    ACTING_AS,
    PROPOSAL_CLASSES,
    proposal_identity,
    validate_proposal,
)
from .runtime import AuthorityRuntime, ConfigError
from .taskforce import Taskforce
from .workforce import SpecialistPool, TaskforceRoster, TaskforceSeatResolver

__all__ = [
    "ACTING_AS",
    "AuthorityRuntime",
    "CAPABILITIES",
    "ChiefEngineerPolicy",
    "ChiefEngineerService",
    "ClaudeCliReasoner",
    "ConfigError",
    "ContinuousDriver",
    "DemonstrationRecorder",
    "EngineerPolicy",
    "EngineerService",
    "PROPOSAL_CLASSES",
    "PlanStore",
    "PlanningView",
    "ReasoningChiefEngineerPolicy",
    "ReasoningEngineerPolicy",
    "RepositoryEnvironment",
    "SpecialistPool",
    "Taskforce",
    "TaskforceRoster",
    "TaskforceSeatResolver",
    "admit_finding",
    "compile_proposal",
    "proposal_identity",
    "pump_authority_events",
    "readonly_view",
    "standing_staffing_rule",
    "validate_proposal",
]
