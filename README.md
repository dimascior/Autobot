# Autobot

Autobot is an autonomous engineering and orchestration system. It takes an objective, decomposes it into concrete work, staffs that work from a persistent Taskforce of specialists, executes through isolated sessions, and independently verifies every result through a deterministic authority plane.

```text
Objective
    |
COORDINATOR ── admission + elevation
    |
    +-- DIRECT ── single task, Taskforce executes
    +-- ENGINEER_ELEVATED ── Opus Engineer designs the solution
    +-- ORCHESTRATOR_ELEVATED ── Opus Orchestrator builds the DAG
            |
    OrchestrationRun
        |
    OrchestrationNode DAG
        |   +-- TASK_CONTRACT ── specialist executes
        |   +-- ENGINEER_REASONING ── Opus Engineer solves
        |   +-- optional Project binding
        |
    Taskforce ── retained seats + outside specialists
        |
    AutoSwarm ── launches sessions on routed providers
        |
    Authority Plane ── verifies and accepts work
```

It uses the agent CLIs' own subscription logins, never API keys.

## Status

| Capability | Status |
|---|---|
| **Coordinator** | Live. Admits objectives as DIRECT, ENGINEER_ELEVATED, or ORCHESTRATOR_ELEVATED based on complexity scoring. |
| **Opus Objective Orchestrator** | Live. Claude Opus 4.6 [1M] constructs multi-node DAGs with dependency edges, delegates architecture to the Engineer, and identifies parallelism. |
| **Opus Engineer** | Live. Reasons on Opus 4.6 [1M] for technical design. ENGINEER_REASONING nodes produce adopted EngineeringDecisions. |
| **Operational Project independence** | Live. TASK_CONTRACT nodes execute through direct authority admission without creating legacy Projects or workstreams. |
| **Taskforce** | Live. 5 seats: 3 Opus 4.6 [1M] (architecture, engineering, security review) + 2 Luna 6 Max (research, verification). Seats earned through qualifying demonstrations and explicit retention decisions. |
| **Parallel execution** | Live. Multiple specialists execute concurrently on independent nodes. |
| **Authority plane** | Live end to end. Registry, Sequencer, deterministic verification, CAS, complete 6-tier identity binding. |
| **Execution routing** | Live. Default: Luna 6 Max (`codex`/`gpt-6-luna`/`max`). Escalation: Opus 4.6 [1M]. Routes are execution state, never part of any identity. |
| **Independent review** | Live. A separate specialist in a separate session. |
| **SOURCE_MODIFYING work** | Live. Specialist commits in isolated worktrees, published by authority after verification and permit. |
| **Dashboard** | Live. Terminal UI with Coordinator, Orchestrator, Engineer, Taskforce, Authority sections. Keyboard scroll (1-5 focus, arrows). |
| **Session consolidation** | Live. Specialist session transcripts auto-consolidate to the parent project for devtools viewing. |

Every identity behind these rows, and everything production found that is still open, is in [docs/architecture/orchestration/PRODUCTION_PROOF.md](docs/architecture/orchestration/PRODUCTION_PROOF.md).

## The parts

### Engineer

The technical problem-solving intelligence. It consumes structured state: objectives, the current project, the work that exists and its authority state, dependencies, findings, verified results, failed attempts, the Taskforce's retained capabilities and the outside specialists a project may draw on. It investigates, develops the solution, weighs tradeoffs and determines the work the solution requires.

When realizing a solution needs coordinated work, the Engineer acts as its **Project Manager**: it opens the project, lays out the workstreams and their dependencies, and composes a temporary team.

It answers only with typed proposals. It never selects a provider or a model, never certifies its own conclusions, and cannot change authority state. The production policy reasons with a model that is given no tools and is not shown how any specialist is routed.

### Projects and their teams

A project is temporary. Each of its workstreams is one piece of work, frozen as a TaskContractRecord that names a capability and a role and never a provider, a model or a specialist. Who executes a workstream is a project assignment, decided on one of two paths:

- **Request the capability from the Taskforce.** The work is proposed and nobody is named. The Taskforce resolves an available retained member that holds a seat for that capability and role.
- **Explicitly select an outside specialist.** The Project Manager names the specialist for that workstream. The assignment is specific to the project.

The Taskforce never brings an outside specialist onto a project: when no retained seat can take a workstream, it says so and the work waits for the Project Manager.

An assignment creates no Taskforce membership. When the project completes, every participation ends.

### Taskforce

The persistent engineering organization: retained membership, retained seats, capability history, availability, and the resolution of retained capabilities.

