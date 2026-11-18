# The frozen principals and the organizational model

Chapter 0 froze these, and they stay frozen:

| Registry | Entry |
|---|---|
| `chapter0/registries/principals.json` | `planner` — "Autobot Planner (Chief Engineer seat)", an Autobot seat, an LLM |
| `chapter0/registries/principals.json` | `agent_factory` — "Autobot Agent Factory", an Autobot seat, not an LLM |
| `chapter0/registries/capabilities.json` | `cap-14` — "Seat, route and model selection", owner `agent_factory`, consumer `dispatcher`, forbidden `task_force` |
| `chapter0/registries/capabilities.json` | `cap-47` — caps, routes and budgets are yours; `agent_factory` consumes them |
| `chapter0/registries/principals.json` | `task_force` — "Task Force agents (orchestrator, worker, monitor, poller)", an agent, an LLM |

Those files are hash-checked history. This document does not amend them
and nothing in this milestone edits them.

## How the frozen names map onto the architecture

| Frozen principal | In the architecture | What it is now |
|---|---|---|
| `planner` | the **Engineer**, in both its roles (technical problem solving; Project Manager) | the wire identity on everything the Engineer authors: `authored_by: "planner"` in a frozen record, `decided_by: "planner"` on a workforce decision |
| `agent_factory` | not an actor | the wire identity of the **staffing function** — the deterministic code that turns a staffing decision into a routed execution |
| `task_force` | the specialists doing the work | unchanged: the agents that execute, which may never choose their own seat, route or model |

The Engineer was called the Chief Engineer before the organizational
model was aligned, and the frozen registry's name for its seat still says
so; that name is history and is not edited. In code,
`ChiefEngineerPolicy`, `ChiefEngineerService` and
`ReasoningChiefEngineerPolicy` remain as aliases of `EngineerPolicy`,
`EngineerService` and `ReasoningEngineerPolicy`, and `intelligence.chief`
re-exports `intelligence.engineer`.

## `agent_factory` is compatibility, not architecture

The architecture has no component called the Agent Factory. What cap-14
names is a function, split along the model's own line:

| Part of cap-14 | Who does it |
|---|---|
| which **retained member** takes a capability request | the Taskforce (`TaskforceSeatResolver.resolve`) |
| which **outside specialist** joins a project | the Engineer as Project Manager (`ASSIGN_SPECIALIST`) |
| which **provider and model** execute the assignment | nobody decides it per task: it is the assigned specialist's route in the catalog, read when the attempt is dispatched |

`agent_factory` is the identity all three are recorded under on the wire,
because every manifest already admitted carries it.

## How the frozen registry still holds

cap-14's forbidden edge is that the `task_force` principal — the agents
doing the work — may not select a seat, a route or a model. No agent
chooses its own model.

That is exactly as true now as when it was frozen:

- A specialist never takes part in its own staffing. It receives a
  manifest; the provider and the model in it were read from the catalog
  by deterministic code.
- The Engineer names a *specialist* for a project assignment. It never
  names a provider or a model — `validate_proposal` refuses either in a
  work requirement and in an assignment — and it is not even shown them:
  handles, providers and models are removed from what it reasons over.
- A work requirement, and so every TaskContractRecord, names no provider,
  no model and no specialist.

## What stays on the wire

Records that cross the execution boundary were admitted with the
registered principal id, and new ones keep it:

| Record | Field | Value |
|---|---|---|
| ExecutionManifest | `provider_assignment.selected_by` | `agent_factory` |
| execution slot (`seats` row, plan store) | `selected_by` | `agent_factory` |
| assignment view | `selected_by` | `agent_factory` |
| assignment view | `staffed_by` | `taskforce` |

`workforce.STAFFING_PRINCIPAL` holds the id. Every manifest AutoSwarm has
already claimed carries it, and AutoSwarm compares a relaunched manifest
byte for byte, so the value is not changed.

Who actually decided an assignment is a separate, intelligence-plane fact:
`project_assignments.decided_by` is `TASKFORCE` (a retained seat was
resolved) or `PROJECT_MANAGER` (a specialist was selected).

`autobot.principals.PRINCIPALS` still mirrors `principals.json` exactly
and a test still cross-checks the two.

## What was removed

| Removed | Replacement |
|---|---|
| `intelligence.roles` (`AgentFactory`, `AgentFactoryError`, `FACTORY_PRINCIPAL`) | `intelligence.workforce`, `intelligence.capabilities` |
| `intelligence.AgentFactory` export | `intelligence.SpecialistPool`, `TaskforceRoster`, `TaskforceSeatResolver` |
| `ContinuousDriver(..., agent_factory, ...)` | `ContinuousDriver(..., pool, ...)` |
| `AuthorityRuntime(factory=...)`, `runtime.factory` | `AuthorityRuntime(pool=...)`, `runtime.pool` |

No compatibility alias remains for these.

## Historical documents

`chapter0/CHAPTER0_ARCHITECTURE.md`, `chapter0/CHAPTER0_REGISTERS.md`,
`chapter2/INTELLIGENCE.md`, `chapter2/RECONCILIATION.md` and
`chapter2/README.md` describe the Agent Factory and the Chief Engineer as
they were when they were written. They are records of those milestones
and are left as written; `chapter2/INTELLIGENCE.md` carries a note
pointing here.
