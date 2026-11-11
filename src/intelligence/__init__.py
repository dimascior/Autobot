"""The engineering-intelligence plane (integration layer, D-63/D-64).

This package is the Chief Engineer / Taskforce side of the loop the
Chapter 2 runtime was built to serve:

    project_state() -> Chief Engineer reasoning -> typed proposals ->
    compiled TaskContractRecords -> Registry admission -> Taskforce
    seats -> PromptProjection -> transport execution -> the deterministic
    authority arcs (evidence, verification, finalization, acceptance,
    dependency release) -> updated project_state() -> reasoning again.

Boundary (the precise split the registries assign):

- ``src/autobot`` is the authority plane (D-64). Registry and Sequencer
  own every authoritative mutation (D-63); cap-43/cap-51 forbid the
  planner and the task_force from acceptance and from applying
  transitions. Nothing in this package writes an authoritative table:
  its reads of the authority ledger go through a READ-ONLY SQLite
  connection (``readonly_view``), and its writes go only through the
  public Registry/Sequencer APIs it is handed.
- This package owns engineering reasoning state: objectives (cap-01,
  authored by you), planning cycles, typed proposals (cap-13), compiled
  candidate contracts (cap-07, inside an active delegation), specialist
  findings, and Taskforce seats (cap-22/cap-23 doing and watching the
  work; cap-14 seat/provider selection). That state lives in its OWN
  SQLite file (``PlanStore``), never in autobot.db.
- Identity still belongs to the protocol: a compiled proposal's
  deduplication key IS the Chapter 1 contract_digest, so duplicate
  submission converges through Registry admission (obj-32), and
  execution idempotency rides the Sequencer-minted call_id (t-a02).
"""

from .chief import ChiefEngineerPolicy, ChiefEngineerService, PlanningView
from .compiler import RepositoryEnvironment, compile_proposal
from .driver import ContinuousDriver
from .events import pump_authority_events
from .findings import admit_finding
from .planstore import PlanStore, readonly_view
from .proposals import PROPOSAL_CLASSES, proposal_identity, validate_proposal
from .roles import CAPABILITIES, AgentFactory
from .taskforce import Taskforce

__all__ = [
    "AgentFactory",
    "CAPABILITIES",
    "ChiefEngineerPolicy",
    "ChiefEngineerService",
    "compile_proposal",
    "ContinuousDriver",
    "admit_finding",
    "PlanStore",
    "PlanningView",
    "PROPOSAL_CLASSES",
    "proposal_identity",
    "pump_authority_events",
    "readonly_view",
    "RepositoryEnvironment",
    "Taskforce",
    "validate_proposal",
]