```text
outside specialist
→ verified work
→ CapabilityDemonstration
→ RETAIN_SPECIALIST
→ TaskforceMembership
→ retained seat
```

Successful work does not retain anyone by itself. Verified work becomes a capability demonstration that belongs to the specialist; only an explicit retention decision that binds qualifying demonstrations creates membership and a seat. A specialist can equally be let go after a task, with no membership change at all. A seat with repeated verified work becomes established, which ranks it higher.

Retention changes who is staffed and nothing else. A retained member's work goes through the same contract, attempt, evidence, verification and permit as anyone's.

### AutoSwarm

The execution substrate. Autobot hands it an execution manifest built from the ledger; AutoSwarm verifies it, claims the call and launches the specialist's session. Which provider, model and effort that session runs on is resolved last, by the routing policy, and recorded with the assignment before the attempt is dispatched. Each specialist works in an isolated Git worktree at a frozen revision.

### The authority plane

| Component | Role |
|---|---|
| Registry | admits and freezes TaskContractRecords; nothing runs without one |
| Sequencer | owns every state transition, attempt, fence, permit and effect intent |
| Collector | classifies the evidence of an attempt into an ExecutionReceipt |
| Verifiers | registered deterministic programs; their receipts are the only verification |
| Git | the source of truth for candidate trees and the canonical ref |
| HELIOS | the command gate: admits or denies every shell command a specialist runs, and records it |

The authority plane decides whether work may proceed and whether a result can be trusted. It is specified in [docs/architecture](docs/architecture) (Chapters 0 to 4) and does not depend on anything above it.

### READ_ONLY and SOURCE_MODIFYING

Every contract has a finalization class.

- **READ_ONLY** work investigates or reviews. Its result is a report and structured findings. It is accepted when verification passes.
- **SOURCE_MODIFYING** work changes the repository. The specialist commits in its worktree; the candidate is checked against the frozen scope, verified, and published by a ref compare-and-swap only inside a publication permit that you grant.

## How a run goes

```text
objective
  -> Coordinator classifies: DIRECT | ENGINEER_ELEVATED | ORCHESTRATOR_ELEVATED
  -> ORCHESTRATOR_ELEVATED: Opus 4.6 [1M] constructs the node DAG
  -> ENGINEER_ELEVATED: Opus Engineer produces the technical design
  -> each TASK_CONTRACT node is admitted to the authority plane
  -> Taskforce resolves a retained seat or outside specialist
  -> AutoSwarm launches the specialist in an isolated worktree
  -> evidence is collected; registered verifiers run; the Sequencer concludes
  -> READ_ONLY: accepted.  SOURCE_MODIFYING: your permit, then the ref moves
  -> the result becomes a CapabilityDemonstration; findings are admitted
  -> the Engineer may issue RETAIN_SPECIALIST for qualifying work
  -> retained specialists earn Taskforce seats; repeated qualifying -> ESTABLISHED
  -> dependency edges release when predecessors accept
  -> the loop is stable when nothing is left to do and nothing is in flight
```

## Run it

You need Python 3.8 or newer (the legacy coordinator needs 3.11), the `autoswarm` CLI, and the `claude` CLI logged in to its subscription. From the checkout root, in PowerShell:

```powershell
$env:PYTHONPATH = 'src'
python -m intelligence.cli seed
python -m intelligence.cli bootstrap
python -m intelligence.cli engineer `
  --coordinator --orchestrator `
  --objective-id <id> `
  --objective "<what you want engineered>"
```

| Command | Does |
|---|---|
| `seed` | admits the repository declaration, the delegation and the verifier and transform registrations |
| `bootstrap` | assembles the runtime, checks every dependency, prints the specialist catalog |
| `preflight` | resolves the execution routes: the Engineer's model, each route, how each specialist is routed, whether AutoSwarm can launch each provider. `--probe` exercises each route with one small real session and asks the Engineer's model one question |
| `engineer` | gives the Engineer an objective and runs until the loop is stably quiet. Without `--objective` it resumes the active objectives |
| `run` | runs one task you name yourself, with no reasoning |
| `status` | objectives, sequences, active projects and the Taskforce, without taking the lease |
| `objectives` | lists objectives; `--close <id>` closes one of yours once all its work is COMPLETED |
| `taskforce` | the persistent organization: members, seats and the evidence behind each, outside specialists, assignment history, projects and their teams |
| `query` | the query layer, as JSON |

### Dashboard

```powershell
$env:PYTHONPATH = 'src'
python -m intelligence.dashboard --interval 1
```

Live terminal UI showing all five organizational layers. Keys: `1`-`5` focus a section (Coordinator, Orchestrator, Engineer, Taskforce, DAG), arrows scroll, `Esc` returns to overview, `a` expands ACTIVITY, `q` quits.

`engineer` and `run` take `--authorize-publication`, which is your publication permit for the verified SOURCE_MODIFYING work of that run, and `--max-wall-seconds`, which bounds how long the loop waits for specialists.

### Configuration

Machine paths live in `config.local.toml` (gitignored). The specialist catalog is in `config.toml`:

```toml
# Opus 4.6 [1M] specialists — complex reasoning
[specialists.opus-architect]
route = "opus-4-6-1m"
offers = ["architecture:reviewer"]

