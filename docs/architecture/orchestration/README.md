# Engineering and orchestration

The layer above the authority plane.

| Who | Does what |
|---|---|
| Engineer | solves technical problems |
| Engineer as Project Manager | organizes a temporary team around a solution |
| Taskforce | maintains the persistent retained specialist organization |
| Outside specialists | may take part in a project without Taskforce membership |
| AutoSwarm | executes assignments |
| Authority Plane | independently proves results |

| Document | Read it for |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | the model, the Engineer's two roles, the project and its team, the two staffing paths, the Taskforce, supervision, authority integration, the artifact graph, decisions and findings |
| [QUERY_MODEL.md](QUERY_MODEL.md) | how project, engineering and Taskforce information is queried |
| [PRINCIPALS.md](PRINCIPALS.md) | how the frozen `planner` and `agent_factory` principals map onto the model |
| [PRODUCTION_PROOF.md](PRODUCTION_PROOF.md) | the production runs that demonstrate it, with their identities |
| [RUNBOOK.md](RUNBOOK.md) | operating the `engineer` command: flags, exit codes, timeouts, provider failures, resuming, preflight, execution routing, the sandbox edge. Written, reviewed, corrected and kept current by the organization's own specialists (Pilots 2, 4, 5 and 6) |
| [ENGINEERING_LOG_20261124.md](ENGINEERING_LOG_20261124.md) | driver instrumentation, transport fixes, operational tooling, the `--trace` flag, the session monitor, the `--supersede-sequence` command, and the decisions behind them |

## Source

| Module | Role |
|---|---|
| `intelligence/engineer.py` | the Engineer service: cycles, disposal of every proposal class, projects and workstreams, budgets, frozen prompts, reasoning records, the standing project-manager rule |
| `intelligence/policy.py` | the production Engineer policy and its reasoner |
| `intelligence/proposals.py` | typed proposals, the role each belongs to, and what each may name |
| `intelligence/compiler.py` | work proposal to TaskContractRecord candidate |
| `intelligence/workforce.py` | the specialist catalog, the roster (memberships, seats, project assignments, teams), the seat transition table, the resolver |
| `intelligence/demonstrations.py` | capability demonstrations from ledger evidence |
| `intelligence/taskforce.py` | the pump: applying staffing, staged allocation, supervision, collection, the authority arcs |
| `intelligence/supervision.py` | the permission-request policy, for a provider that asks before each tool use and for one that runs in a sandbox |
| `intelligence/routing.py` | the execution-routing policy: default, escalation, fallback and utility routes; the escalation classes a Project Manager may ask for |
| `intelligence/preflight.py` | resolving and probing the routes before work is dispatched |
| `intelligence/prompts.py` | specialist prompt composition and the output contracts |
| `intelligence/query.py` | the query layer |
| `intelligence/driver.py` | the loop, including the form that waits |
| `intelligence/runtime.py`, `intelligence/cli.py` | production assembly and the `autobot-authority` command |
| `intelligence/trace.py` | the driver step tracer: real-time pipeline visibility activated with `--trace` |
| `intelligence/monitor.py` | the session monitor: standalone read-only supervisor with section scroll |
| `intelligence/planstore.py` | `intelligence.db`, schema v3 |
| `intelligence/chief.py` | the import path the Engineer had when it was called the Chief Engineer |

## Tests

| Suite | Pins |
|---|---|
| `tests/autobot/test_architecture_conformance.py` | the organizational model, one sentence per test: a TaskContractRecord names no provider, model or specialist; the two staffing paths; an assignment and a demonstration create no membership; explicit retention does; a retained member is resolved later; retention weakens no verification; a review's author is prohibited on both paths; a completed project has no team while membership persists; the six identities are separate records |
| `tests/autobot/test_taskforce_workforce.py` | the roster at its own surface: identity, the staffing paths, membership separation, retention, release with and without a membership change, the project team, the seat transition table, established status, provider replacement, staffing words, what a proposal may name, supervision, findings, prompts, migration, budgets |
| `tests/autobot/test_taskforce_orchestration.py` | the full plane: retention and reuse, release of a successful specialist, explicit selection, multi-workstream concurrency and dependencies, repair staffing, restart recovery at every stage, authority separation, the waiting loop, budgets, frozen prompts, independence, the query layer, the CLI |
| `tests/autobot/test_chief_engineer_policy.py` | the production policy end to end with a scripted reasoner |
| `tests/autobot/test_model_routing.py` | routing: the policy and the catalog; the route replaced between two pieces of work and between two attempts of the same work, with the TaskContractRecord, the assignment, the specialist, the membership, the seat and the demonstrations unchanged; the route recorded before dispatch and repeated across a stop, arguments included; an outside specialist and an independent review on the default route; the Engineer's model apart from every specialist route; escalation by rule and by the Project Manager's ask; supervision of a sandboxed provider; preflight; the v3 migration |
| `tests/autobot/test_independent_review.py` | independent review: the contract keeps a session in its own shell; a review states whether it examined the work, and one that could not is recorded as that, earns no qualifying demonstration and cannot close its project; a reviewer is given the accepted work, what that work was given, and a note saying what its workspace holds; a reviewer's session is READ_ONLY to supervision whatever it reviews, and the builder's sandbox-edge rule is unchanged; one assignment is one session: every contract says so, and a tool that starts another agent is refused where a provider asks before each tool use; a session stays on this machine: every contract says so, and a connector, the web, a browser or a desktop is refused where a provider asks before each tool use |
