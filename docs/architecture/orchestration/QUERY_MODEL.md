# The system query model

Autobot reasons over structured engineering artifacts. Every question
the Engineer or an operator asks is a read over identities that already
exist; `intelligence.query` joins them and writes nothing.

Protocol objects are read from the authority ledger through the
read-only connection. Intelligence-plane objects are read from the plan
store. The Engineer never receives a database handle: it receives the
`PlanningView` these functions build.

Two reports stay separate, because the things they report are separate:

| Report | What it is | Function |
|---|---|---|
| the project team | temporary: who takes part in which project, as what | `project_state`, `projects_report` |
| the Taskforce | persistent: who is a member, in which seats, on what evidence | `taskforce_report` |

## From the command line

```powershell
$env:PYTHONPATH = 'src'
python -m intelligence.cli query <kind> <identity>
python -m intelligence.cli status
python -m intelligence.cli taskforce
python -m intelligence.cli taskforce --json
```

The reporting commands take no lease and start no driver, so they are
safe beside a running organization. `query` output is JSON.

## Queries

| Query | `query` kind | Function | Answers |
|---|---|---|---|
| by objective | `objective <objective_id>` | `objective_state` | status, constraints, budget and usage, its projects, tasks and their states, dependencies, decisions, findings, accepted results, open questions, risks |
| by project | `project <project_id>` | `project_state` | the solution it realizes, its workstreams with their dependencies and state, each workstream's failed attempts with what the provider itself reported, every assignment and who decided it, staffing decisions, the team and whether each participation is open |
| every project | `projects` | `projects_report` | each project on record with its team |
| by task or contract | `sequence <sequence_id>` | `by_sequence` | the decision that created it, its frozen identity, state, attempts, assignments, verification, findings, demonstrations, repair decisions |
| by attempt | `attempt <attempt_id>` | `by_attempt` | the call, the assignment, witnesses, receipts, candidate, verification rows, supervision decisions, findings |
| by subsystem | `subsystem <name>` | `by_subsystem` | tasks against it, findings that name it, specialists with verified experience in it |
| by finding | `finding <finding_id>` | `findings`, `finding_consequences` | the finding, who produced it, and the decisions that cite it |
| by dependency | `dependencies <objective_id>` | `dependencies` | each frozen edge, the task it points at, whether authority has released it |
| by specialist | `specialist <specialist_id>` | `by_specialist` | routing, whether it is a member and in which seats, the projects it took part in, assignments, demonstrations, decisions, findings |
| by capability | `capability <name>` | `by_capability` | who holds a retained seat for it, who has demonstrated it, which outside specialists offer it |
| by seat | `seat <seat_id>` | `by_seat` | status, the membership it belongs to, history, demonstrations, decisions, assignments |
| by verification outcome | `verification-outcome PASS` | `by_verification_outcome` | every admitted row with that outcome, with its task and specialist |
| by accepted result | `accepted <objective_id>` | `accepted_results` | what authority accepted, with candidate, acceptance and release references |
| artifact graph | `graph <objective_id>` | `artifact_graph` | nodes and typed edges for one objective |
| the organization | `taskforce` | `taskforce_report` | members, retained and established seats, available retained capabilities, inactive seats, outside specialists, retention candidates, demonstrations, workforce decisions, assignment history |

## Provenance questions

| Question | `query` kind | Reads |
|---|---|---|
| Why does this specialist execute this attempt? | `why-selected <assignment_id>` | who decided (`TASKFORCE` or `PROJECT_MANAGER`), the staffing decision and its rationale, the request, the candidates on record, and — for a retained seat — the outside specialist a Project Manager would have had to select without the membership |
| Why was this task created? | `why-created <sequence_id>` | the proposal: rationale, the snapshot it reasoned over, the findings and events it cited, the policy that reasoned |
| Why is this specialist a member? | `why-retained <seat_id>` | the retention decision that created the membership and the seat, its rationale and the demonstrations it bound |
| In which role was this decided? | `objective <objective_id>` | `decisions[].acting_as`: `ENGINEER` or `PROJECT_MANAGER` |
| Why was a member replaced on a repair? | `sequence <sequence_id>` | the repair decision's staffing word and the next assignment's `selected_reason.directive` |
| Which finding triggered this work? | `why-created`, `finding` | `provenance.findings` on the proposal; `finding_consequences` in the other direction |

## Organization questions

| Question | Query |
|---|---|
| Who is on this project's team, and who of them is a Taskforce member? | `project <project_id>` -> `team[].joined_as`, `team[].taskforce_member` |
| Which specialists took part in a project and were never retained? | `projects` -> `team[]` where `taskforce_member` is false |
| Which specialists have demonstrated Git repository operations? | `capability git_repository_operations` -> `demonstrated_by` |
| Which members have worked successfully on Autobot? | `taskforce` -> `retained_members[].demonstrated_environments` |
| Which outside specialists have verified work awaiting a decision? | `taskforce` -> `retention_candidates` |
| Which specialists required repairs? | `verification-outcome FAIL` -> `specialist_id` |
| Which specialist has experience with this subsystem? | `subsystem <name>` -> `experienced_seats` |
| What evidence justified retaining this specialist? | `why-retained <seat_id>` |

## What the Engineer sees

`query.project_views`, `query.engineering_view` and `TaskforceRoster.view`
are the projections `EngineerService.build_view` puts into the
`PlanningView`:

| View field | Built from |
|---|---|
| `projects[objective]` | `project_state` of the current project: solution, workstreams, dependencies, failed attempts and the provider's report on each, assignments, team |
| `projects[objective].needs_staffing` | `staffing_needs`: READY workstreams no available retained seat can take, each with its cause and the outside specialists that could be selected |
| `engineering[objective].accepted_results` | `accepted_results` |
| `engineering[objective].failed_assertions` | verification rows that are FAIL or INCONCLUSIVE |
| `engineering[objective].open_questions` | findings no decision took up, halted sequences, waits on you |
| `engineering[objective].decisions` | `decisions` |
| `engineering[objective].risks` | high-significance defect and security findings |
| `taskforce.members` | memberships |
| `taskforce.retained_seats`, `inactive_seats` | seat views, with availability |
| `taskforce.outside_specialists` | the catalog, less the work each already holds a retained seat for, with each specialist's verified record |
| `taskforce.available_capabilities` | per capability: retained seats, retained and free, outside specialists |
| `taskforce.active_assignments` | open assignments |
| `taskforce.recent_demonstrations` | the latest demonstrations with their criteria |
| `taskforce.retention_candidates` | outside specialists with qualifying demonstrations no decision has considered |

The production policy removes handles, providers, models and routes from
all of it before a model sees it: routing is not something the Engineer
reasons over. An assignment's `route` (the route its attempt was
dispatched on: name, provider, model, effort) is in the operator's view
and never in the Engineer's. What the Engineer does see is whether an
assignment was escalated, in its own word (`escalation`: `high_context`
or `deep_reasoning`), because asking for that is its decision.

Semantic retrieval is not part of this model. The state is relational and
every answer above is an exact read.