[specialists.opus-engineer]
route = "opus-4-6-1m"
offers = ["software_implementation:builder", "documentation:builder"]

[specialists.opus-security]
route = "opus-4-6-1m"
offers = ["security_review:reviewer"]

# Luna 6 Max specialists — high-throughput execution
[specialists.luna-investigator]
offers = ["research:investigator"]

[specialists.luna-reviewer]
offers = ["verification:reviewer"]

[taskforce]
established_threshold = 3
```

Each specialist's handle is its organizational identity. The execution route is resolved at dispatch time. Being in the catalog makes a specialist available; Taskforce membership and seats are earned through qualifying work.

### Execution routing

Which provider and model a session runs on is decided last, after staffing, by the `[routing]` policy in `config.toml`:

| Who | Runs on |
|---|---|
| Engineer, and the Engineer as Project Manager | `[engineer] model`, else `[models] planner` |
| every specialist session: a retained member, an outside specialist, an independent reviewer in its own session | the `default` route |
| work the organization escalates, and single assignments the Project Manager asks to escalate | the `escalation` route |
| specialist sessions while the default route is switched off | the `fallback` routes, in order |
| lightweight utility work | the `utility` route |

A route is a provider (`agent`), a `model`, an `effort` and native session arguments. Replacing a route changes no task contract and no specialist, membership or seat: the next attempt runs on the new route, and each earlier attempt keeps the route it ran on. A specialist may name a route of its own (`route = "<name>"`).

The Project Manager can ask for escalated execution of one assignment. It says what the engineering problem needs (`"escalation": "high_context"` or `"deep_reasoning"`) and why; the routing policy resolves that to the escalation route. The Engineer never names a provider or a model and is not shown any.

### Tests

```powershell
$env:PYTHONPATH = 'src'
python -m pytest tests/autobot -q
python -m pytest tests/test_coordinator.py -q
```

## Documentation

| Read | For |
|---|---|
| [docs/architecture/orchestration](docs/architecture/orchestration/README.md) | the Engineer, projects and teams, the Taskforce, staffing, supervision, the query model |
| [docs/architecture/chapter0](docs/architecture/chapter0/README.md) to [chapter4](docs/architecture/chapter4/README.md) | the authority plane: principals, capabilities, contracts, the runtime, receipts, verification |
| [docs/implementation](docs/implementation) | production pilot records and phase handoffs |
| [docs/LEGACY_COORDINATOR.md](docs/LEGACY_COORDINATOR.md) | the legacy coordinator |

## Layout

```text
src/intelligence/           Coordinator, Orchestrator, Engineer, Taskforce, production runtime
src/intelligence/dashboard/ live terminal dashboard (5-section UI)
src/autobot/                authority plane (Registry, Sequencer, CAS, transport)
src/contract_library/       canonical bytes, digests and schema validation
src/coordinator/            the legacy coordinator
tests/autobot/              authority and intelligence suites (1223 tests)
docs/architecture/          chapters 0 to 4 and orchestration
```

Runtime state is never committed: `autobot.db` (the authority ledger), `intelligence.db` (planning, project and Taskforce state), `store/` (content-addressed bytes).

## The legacy coordinator

Autobot began as a backlog coordinator: it read a curated backlog, had a planner pick tasks, launched each through AutoSwarm and parked the result for a human to close. That code is `src/coordinator`, with the `coordinator`, `coordinator-discover`, `coordinator-authorize` and `coordinator-verify` console scripts, and it still runs.

It is separate from the current runtime. Nothing in `src/autobot` or `src/intelligence` imports it, it has its own ledger (`coordinator.db`), and the `[routes.*]`, `[caps]`, `[cooldowns]`, `[triggers]` and `[safety]` sections of `config.toml` are its configuration. One setting is shared: `[models] planner` also names the Engineer's model when `[engineer] model` is not set. Its guide, including how to write prompts for cold sessions and how the command gate behaves, is [docs/LEGACY_COORDINATOR.md](docs/LEGACY_COORDINATOR.md).

## License

MIT, see [LICENSE](LICENSE).
