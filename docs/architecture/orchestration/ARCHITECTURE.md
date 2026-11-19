# Autobot: the engineering and orchestration architecture

Autobot is an autonomous engineering and orchestration system.

**The Engineer solves problems and may become the Project Manager needed
to realize a solution. A project may use both retained Taskforce members
and outside specialists. Project participation is temporary; Taskforce
membership is persistent and earned through explicit evidence-backed
retention. Autobot coordinates the project, AutoSwarm executes the
sessions, and the authority plane independently determines what work
becomes accepted system state.**

This document is the definitive description of the system above the
authority plane. The authority plane itself — what a frozen contract is,
how an attempt is fenced, what a receipt binds — is specified in
Chapters 0 to 4 and is unchanged here.

Decisions are numbered D-TF-n and findings F-TF-n; both are listed at
the end.

## 1. The model

```text
AUTOBOT
    │
    Engineer
    │
    ├── technical problem solving
    │
    └── Project Manager role when coordination is required
            │
            ├── project/work graph
            ├── retained Taskforce participants
            └── outside project-specific specialists

Taskforce
    ├── persistent retained membership
    ├── retained seats
    ├── capability demonstrations
    ├── availability
    └── organizational learning

AutoSwarm
    └── execution substrate

Authority Plane
    └── independently verifies and accepts work
```

| Who | Does what | Where |
|---|---|---|
| Engineer | solves technical problems | `engineer`, `policy`, `proposals`, `compiler`, `query` |
| Engineer as Project Manager | organizes a temporary team around a solution | the same seam; `OPEN_PROJECT`, `ASSIGN_SPECIALIST`, `RETAIN_SPECIALIST`, `RELEASE_SPECIALIST`, `PROJECT_COMPLETE` |
| Taskforce | maintains the persistent retained specialist organization | `workforce`, `demonstrations` |
| Outside specialists | may take part in a project without Taskforce membership | the specialist catalog; `project_assignments`, `project_team` |
| AutoSwarm | executes assignments | `taskforce` (the pump), `supervision`, `prompts`, `autobot.autoswarm_transport` |
| Authority Plane | independently proves results | `src/autobot`: Registry, Sequencer, Collector, Verifiers, Git, Helios |

Six identities stay separate, in every table and every interface:

| Identity | What it is | Lives in |
|---|---|---|
| TaskContractRecord | frozen work authority; independent of provider, model and specialist identity | the authority ledger |
| Project | a temporary coordinated engineering undertaking | `intelligence.db` |
| ProjectAssignment | temporary participation in one project workstream, for one attempt | `intelligence.db` |
| TaskforceMembership | persistent organizational membership | `intelligence.db` |
| TaskforceSeat | a retained organizational capability and role | `intelligence.db` |
| AttemptContract | one authority-bound execution | the authority ledger |

Everything in `src/intelligence` holds no authority. It reads the ledger
through a read-only connection and changes it only by calling the public
Registry and Sequencer APIs, which enforce every rule regardless of who
calls.

## 2. The Engineer

The Engineer is the technical problem-solving intelligence. It
investigates problems, develops solutions, weighs tradeoffs and
determines the technical work a solution requires. It consumes structured
state — never chat history, never a database handle — and produces typed
proposals.

When realizing a solution needs coordinated work, the Engineer assumes
the **Project Manager** role for that project: it opens the project, lays
out its workstreams and their dependencies, and composes a temporary team
from retained Taskforce members and outside specialists.

It never selects a provider or a model, never puts a specialist into a
work requirement, does not certify its own conclusions, and cannot mutate
authority.

### 2.1 The reasoning loop

```text
   authority change            (journal: admitted, verified, failed, completed ...)
   engineering finding         (a specialist's structured finding)
   objective                   (yours)
   workforce event             (a demonstration, a workstream nobody can take,
                                a refused assignment, a spent budget)
          |
          v
   Engineer cycle  --------- PlanningView (plain data) --->  policy
          |                                                    |
          |  <------------- typed proposals -------------------+
          v
   recorded BEFORE disposal (cycle_reasoning)
          |
          +--> OPEN_PROJECT          --> the project
          +--> work classes          --> compiler --> Registry admission
          |                              (and a workstream of the project)
          +--> ASSIGN_SPECIALIST     --> an assignment decision
          +--> REPAIR_TASK           --> an authorization for one more attempt
          +--> RETAIN / RELEASE      --> the Taskforce roster
          +--> waits, completion     --> the plan store
          |
          v
   pump: apply the staffing -> launch -> supervise -> collect
          |
          v
   authority: evidence -> verification -> finalization -> acceptance -> release
          |
          v
   new authority events, findings, demonstrations  ---> the next cycle
```

A cycle opens only when something new exists. Everything that woke it is
consumed exactly once.

### 2.2 The seam

```text
EngineerPolicy.plan(view: PlanningView)  -> [typed proposal, ...]
EngineerPolicy.staff(view, proposals)    -> [ASSIGN_SPECIALIST, ...]
```

The two methods mirror the two roles. `plan` is the whole answer of a
reasoning policy — technical work and project-management decisions alike.
`staff` is where a policy that does not reason about staffing gets its
Project Manager decisions from; its default is the standing rule
(section 3.5).

`PlanningView` carries:

| Field | Content |
|---|---|
| `authority` | the `project_state()` snapshot: sequences, attempts, obligations, permits |
| `snapshot` | the journal entry count and head hash the cycle reasoned over |
| `events`, `findings`, `workforce_events` | what is new |
| `objectives` | the active objectives, with your design constraints |
| `open_proposals`, `workstreams` | waits and queues |
| `projects` | per objective, the current project: its solution, its workstreams with their dependencies and state, the project assignments, the team, and the workstreams that wait for a staffing decision (with the outside specialists that could be selected) |
| `taskforce` | members, retained seats and their availability, inactive seats, outside specialists and what each offers and has demonstrated, available retained capabilities, active assignments, recent demonstrations, retention candidates |
| `budget` | each objective's limits and what it has used |
| `engineering` | per objective: verified and accepted results, failed assertions, open questions, decisions taken, risks |

All of it is plain data built by the query layer (section 9). A test
asserts the view has no attribute that could reach a database, the
Registry, the Sequencer or the store, and that it serializes as JSON.

### 2.3 Typed proposals

| Class | Role | What it is | Disposal |
|---|---|---|---|
| `INVESTIGATE`, `CREATE_TASK`, `REQUEST_REVIEW` | Engineer | one work requirement | compiled to a TaskContractRecord candidate, submitted to the Registry, and recorded as a workstream of the project |
| `DECOMPOSE_TASK` | Engineer | ordered parts with dependencies; a part may be an independent review of an earlier part (`review_of_part`) | expanded into contracts; later parts carry frozen edges on earlier ones, and a review part becomes a `REQUEST_REVIEW` of the earlier part's frozen record |
| `REPAIR_TASK` | Engineer | one more attempt of existing work, optionally with a staffing word | an authorization the pump consumes |
| `WAIT_FOR_AUTHORITY` | Engineer | the registered authority path is yours | a visible wait |
| `OPEN_PROJECT` | Project Manager | the project that realizes a stated solution | the project |
| `ASSIGN_SPECIALIST` | Project Manager | who executes a workstream's next attempt | an assignment decision |
| `RETAIN_SPECIALIST` | Project Manager | retain a specialist, binding demonstrations | Taskforce membership and a retained seat |
| `RELEASE_SPECIALIST` | Project Manager | release or deactivate a specialist | the seat (a member), or project participation only (an outside specialist) |
| `PROJECT_COMPLETE` | Project Manager | the project is done | closes the project and its objective |

`proposals.ACTING_AS` is that role column; every recorded decision carries
it.

What may name whom:

```text
work requirement / TaskContractRecord:
    provider forbidden
    model forbidden
    specialist identity forbidden
    (it names a capability and a role)

project assignment (staffing):
    specialist identity allowed
    provider/model remain execution-routing details
```

`validate_proposal` refuses a provider, a model, a specialist, a seat or
any staffing in a work requirement and in the proposal that carries it. A
project assignment names a specialist in `staffing.specialist_id` and is
refused if it names a provider or a model. Tests scan every admitted
TaskContractRecord of a completed project, key by key and value by value,
for the route and the people that executed it, and find none.

`RETAIN_SPECIALIST` and `RELEASE_SPECIALIST` reach no Registry or
Sequencer call. A test runs a cycle that disposes a retention and asserts
that nothing was submitted and the authority journal did not grow.

### 2.4 The production policy

`policy.ReasoningEngineerPolicy` is the production implementation of the
seam (D-TF-9). It renders a structured projection of the view, asks a
reasoning model, and converts the answer into typed proposals.

Reasoned: the solution and the work it requires, the dependency graph,
capabilities, who is on the project team, whether demonstrated work earns
retention, repair staffing, how the plan changes when verified findings
arrive, completion.

Deterministic: every proposal's envelope (the snapshot and objective),
resolving a review target, a dependency or a workstream named by key into
the frozen record edge or the sequence, refusing any identity the view
does not contain (a specialist that does not exist or does not offer the
work, the author of the work under review, a provider or a model
anywhere), the order a batch is applied in (the project, then workforce
decisions, then work, then assignments), binding a retention to the
specialist's qualifying demonstrations, the completion guard, and one
reflex — verified SOURCE_MODIFYING work at your publication permit is
reported as `WAIT_FOR_AUTHORITY` without a model call.

The model is given no tools. The production reasoner is the subscription
`claude` CLI in print mode with `--tools ""`, the same login the
specialists run under; the model name comes from configuration
(`[engineer] model`, else `[models] planner`). An unusable answer is
returned to the model once with its problems; a proposal that still does
not validate is dropped and recorded.

Routing never reaches the Engineer (D-TF-22). A specialist's handle may
spell a provider or a model, so the projection removes handles, providers
and models: the Engineer selects people by identity, by what they offer
and by their verified record.

Routine progress the Engineer set in motion (a contract admitted, work
made ready, a specialist it just assigned or retained) wakes the cycle
but costs no model call. A workstream waiting for a staffing decision is
always reasoned over.

### 2.5 Exactly-once reasoning

A model does not answer the same question the same way twice, so the
plane may not ask twice. The policy's proposals are written to
`cycle_reasoning` before any of them is disposed, keyed by a digest of
exactly what woke the Engineer. A cycle interrupted after that point is
replayed from the record; the policy is not called again (D-TF-10).

A well-formed proposal that cannot be compiled or applied is recorded as
refused with the reason and the Engineer is told; it never takes the
cycle down.

### 2.6 Autonomy bounds

| Bound | Where |
|---|---|
| proposals per cycle | `EngineerService.max_proposals_per_cycle` |
| typed proposals only | `validate_proposal` |
| admission, scope, delegation | the Registry |
| every transition | the Sequencer |
| publication | your permit (cap-36) |
| retry and repair budgets | the frozen record |
| one reasoner at a time | the plan store's driver lease |
| runaway loop | the driver's active-step guard |
| engineering work per objective | the objective budget, below |

The objective budget (D-TF-11) closes F-CE-2. Each objective has limits on
contracts created, attempts launched, outside specialists assigned,
repairs and retention decisions. Usage is derived from durable rows, never
stored as a counter. A proposal past a limit is recorded as refused with
the reason and the Engineer is told; once you raise the limit the same
intent is admitted. The limits are yours (cap-47).

## 3. The project

A project is a temporary coordinated engineering undertaking: the
Engineer's realization of its solution to one objective (D-TF-16).

```text
   Objective
      |
   Project                     opened by OPEN_PROJECT; one ACTIVE per objective
      |
      +-- Workstream           one piece of work; references its proposal,
      |      |                 and through it the frozen TaskContractRecord
      |      +-- ProjectAssignment     who executes one attempt of it
      |
      +-- Team                 everyone taking part, as a retained member
                               or as an outside specialist
```

Work proposed before any project was opened is organized under a project
the service opens implicitly, and an `OPEN_PROJECT` that follows adopts
it; the record says which happened. The production policy opens the
project explicitly — that is where the solution is stated.

### 3.1 Two staffing paths

```text
   Engineer/PM
   → requests capability from Taskforce
   → Taskforce resolves a retained member

   Engineer/PM
   → explicitly selects outside specialist
   → project-specific assignment
```

A work proposal with no assignment is a capability request. The pump asks
the Taskforce, which resolves an available retained seat for that
capability and role — and only a retained seat. **The Taskforce never
brings an outside specialist onto a project** (D-TF-18): composing the
temporary team is the Project Manager's decision.

When no retained seat can take a workstream, the pump says so
(`staffing_unavailable`, with the cause and the outside specialists that
could be selected) and the work waits. The Project Manager then decides:

```text
   {"class": "ASSIGN_SPECIALIST", "workstream": "<key>",
    "staffing": {"mode": "outside_specialist", "specialist_id": "sp_…"}}
```

An assignment may be decided in the same answer that creates the work
(the workstream is named by the Project Manager's key), in which case the
Taskforce is never asked.

| Cause of a wait | Meaning |
|---|---|
| `no_retained_seat` | no member holds this capability and role |
| `retained_seats_prohibited` | members hold it, none may be used (independence) |
| `retained_seat_busy` | a member holds it and is occupied |
| `specialist_busy` | the selected specialist is occupied; the work waits for it |
| `selection_refused` | the selection can never be honored; the decision is closed and the Engineer is told |

### 3.2 The project assignment

A ProjectAssignment records, before anything launches: the project and
workstream, the specialist, the staffing path (`RETAINED_SEAT` or
`OUTSIDE_SPECIALIST`), who decided (`TASKFORCE` or `PROJECT_MANAGER`), the
specialist's standing at that moment, the Project Manager's decision when
there is one, the candidates on record, and — for a retained seat — the
outside specialist a Project Manager would have had to select without the
membership.

**An assignment creates no Taskforce membership** (D-TF-17). Assigning an
outside specialist writes a project assignment and a team row and nothing
else: no membership, no seat, no provisional standing. The schema has no
state for one.

Provider and model are read from the assigned specialist's route when the
attempt is dispatched — downstream of the staffing decision, and never
part of it (D-TF-19).

### 3.3 What binds an explicit selection

The Project Manager may select any outside specialist that exists, is in
the catalog, offers exactly that capability and role, was not released
from it for cause, and — for a review — did not do the work under review.
A selection that fails any of these is refused with the reason, at the
decision and again at launch.

A review is staffed independently of its subject (D-TF-15). The
specialists assigned to the reviewed record may not review it: the
Taskforce does not resolve them, the Project Manager may not select them,
and with nobody else eligible the review waits. Independence is not
traded for availability.

A specialist that holds a retained seat for the work is not selectable
*as* an outside specialist for it: the capability is requested from the
Taskforce.

### 3.4 Scheduling

A specialist executes one assignment at a time unless its route says
otherwise. A busy specialist is unavailable to every path, so one worker
is never dispatched into two executions. Independent READY work is
staffed in one sweep and runs concurrently on different specialists.
Dependent work is never staffed early: only READY sequences are staffed,
and readiness is the Sequencer's.

### 3.5 The standing rule

A policy that does not reason about staffing — the deterministic policies
of the test suites, the operator's single-task `run` command — gets its
Project Manager decisions from a declared rule (D-TF-20): for each
workstream no retained seat can take, the first **eligible** outside
specialist in catalog order, free ones first. Its decisions are ordinary
`ASSIGN_SPECIALIST` proposals, recorded with the rule's id
(`standing-outside-rule/v1`) and bound by everything in 3.3.

The reasoning Engineer does not use it. It selects for itself.

### 3.6 Repair staffing

A repair is one more attempt of the same workstream, authorized by a
`REPAIR_TASK` proposal. The proposal may carry a staffing word:

| `staffing.mode` | Effect |
|---|---|
| `taskforce` | a capability request (the default) |
| `different_member` | a capability request, excluding whoever did the last attempt |
| `same_member` | the specialist that produced the work repairs it, in whatever standing it has |
| `outside_specialist` | the named outside specialist joins |

### 3.7 The end of a project

`PROJECT_COMPLETE` closes the project and its objective once every
workstream is COMPLETED. Every team member's participation ends
(`project_completed`), staffing decisions never applied are closed, and
the assignment rows stay as history. Nothing in the Taskforce changes: a
member is still a member, an outside specialist is still outside
(D-TF-17).

A proposal that was refused at admission — by the Registry or by the
objective budget — never became work: it has no sequence. It stays on
record and the Engineer is told, but it is not a workstream to complete
and does not keep a project open. An admission still queued for you does.
A project with no admitted work at all is never complete.

A review counts only when it examined the work (D-TF-32). The frozen
prompt of every `REQUEST_REVIEW` carries a review contract: the reviewer
puts one line in its response, `AUTOBOT_REVIEW: EXAMINED` when it read
both the work under review and the source it must agree with,
`AUTOBOT_REVIEW: NOT_EXAMINED` when it could not. What it said is recorded
with its assignment when its result is collected and shown on the review
workstream as `review_verdict`: `EXAMINED`, `NOT_EXAMINED`, or `NOT_STATED`
when the response carried no such line. Nothing is inferred from prose.

Authority is not asked this question. A reviewer that reports truthfully
that it could not read the work has returned a valid result, and the
Registry, the Collector and the verifiers treat it as one: the review
sequence is verified, accepted and COMPLETED. What changes is what the
organization makes of it:

| Verdict | The reviewed work | The reviewer's demonstration | `PROJECT_COMPLETE` |
|---|---|---|---|
| `EXAMINED` | reviewed | judged as any work | allowed |
| `NOT_EXAMINED` | still unreviewed | NOT_QUALIFYING (`outcome useful` fails) | refused until another review of the same record is `EXAMINED` |
| `NOT_STATED` | the Engineer's to judge; it is told to treat it as unreviewed | judged as any work | allowed |

Another review is a new `REQUEST_REVIEW` of the same record: a new
contract, staffed independently again.

## 4. The Taskforce

The Taskforce is the persistent engineering organization. It owns retained
membership, retained seats, capability history, demonstrated environment
competence, availability, organizational learning, and the resolution of
retained capabilities.

### 4.1 Membership and seats

```text
   outside specialist
   → verified work
   → CapabilityDemonstration
   → RETAIN_SPECIALIST
   → TaskforceMembership
   → retained seat
```

A **TaskforceMembership** is one specialist's membership in the
organization. A **TaskforceSeat** is one retained capability and role that
member holds. Both come to exist through one thing only: an explicit
retention decision that binds qualifying demonstrations of exactly that
specialist, capability and role (D-TF-4). Successful project execution
alone implies nothing.

A specialist with no retained seat for a piece of work is OUTSIDE for it —
including a member, for work it holds no seat for (D-TF-23).

### 4.2 The seat lifecycle

```text
   (OUTSIDE) --RETAIN_SPECIALIST--> RETAINED --repeated verified work--> ESTABLISHED
                                       |                                     |
                                       +-------------- deactivate -----------+
                                                         |
                                                         v
                                                     INACTIVE --RETAIN_SPECIALIST--> RETAINED

   RETAINED, ESTABLISHED, INACTIVE --RELEASE_SPECIALIST--> RELEASED --RETAIN_SPECIALIST--> RETAINED
```

The table is `workforce.SEAT_TRANSITIONS`; each pair names the one cause
that may produce it, and nothing else moves a seat:

| From | To | Cause |
|---|---|---|
| OUTSIDE, RELEASED, INACTIVE | RETAINED | a retention decision |
| RETAINED | ESTABLISHED | the established threshold |
| RETAINED, ESTABLISHED | INACTIVE | a deactivation decision |
| RETAINED, ESTABLISHED, INACTIVE | RELEASED | a release decision |

No transition is caused by an assignment or by a demonstration. A
membership's status follows its seats: ACTIVE while any is RETAINED or
ESTABLISHED, INACTIVE while the best is INACTIVE, RELEASED when all are.

### 4.3 Release

Releasing a **member** releases (or reversibly deactivates) its seat, and
the membership follows.

Releasing an **outside specialist** after project work changes no
membership (D-TF-21): there is none. The decision is recorded
(`RELEASE_OUTSIDE`, with the reason and the work it considered) and the
specialist's participation in the project ends. Released for an
organizational reason it may be selected again; released for cause
(`poor_performance`, `environment_mismatch`) it is not selected for that
work again.

### 4.4 Retention candidates

An outside specialist whose verified, qualifying work no workforce
decision has considered is a *retention candidate*. That is a listing, not
a standing: nothing happens to a candidate until the Engineer decides to
retain it or let it go, and completing the project decides neither.

### 4.5 Identities

| Identity | What it is | Derived from |
|---|---|---|
| `specialist_id` | one engineering worker, across projects | the worker's handle |
| `membership_id` | that worker's Taskforce membership | the specialist |
| `seat_id` | one retained capability and role | specialist, capability, role |
| `project_id` | one project | objective, ordinal |
| `workstream_id` | one workstream | project, work proposal |
| `assignment_id` | one assignment of one attempt | sequence, attempt ordinal |
| `demonstration_id` | one concluded assignment | the assignment |

None of these is a TaskContractRecord, AttemptContract, call or native
session identity, and none substitutes for one. Provider and model are
attributes of a specialist. They can be replaced (`SpecialistPool.reroute`)
without changing the specialist, its membership, its seats or any contract
(D-TF-2): the activity is stable, the execution implementation is
replaceable.

### 4.6 Resolving a retained capability

Retained candidates for a request are ranked by seat status (ESTABLISHED
before RETAINED), then verified experience in the requested environment,
then in the requested subsystem, then qualifying demonstrations, then
fewest failures, then seniority. The first one that is available is
resolved; with none available the request waits.

Retention is preferential eligibility for future work. It changes who is
staffed and nothing else.

### 4.7 The registered principal

Chapter 0 froze seat, route and model selection as cap-14, owned by the
principal `agent_factory` and forbidden to the `task_force` agents. See
[PRINCIPALS.md](PRINCIPALS.md): `agent_factory` is not an actor of this
architecture; it is the wire identity of the staffing function, the frozen
registry is not rewritten, and records that cross the execution boundary
keep `selected_by: "agent_factory"` (D-TF-13).

## 5. Demonstrated capability

The original Taskforce membership principle: a specialist may earn a
retained seat after demonstrating the capacity to exert coherent action
within the relevant environment while working on real systems and complex
engineering activities.

`demonstrations.DemonstrationRecorder` turns that into criteria read from
the ledger:

| Criterion | Evidence |
|---|---|
| task completed | the sequence was accepted and released on this attempt |
| candidate accepted | the attempt's admitted candidate is what was accepted |
| verification passed | every admitted verification row is PASS or your ATTESTED |
| scope respected | SOURCE_MODIFYING: the candidate tree was captured after the frozen scope check |
| governance satisfied | the admitted ExecutionReceipt is COMPLETE |
| repair coherent | the attempt that verified is this one |
| outcome useful | the accepted work was created for an objective; and, for a review, the reviewer did not say it could not examine the work (section 3.7) |

All seven make a demonstration QUALIFYING. Anything else is
NOT_QUALIFYING, recorded with what failed.

A demonstration belongs to the specialist. It references the project, the
assignment, the sequence, attempt, call, TaskContractRecord identity, each
verification receipt and the execution, candidate, acceptance and release
receipts by their existing identities and content addresses, and records
the specialist's standing when it did the work. It copies none of them and
changes no membership. Nobody certifies a specialist: its own report is
not evidence.

An established seat is a retained seat with repeated independent verified
work: qualifying demonstrations in that capability and role on at least
`established_threshold` distinct sequences (default 3), the work that
earned the seat included. It is a ranking fact.

## 6. Execution

### 6.1 The allocation stages

The assignment is persisted before anything launches, and allocation is
staged so that a restart continues from the stage it reached (D-TF-7):

```text
   1  project assignment (and the team row)           plan store
   2  attempt created                 t-s06           Sequencer
   3  attempt reserved                t-a01           Sequencer
   4  execution route recorded                        plan store
   5  projection + dispatch, call_id  t-a02           Sequencer
   6  execution slot + references bound               plan store
   7  manifest built, session launched                AutoSwarm
```

Each stage is read back from the ledger before it is attempted. A restart
after stage 1 re-derives the same assignment row; after stage 5 it
relaunches on the same call_id, which the transport treats as the same
call. The route is recorded before the ledger binds the call to a
provider (stage 4), so a restart anywhere after it reads the route from
the assignment and never from a policy that may have changed since
(section 6.8).

### 6.2 The frozen prompt

A specialist's prompt is a declared specification input of the frozen
TaskContractRecord (D-TF-8). The Engineer authors guidance;
`prompts.compose_specialist_prompt` composes the complete prompt
deterministically; the service stores those bytes and the candidate binds
their address as `specialist_prompt`. The pump launches the specialist on
exactly those bytes, and verification proves it (INV-49).

A repair therefore executes the same frozen requirement. Different
instructions are a different contract.

The contracts appended to the guidance are one text for every provider
(D-TF-29) and say how to work in the environment, never what to conclude.
Four parts of them exist because production needed them. Every execution
contract tells the specialist to run each command in the default shell of
its session, that a shell which cannot start is not a refusal of the
command and not a reason to stop, and that an interpreter run on inline
code needs an authorization a specialist cannot give (D-TF-31). A
review carries the review contract of section 3.7. Every execution
contract says that one assignment is one specialist doing its own task in
one session (D-TF-34): it starts no sub-agent, other agent or other
session; the project objective it is shown is context, and the other
parts of it are other assignments the organization staffs; and a check of
its own work is not an independent review and may not be called one. And
every execution contract says that nothing which reaches outside the
machine's files and the session's shell is part of an assignment, even to
read (D-TF-35): no connector, app or plugin, no web access, no browser or
desktop control, no remote copy of the repository; what a session could
not read without one it has not read, and it says so.

Those last two rules are about what a provider offers a session beyond
what an assignment grants. They are enforced as far as each provider lets
Autobot enforce them.

| What the provider offers | A provider that asks before each tool use | A provider that runs in a sandbox and asks only at its edge |
|---|---|---|
| a tool that starts another agent (D-TF-34) | refused: no assignment grants it (section 6.3) | the contract's alone. On the default route's provider neither of its two multi-agent feature switches removes the tool when given as a session argument (two probe sessions, 2026-11-19) |
| a connector, app, plugin or MCP server, the web, a browser, a desktop (D-TF-35) | refused: no assignment grants it (section 6.3) | switched off by the route. The default route's session arguments turn those provider features off for the session; the session's own tool registry, read by a probe, held 158 tools without them and 5 with them (the shell, the patch tool, the clock, an image viewer and the shell's input, 2026-11-19). The contract covers what a later provider version or a newly configured server would add |

Where a rule is the contract's alone, a session can still break it.
Autobot does not detect that by itself: whether a session kept it is read
from the provider's own session record.

### 6.3 Supervision

A specialist runs with its provider's permission prompts on. Each request
it raises is answered by `supervision.decide`, deterministically, from the
frozen contract it is executing (D-TF-6):

| Request | Answer |
|---|---|
| file-reading tool | allowed inside the workspace, the repository and the command-gate tree |
| shell tool | allowed where the contract is SOURCE_MODIFYING or requires command governance; Helios then admits or denies each command |
| write to a command-gate file | allowed where the shell is |
| write, SOURCE_MODIFYING | allowed inside the isolated worktree and the frozen scope |
| any other write, any other tool | denied |
| a native question | left unanswered |

Every decision is recorded against the assignment and counted in its
demonstration.

"Inside" always means where a path resolves, never how it is spelled
(D-TF-30): a junction and its target are one place, and a link under the
workspace that leads out of it is outside.

That table is how a provider that asks before every tool use is
supervised (Claude Code). A provider that runs inside an operating-system
sandbox asks only to leave it (Codex), and its route declares the sandbox:
the isolated worktree and the command-gate pending directory are
writable, nothing else is. Commands inside the sandbox run without a
request and are still admitted or refused one by one by Helios. A request
is therefore an escalation — the command it names would run with no
sandbox around it — and the policy grants one kind (D-TF-28):

| Request from a sandboxed session | Answer |
|---|---|
| run a command outside the sandbox | allowed for SOURCE_MODIFYING work, for the two commands a specialist-owned candidate commit needs and nothing else: `git add [--] <path in the frozen scope>...` and `git commit -m <message>`. A linked worktree's index and objects live in the repository's Git directory, which the sandbox cannot write |
| a file change the sandbox refused | denied |
| a change of the session's permissions | denied |
| any request shape the policy does not know | denied |

The command is judged as what would run, not as the provider describes
it. All of these must hold, and the refusal says which did not:

- the request is a shell the operating system supplies, started to run
  exactly one script, and the provider's own description of the request
  is that same script;
- the script is one plain command: no second command, pipe, redirection,
  substitution, variable, wildcard or escaped quote. Quoted text may hold
  anything a shell would not expand;
- the command is `git add` or `git commit`, written as `git` and not as a
  path to a program, with only the options above; every path it names is
  a plain path inside the frozen scope (`git add .` and `git add -A` name
  none and are refused);
- it runs from the root of the assignment's worktree;
- the worktree is still the one the repository made: its `.git` is the
  pointer file into the repository's own worktree registry, so Git reads
  the repository's configuration and hooks and not ones the session
  wrote.

What this does not close is recorded with F-TF-26.

### 6.4 Workspaces

Every specialist works from a frozen source revision — the canonical
ref's value when the attempt was staffed — in an isolated Git worktree
(D-TF-14). SOURCE_MODIFYING work always did. READ_ONLY work does in
production too, because a shared checkout's files are not what authority
has accepted: a local publication moves the ref without touching them, so
a review dispatched after a change landed would otherwise read the old
content.

The revision is recorded on the assignment and carried in the manifest.
When an assignment settles, its worktree is given back and removed; a
SOURCE_MODIFYING worktree is kept until then, because until the work is
accepted the candidate commit is reachable only from it.

### 6.5 The loop that waits

`ContinuousDriver.run` reports a stable quiet state only when nothing is
left to do and nothing is in flight. A quiet step while a specialist is
still executing is a wait: the driver sleeps, looks again, answers
requests and collects the result when it arrives (D-TF-5).

### 6.6 A failed attempt

An attempt can fail before its specialist does any work: the provider's
usage window is exhausted, or the session dies. Authority records the
attempt FAILED with the provider's own result as its witness and accepts
nothing.

The work is not lost, and it does not restart itself (D-TF-24):

| Step | Who |
|---|---|
| the sequence returns to READY while the frozen retry budget lasts (t-s08) | the pump asks; the Sequencer checks both preconditions |
| the Engineer is told: `attempt_failed`, `retry_ready`, and a NOT_QUALIFYING demonstration that carries the provider's report | authority and workforce events |
| another attempt of the same workstream, on a new AttemptContract | only on the Engineer's word (`REPAIR_TASK`), staffed like any attempt |
| the retry budget is spent | the sequence stays where it is and the Engineer is told once (`retry_exhausted`); the work can continue only as a new contract |

One word authorizes one attempt. A second `REPAIR_TASK` for an attempt
that is already ordered is refused — it would outlive that attempt and
start a later one nobody ordered. Changing who does the pending attempt
is a staffing decision (`ASSIGN_SPECIALIST`).

The failed attempt stays part of the workstream. Project state lists it
under `failed_attempts` with what the provider reported, read from the
admitted witness (`demonstrations.provider_report`): its outcome, its
error text, the phase it failed in. The Engineer reads those words;
nothing in the deterministic layer interprets them. A provider failure is
no evidence about the specialist, and the production policy is told to
release nobody for one.

A reasoning model that cannot be reached answers nothing, and the loop
goes quiet with the objective still active. `engineer` without
`--objective` resumes it: the resume is a stimulus, so the Engineer
reasons again over the state as it stands. A resume may also change the
objective's budget (`--objective-id` with the budget flags): attempts the
provider failed spend budget through no fault of the plan.

### 6.7 What dependent work is given

Work that depends on other work — through a frozen predecessor edge, or
as the review of a record — consumes its predecessors' ACCEPTED results,
and it gets them through the authority path (D-TF-25):

| Predecessor | How the dependent sees it |
|---|---|
| SOURCE_MODIFYING | the accepted commit is in the frozen revision the dependent's worktree is created at (section 6.4) |
| any, READ_ONLY in particular | the artifact authority admitted for the accepted attempt is read from the content store by the ledger's address and placed in the dependent's isolated worktree as `.autobot/inputs/<predecessor workstream>.md` |

Only predecessors authority has released are delivered
(`released_edges`). The frozen prompt of dependent work says where the
files are, so the launched prompt is still exactly the frozen one. The
assignment records which results were provided: workstream, sequence,
attempt, content address. Git is told to ignore the directory (the
repository's local `info/exclude`), so a SOURCE_MODIFYING worktree still
reads as clean and the files cannot be staged.

Two more things are placed there, because a session cannot work out
either by itself (D-TF-33):

| File | What it is |
|---|---|
| `README.md` | what the working directory holds: the revision it is a checkout of; each delivered file and the workstream, sequence and attempt it came from; and, for a predecessor whose accepted work changed the repository, the commit authority published, its parent, and whether the checkout is at that commit. Every fact is read from the ledger. The note's bytes are stored and their address is recorded with the assignment (`inputs_note`) |
| `<reviewed>.given.<workstream>.md`, for a review only | the accepted results the reviewed work was itself given, by the addresses its own assignment recorded |

The frozen prompt cannot say any of this: the revision a predecessor's
change lands as does not exist when the dependent contract is frozen.

### 6.8 Execution routing

Which provider and model a session runs on is decided last, after every
engineering and organizational decision, and is part of none of them
(D-TF-26):

```text
work requirement    capability, role, objective, constraints, expected output
        ↓
staffing            the specialist: a retained member the Taskforce resolves,
                    or an outside specialist the Project Manager selects
        ↓
routing             provider, model, reasoning effort, session arguments
```

The policy is configuration (`routing.py`, `[routing]`):

| Class | Used for |
|---|---|
| `default` | every specialist session |
| `escalation` | work the organization escalates, and assignments the Project Manager asks to escalate |
| `fallback` | specialist sessions while the default route is switched off (`enabled = false`), in the declared order; never otherwise |
| `utility` | lightweight utility work; no engineering seat runs on it |

A route is a name of the organization's own and, inside it, the
provider's identifiers passed through exactly: `agent`, `model`, `effort`
and the native session arguments (`provider_args`). A specialist in the
catalog is a handle and what it offers. It runs on the default route
unless it names another (`route = "<name>"`) or carries an `agent` and
`model` of its own.

The Engineer's model is separate configuration (`[engineer] model`, or
`[models] planner`). No specialist route is the Engineer's.

**What does not change with the route.** The TaskContractRecord (it names
no provider, model or specialist, and its frozen prompt is the same text
for every provider). The specialist (its identity is its handle). The
ProjectAssignment (its identity is the sequence and the attempt ordinal).
The TaskforceMembership, the TaskforceSeat and every
CapabilityDemonstration (identities over the specialist, the capability
and the role, and references to authority records).

**What does.** Each attempt has its own AttemptContract, its own
ExecutionManifest and its own call. The manifest's `provider_assignment`
names the provider and the model that attempt runs on. Its schema is
shared byte for byte with AutoSwarm, so the rest of the route — the
reasoning effort and the native session arguments — travels beside the
manifest: the Taskforce stages it for the call and the transport passes
it to AutoSwarm, whose own call record (the admitted obj-11 witness)
holds the model and effort it was launched with. The assignment row
records the whole route — its name, provider, model, effort and session
arguments — just before the attempt is dispatched (`execution.route`),
and a re-entry or a relaunch after a restart repeats exactly that route,
arguments included, whatever the policy says by then. The staged
configuration names the provider it is for, and the transport refuses to
apply it to a manifest that names another. A specialist whose route is
replaced executes its next attempt on the new one. Its earlier attempts
keep the routes they ran on, so one specialist's history can read:
attempt 1 on one model, attempt 2 on another.

A route's session arguments may carry four placeholders the transport
fills for each session (`{workspace}`, `{repo_git}`, `{gate_pending}`,
`{gate_pending_real}`). Any other name in braces is refused when the
policy is read, however it is cased or punctuated: it would otherwise
reach the provider as literal text.

**Escalation** (D-TF-27). Work runs on the escalation route in two ways,
and neither names a model:

| Who determines it | How |
|---|---|
| the organization | `escalate` lists capabilities, or `capability:role` pairs, whose work always runs escalated |
| the Project Manager, for one assignment | the staffing decision carries `"escalation": "high_context"` or `"deep_reasoning"`, with the reason in its rationale |

The Project Manager's words describe what the engineering problem needs.
The Taskforce's routing resolves them. The request is recorded with the
staffing decision and with the assignment; an organization that declares
no escalation route refuses it, with the reason, instead of running the
work at standard depth under it. The Engineer is shown that an assignment
was escalated and never how any assignment is routed (D-TF-22).
Escalation has no budget of its own: the Engineer is told it is scarce,
and every ask is a recorded decision with its reason.

**Preflight.** `autobot-authority preflight` resolves the policy without
spending a model call: the Engineer's model, every route, what each class
resolves to, how each specialist is routed, and whether AutoSwarm can
launch each provider. With `--probe` it launches one small real session
per role — a specialist and an independent reviewer on the default route,
as two sessions at once, and one on the escalation route — on the
READ_ONLY contract production work runs on, answers their requests with
the same supervision policy, and asks the Engineer's model one question.
A preflight creates no objective, no contract and no attempt.

## 7. Authority integration

```text
   Engineer / PM             the pump                       Authority
   -------------             --------                       ---------
   work proposal -----------------------------------------> Registry: admit, freeze
   ASSIGN_SPECIALIST ------> apply it, or ask the Taskforce
                             for a retained member
                             start, reserve, dispatch ----> Sequencer: attempt, fence, call_id
                             build the manifest ----------> AutoSwarm: verify, claim, launch
                             supervise requests             Helios: admit each command
                             collect originals -----------> Sequencer: witnesses, artifact
                                                            git adapter: candidate
                                                            Collector: ExecutionReceipt
                             trigger ---------------------> Verifier runner: receipts
                                                            Sequencer: conclude
                                                            your permit (SOURCE_MODIFYING)
                                                            Git: ref compare-and-swap
                                                            Sequencer: accept, release, complete
                             record the demonstration <---- (read-only)
   next cycle <------------- workforce events, findings
```

A retained or established member passes through every box on the right,
exactly as an outside specialist does. Tests pin it: a retained member's
failed verification is REJECTED and nothing is accepted; an established
member's verified SOURCE_MODIFYING work waits at your permit; every
assignment stands on a record the Registry admitted and an AttemptContract
the Sequencer minted; a workforce decision appends nothing to the
authority journal.

## 8. The engineering artifact graph

```text
   Objective
      |
   Project ------------------------------------------ Team
      |                                                 |
   Workstream                                        Specialist
      |                                                 |
   Engineering Decision          (typed proposal)       +--> Capability Demonstration
      |                                                 |          |
   Task / Requirement                                   |    bound by RETAIN_SPECIALIST
      |                                                 |          |
   TaskContractRecord            (record_hash)          |    TaskforceMembership
      |                                                 |          |
   ProjectAssignment  <----- TaskforceSeat (when one resolved it) <-+
      |
   Attempt / AttemptContract     (attempt_id, call_id)
      |
   Artifact / Candidate
      |
   Verification                  (receipt content addresses)
      |
   Accepted Result
      |
   Finding / Demonstration
      |
   next Engineering Decision
```

Protocol objects keep their protocol identities. The intelligence plane
adds only what was missing. `query.artifact_graph` returns the graph for
one objective as nodes and typed edges.

## 9. The query model

See [QUERY_MODEL.md](QUERY_MODEL.md).

## 10. Storage

`intelligence.db` schema v2 adds, without touching a v1 row:

| Table | Holds |
|---|---|
| `projects` | each project: objective, title, the solution it realizes, status, who opened it |
| `project_workstreams` | each workstream: the project, the Project Manager's key, the work proposal |
| `project_team` | who takes part in which project, as what, and whether participation is open |
| `assignment_decisions` | the Project Manager's staffing decisions |
| `project_assignments` | who executes which attempt, on which path, decided by whom, and why |
| `specialists` | identity, routing, what each offers, origin, first seen |
| `taskforce_memberships` | persistent membership, one row per member |
| `taskforce_seats` | one retained capability and role of one member |
| `seat_history` | every seat transition and its cause |
| `capability_demonstrations` | verified performance, by reference |
| `retention_decisions` | retain, release, deactivate, establish, release of an outside specialist |
| `workforce_events` | changes that wake the Engineer |
| `assignment_approvals` | supervision decisions |
| `objective_budgets` | your limits per objective |
| `cycle_reasoning` | the policy's proposals, durable before disposal |

It also adds `objectives.constraints`, `findings.statement` and
`seats.assignment_id`. The v1 table `seats` keeps its name and is the
per-attempt execution slot.

Schema v3 adds one column, `assignment_decisions.escalation`: the
escalation class a staffing decision asked for, or nothing.

No table copies an authority task, attempt, candidate, evidence or
verification record: each references them by identity. Migration is
monotonic and runs in one transaction. A store newer than the code is
refused. The authority database is not touched.

## 11. Decisions

| ID | Decision |
|---|---|
| D-TF-1 | The Taskforce is the persistent engineering organization. The Agent Factory is not an architectural actor; its mechanics are the staffing function. |
| D-TF-2 | Specialist identity is a function of the worker's handle. Provider and model are replaceable attributes. |
| D-TF-3 | TaskforceMembership (one per member) and TaskforceSeat (one per retained capability and role) are separate records. OUTSIDE is the absence of a retained seat. There is no provisional membership. |
| D-TF-4 | Verified work is recorded as a demonstration and changes no membership. Only an explicit decision binding qualifying demonstrations creates membership and a seat. |
| D-TF-5 | The production loop waits for work in flight. Quiet with a specialist executing is not stable. |
| D-TF-6 | A specialist's permission requests are answered by a deterministic policy bounded by the frozen contract. |
| D-TF-7 | The assignment is persisted before launch; allocation is staged and resumable. |
| D-TF-8 | The specialist prompt is a frozen specification input. A repair executes the same prompt. |
| D-TF-9 | The production Engineer reasons with a model behind the seam, with no tools and no handles. |
| D-TF-10 | Reasoning is recorded before disposal and replayed after an interruption. |
| D-TF-11 | Every objective has an engineering-work budget whose usage is derived from durable rows. |
| D-TF-12 | Evidence requirements may be set per finalization class: READ_ONLY work that runs no command is witnessed by its execution lifecycle. |
| D-TF-13 | The frozen `agent_factory` principal is preserved as the wire identity of cap-14. |
| D-TF-14 | Every specialist works from a frozen source revision in an isolated worktree, removed when its assignment settles. |
| D-TF-15 | A review is staffed independently of the specialists whose work it examines, on both staffing paths. |
| D-TF-16 | The Engineer has two roles. The Project Manager role is entered by opening a project, the temporary unit of coordinated work for one objective. |
| D-TF-17 | Project participation and Taskforce membership are separate records. An assignment creates no membership; completing a project ends participation and never membership. |
| D-TF-18 | Two staffing paths: a capability request the Taskforce resolves to a retained member, and an outside specialist the Project Manager selects. The Taskforce never brings an outside specialist. |
| D-TF-19 | A work requirement and its TaskContractRecord name no provider, model or specialist. A project assignment may name a specialist; provider and model are routing, resolved downstream of staffing. |
| D-TF-20 | A policy that does not reason about staffing gets its Project Manager decisions from a declared standing rule; the reasoning Engineer selects for itself. |
| D-TF-21 | An outside specialist can be released from a project with no membership change. |
| D-TF-22 | The Engineer is not shown routing: handles, providers and models are removed from what it reasons over. |
| D-TF-23 | A member is outside for work it holds no seat for. |
| D-TF-24 | An attempt that failed at the provider returns its sequence to READY by the registered retry arc (t-s08), which the pump asks for; the next attempt needs the Engineer's word. The provider's own report is recorded with the attempt and shown to the Engineer, uninterpreted. |
| D-TF-25 | Dependent work is given its predecessors' accepted results through the authority path: the landed revision for a SOURCE_MODIFYING predecessor, and for any predecessor the admitted artifact, placed in the dependent's isolated worktree. |
| D-TF-26 | Execution routing is a declared policy resolved after staffing: a default route for every specialist session, an escalation route, fallbacks used only while the default is switched off, and a utility route. A route's provider, model and effort are no part of a TaskContractRecord, a requirement, a project, a workstream, a capability, a specialist, a membership, a seat or a demonstration. The Engineer's model is separate configuration. |
| D-TF-27 | Work is escalated by the organization's rule over capability and role, or by the Project Manager for one assignment in words about the engineering need (`high_context`, `deep_reasoning`) with its rationale. Routing resolves either to the escalation route. Neither names a provider or a model. |
| D-TF-28 | A provider that runs inside an operating-system sandbox is supervised at the sandbox's edge: its route declares what the sandbox may write, and the only escalation the policy grants is the pair of Git commands a specialist-owned candidate commit needs (`git add` of paths in the frozen scope, `git commit -m`), as one plain command run by the system shell from the root of the assignment's own, untampered worktree. The command that would run is what is judged. Every command still passes Helios. (Narrowed after F-TF-26; as first written it granted seven Git subcommands by their first two words.) |
| D-TF-29 | The route an attempt is dispatched on is recorded with that attempt: provider and model in the ExecutionManifest, whose schema stays what AutoSwarm also holds; the whole route, with its name, effort and session arguments, on the assignment, before the ledger dispatches the attempt. Effort and session arguments are staged beside the manifest, for the provider they belong to. A re-entry or a relaunch repeats the recorded route. The specialist prompt is one text for every provider. (Completed after F-TF-28: the route was first recorded after dispatch and without its arguments.) |
| D-TF-30 | Supervision judges a location by where it resolves. A path is inside a root when its resolved location lies inside the root's resolved location; the spelling of either decides nothing. |
| D-TF-31 | A specialist works in the default shell of its session. The execution contracts say so, and say that a shell which cannot start is neither a refusal nor a reason to stop. The contract names no shell: which one a session has is the provider's and the host's. |
| D-TF-32 | A review states whether it examined the work, in one line the review contract defines. The statement is recorded with the review's assignment. A review that says it could not examine the work is still a valid result for authority; for the organization it leaves the work unreviewed, earns its reviewer no qualifying demonstration, and refuses project completion until another review of the same record examines it. A verdict is read from the line and never inferred. |
| D-TF-33 | A dependent session is told what its working directory holds, by a note written at launch from ledger facts and recorded by content address; a review is also given what the reviewed work was given. |
| D-TF-34 | One assignment is one specialist doing its own task in one session. The execution contracts say so for every provider: no sub-agent, other agent or other session; the project objective is context and its other parts are other assignments; a check of one's own work is not an independent review. Independence is the organization's to arrange and nothing a session does to itself supplies it. The contract names no provider and no provider's tool. |
| D-TF-35 | An assignment's session reaches nothing outside the machine's files and its own shell. The execution contracts say so for every provider, and name no provider and no service. The default route's session arguments switch off, for the session only, every provider feature that would give it more: apps and their connectors, plugins, MCP servers (by name), web search, browser and desktop control, image generation. They are the provider's own configuration, passed through exactly like every other route argument; what they switch off is established by probing a session's tool registry, not by a test, and is re-established the same way when the provider changes. |

## 12. Findings

| ID | Finding | Disposition |
|---|---|---|
| F-TF-1 | `main` carries an amended copy of PR 19's merge commit (`e3ad4d0` in place of GitHub's `bed5558`). The amendment moved two variable assignments in `cli.py` above their first use and was force-pushed. | Recorded. History is not rewritten again. |
| F-TF-2 | The SOURCE_MODIFYING pilot advanced `refs/heads/main` by ref compare-and-swap with `main` checked out, leaving the index and working tree one commit behind. | The checkout was restored from HEAD. Specialists no longer read the shared checkout (D-TF-14), so later work is unaffected. The runtime still does not synchronize a checked-out branch after a local publication; the operator does. |
| F-TF-3 | The driver reported a stable state while specialists were still executing; the pilots were continued by running the command again. | Fixed: `ContinuousDriver.run` (D-TF-5). |
| F-TF-4 | Specialists' permission requests were approved by a script outside the runtime that approved everything. | Fixed: assignment supervision (D-TF-6). |
| F-TF-5 | The production transport returned no findings; findings existed only in scripted tests. | Fixed: the findings contract and `findings.extract_findings`. |
| F-TF-6 | A repair proposal's guidance replaced the launch prompt, which the role-transform check would reject against a frozen prompt. | Fixed: the frozen prompt is launched on every attempt (D-TF-8). |
| F-TF-7 | An allocation interrupted between attempt creation and launch was never resumed. | Fixed: staged, resumable allocation (D-TF-7). |
| F-TF-8 | F-CE-2: no objective-scoped work budget. | Fixed (D-TF-11). |
| F-TF-9 | A SOURCE_MODIFYING pilot's isolated worktree is left in the checkout after the work lands. | Fixed: the worktree is removed when the assignment settles (D-TF-14). The worktree the 2026-11-17 pilot left behind predates this and was not touched. |
| F-TF-10 | `test_joined_lifecycle` read "the first" of two obj-11 witnesses through a query with no ordering; the rows return in content-hash order, so the test passed or failed by chance (4 of 8 identical runs). | Fixed in the test: the witness is selected by the fact it carries. The product was not affected. |
| F-TF-11 | The earlier production pilots were reset by a script outside the runtime that deleted authority rows; the production journal holds 46 entries whose sequence numbers run to 367. | Recorded. Nothing in this architecture deletes an authority row; production state is carried forward as it is. |
| F-TF-12 | The first form of this workforce model made an outside assignment a provisional Taskforce seat and let the Taskforce bring outside specialists onto work itself, conflating project participation with membership. | Corrected before it was committed: D-TF-3, D-TF-4, D-TF-16 to D-TF-23. |
| F-TF-13 | A second release of the same specialist for the same reason had the same proposal identity as the first and was treated as already applied. | Fixed: a release names the work it considered, and a workforce decision on record is applied again through the roster, which converges on its own identity. |
| F-TF-14 | The production policy's completion guard counted a work proposal that was refused at admission as an unfinished workstream, so one refusal (a spent contract budget, a Registry rejection) made its project impossible to complete. | Fixed before it was committed: the guard and the finished-project trigger count admitted work only (section 3.7). |
| F-TF-15 | Found in production, 2026-11-18. The provider's usage window ran out while Pilot 1's first two specialists were starting; both sessions failed. Authority recorded the attempts FAILED and accepted nothing, correctly — but the pump never asked for the registered retry arc (t-s08), so both sequences stayed EXECUTING and no further attempt could be ordered. | Fixed: the pump asks for t-s08 every sweep (section 6.6, D-TF-24). |
| F-TF-16 | Found in the same run. The Engineer was not shown why an attempt failed: the provider's error text existed only inside the admitted witness, so a usage-window failure looked like a specialist's failure. | Fixed: the provider's report is recorded with the demonstration and listed with the workstream's failed attempts (section 6.6). The two demonstrations recorded before the fix keep the form they were recorded in; project state reads their reports from the witnesses. |
| F-TF-17 | Found in the same run. The limits of an objective's budget are the operator's, but once an objective had started the command line had no way to change them. | Fixed: `engineer --objective-id … --max-…` on a resume. |
| F-TF-18 | Found in production, 2026-11-18 (Pilot 1). The independent review of a READ_ONLY investigation was dispatched without the investigation's accepted result: nothing delivered a READ_ONLY artifact to dependent work, so a dependency ordered execution and carried no content. The reviewer said so in its findings and verified the question from source instead. | Fixed: section 6.7, D-TF-25. |
| F-TF-19 | The catalog gave each specialist its own provider and model, so the organization had no default route to change; and a route's reasoning effort and session arguments were recorded and never passed to AutoSwarm. | Fixed: the routing policy (section 6.8, D-TF-26). The transport passes the effort and the session arguments. |
| F-TF-20 | The READ_ONLY specialist contract named one provider's tools (Read, Glob, Grep) and forbade the shell. A provider that reads through a shell could not do READ_ONLY work under it; and the frozen prompt is part of the contract, so it cannot differ by provider. | Fixed: one provider-neutral text (D-TF-29). |
| F-TF-21 | An assignment row kept the provider and model its specialist had when it was selected. A route replaced between selection and dispatch was used for the launch and then lost: a relaunch after a restart would have asked for the old model under the same call, which AutoSwarm refuses as a different manifest. | Fixed: the dispatched route is recorded at dispatch and repeated (D-TF-29). |
| F-TF-22 | Found by probing the Codex route, 2026-11-18. Inside a `workspace-write` sandbox a linked worktree cannot stage or commit: its index and objects are in the repository's Git directory, which stays protected even when it is listed as writable. With approvals off, the session stops there. | The route relays approvals, and the supervision policy grants the two Git commands a candidate commit needs and nothing else (section 6.3, D-TF-28). |
| F-TF-23 | Reported by Pilot 2's specialists and confirmed in source: `Sequencer.mark_member_stale` (t-a12) has no caller, so a provider session that hangs is never failed and holds every run until `--max-wall-seconds`. | Open. Recorded for operators in [RUNBOOK.md](RUNBOOK.md) section 5.2. |
| F-TF-24 | Reported by Pilot 2's specialists, read in source, not confirmed by a test: an attempt the Sequencer refuses to start leaves an ABANDONED assignment that `allocate_ready` then skips for good; a non-zero exit of `autoswarm run` escapes as an uncaught error and leaves the attempt DISPATCHED; a negative budget on a fresh run leaves the objective ACTIVE behind a traceback; `--authorize-publication` and `--max-wall-seconds` do more than their help text says. | Open. Recorded for operators in [RUNBOOK.md](RUNBOOK.md). |
| F-TF-25 | RUNBOOK.md cites source by line, and the routing change moved those lines. | Fixed through the organization, 2026-11-18 (Pilot 4): commit `606663ade00219a4b2c1baef0df0aa81dc510067`, written by the retained documentation builder from two verified investigations and published by authority. All 104 references of the earlier text carry through the line shifts of the routing change (75 moved, 29 did not); the commit also adds the `preflight` command and an execution-routing subsection. [PRODUCTION_PROOF.md](PRODUCTION_PROOF.md) section 19. |
| F-TF-26 | Reported by Pilot 3's reviewers, 2026-11-18 (three findings, significance high), and confirmed in source. The sandbox-edge rule of D-TF-28 as first written read the provider's description of a command by its first two words. A script that chained a second command after `git status` would have been approved to run outside the sandbox; any program named `git` was accepted; path-capable Git commands (`rm`, `mv`, `reset`, `restore`) were granted without looking at their paths; and the working directory only had to be spelled inside the workspace. | Fixed: the rule of section 6.3 (D-TF-28 as narrowed, D-TF-30). No production request used the gap. The only two requests production had raised (Pilot 4: `git add docs/architecture/orchestration/RUNBOOK.md` and `git commit -m "docs: update orchestration runbook"`) are replayed in the tests as recorded and are still granted. Not closed by a policy that decides before a command runs: the checks are made at decision time and the command runs afterwards; Git reads the repository's own configuration and hooks, which are trusted as the operator's; and inside the sandbox a session can still write any file of its worktree, in scope or not, which the candidate scope check refuses afterwards. |
| F-TF-27 | Reported by Pilot 1's specialists and again by Pilot 3's: `_relative_inside` accepted a path when either its written or its resolved spelling lay inside the root, while its documentation said both were judged. A link inside a worktree that leads out of it counted as inside, for reads, for writes and for the sandbox-edge working directory. | Fixed: resolved containment (D-TF-30). |
| F-TF-28 | Reported by Pilot 3's specialists; each part then confirmed by reading the source and by a test that fails without the fix. (a) The route of an attempt was recorded when its execution slot was bound, after dispatch: a stop between the two, followed by a routing change, relaunched on the new route while the ledger had dispatched the attempt to the old provider. (b) A relaunch took a route's session arguments from the live policy, not from the assignment. (c) Staged session arguments were keyed by call id alone and applied to whatever manifest carried that id. (d) A placeholder that was not lower-case letters in braces passed validation and stayed literal in the arguments. | Fixed, all four. (a) The route is recorded with the assignment before the ledger dispatches, and once recorded it is what every re-entry reads (section 6.1, stage 4). (b) The recorded route carries its session arguments, and a relaunch repeats them; an edit of a route's arguments applies to attempts dispatched after it. (c) Staged configuration names its provider, and the transport refuses to apply it to a manifest naming another. (d) Any name in braces that is not one of the four placeholders is refused when the policy is read. No production run met any of the four conditions. Since the fix production shows (a) and (b) directly: in Pilot 6 each assignment carried its route, session arguments included, while its attempt was still executing and had no result. |
| F-TF-29 | Found in production, 2026-11-18 (Pilot 4). The reviewer of a SOURCE_MODIFYING change is given the builder's accepted result and a worktree at the landed revision, but is not told that its worktree holds the reviewed change, and is not given the results the builder worked from. Pilot 4's reviewer verified the new sections, spot-checked the references and reported that it could not compare exhaustively. | Fixed: D-TF-33, section 6.7. The review of Pilot 4 stays what it was, a partial one; the exhaustive comparison was made outside the organization and is recorded as that in the production proof. Confirmed in production by Pilot 6: the reviewer's worktree held the note and the accepted result, each by the bytes its assignment recorded, and its first command read the note ([PRODUCTION_PROOF.md](PRODUCTION_PROOF.md) section 21). |
| F-TF-30 | Found in production, 2026-11-18 (Pilot 5). The independent reviewer read the change under review in its session's default shell, then set its command tool to `bash`. On that host the only `bash` is the WSL launcher, and WSL refuses a sandboxed process: every later read returned `Bash/Service/CreateInstance/E_ACCESSDENIED`. Helios had admitted each command, no permission request was raised, and no file was unreadable: the default shell would have read everything, which two probe sessions confirmed. The reviewer took the failure for a denial, looked for a file-reading tool, found none and reported that it could not determine whether the reviewed text was accurate. Three things then went wrong above it. Authority, correctly, accepted the result. The reviewer was credited with a qualifying demonstration. And the Engineer completed the project, reasoning that the review had reported no defect. | Fixed. The contract keeps a session in its default shell and says a shell that cannot start is not a denial (D-TF-31). A review states whether it examined the work; one that says it could not leaves the work unreviewed, earns no qualifying demonstration and blocks completion (D-TF-32, section 3.7). The reviewed text itself was checked against the source outside the organization and is accurate. The review of Pilot 5 stays on record as what it was: an accepted result that examined nothing. Confirmed in production, 2026-11-19 (Pilot 6, `obj-pilot6-review-confirmation-001`): the reviewer (specialist `sp_b2f72881be89546de36037d84fea287d`, assignment `asg_dc9b5f700a37a8722dbe85010295754c`, call `call_08e03c806919cb2e66c1e955661d3f10`), a different specialist, assignment, call and session than the builder, ran all 33 of its commands in its default shell, named the files and line ranges it read, subsection 5.5 and the source it cites among them, and stated `AUTOBOT_REVIEW: EXAMINED`. The verdict was recorded with its assignment, the result was verified and accepted, and the project was completed on it ([PRODUCTION_PROOF.md](PRODUCTION_PROOF.md) section 21). |
| F-TF-31 | Found in production, 2026-11-19 (Pilot 6), from the session records. A specialist session can start sub-agent threads of its own provider inside its session. The builder did, alone among the eleven sessions the default route had run by then and alone among the fifteen it has run to date: it named the thread `independent_review`, had it check its citations, and wrote in its result that "an independent review checked the citations". The sub-agent ran in the builder's worktree and sandbox. Each of its commands passed Helios, and their decisions are stored under the builder's session, in the folder authority collects for the attempt; it asked for nothing outside the sandbox. | No guarantee of section 6 was affected: the assignment was still one specialist, one call, one sandbox and one body of command evidence. It is not an independent review in the sense of section 3.7, which is another specialist staffed by the organization, and it is never counted as one; the organization's review of that work was done separately. The likely trigger was in the builder's own prompt: the project objective it was shown as context also asked for the result to be independently reviewed. Fixed as far as Autobot owns it: D-TF-34, section 6.2. Not closed: on the default route's provider the tool cannot be switched off by session argument (two probes), so a session there can still break the rule; Autobot does not detect that by itself, and the sub-agent's reasoning record is a second provider session file that is not a collected witness. Confirmed in production, 2026-11-19 (Pilot 7, `obj-pilot7-one-session-001`): shown a project objective that again asked for an independent review, neither the builder's session nor the reviewer's called any of the provider's six sub-agent tools, and each session's stored command decisions are as many as its own commands (54 and 13). The same held in Pilot 8 (36 and 16), whose builder wrote of its own checking that it "did not perform an independent review, which belongs to a separate assignment". |
| F-TF-32 | Found in production, 2026-11-19 (Pilot 7), from the session records, then measured over all thirteen sessions the default route had run. A session of that provider inherits the operator's own provider configuration: 158 tools in its registry, 141 of them provider-side apps (89 of a GitHub connector, tools that write among them), 3 of a node REPL server, with web search and image generation beside them. None passes the sandbox or the command gate, none is in an attempt's command evidence, and no assignment grants one. Two sessions used the connector, both of them reviewers, in 16 tool-use scripts, every one a read of the remote repository: Pilot 3's `argument-review` read its source from the remote's `main`, and Pilot 7's reviewer, after three gate files it had addressed to the wrong folder were refused, read the source there at the parent of the commit under review. Two reviewers asked the provider's desktop-control tool for its state and got nothing usable. No tool that writes was called. | No guarantee of the authority plane was touched: each result was verified and accepted as what it was, and what the two reviewers read was the content of the revision they were to read (in Pilot 7 the reviewer said where it had read it and why that was the same content). What failed is the organization's own rule that an assignment is a working directory and a shell, which nothing stated or enforced on this provider. Fixed: D-TF-35, section 6.2. The route switches the tools off (a session's registry: 158 tools before, 5 after) and every contract forbids them. Not closed: an MCP server is switched off by name, so one configured later is covered by the contract alone until its line is added to the route; and Autobot does not itself read a provider's session record. Confirmed in production, 2026-11-19 (Pilot 8, `obj-pilot8-session-reach-001`): with the route's arguments on each provider command line, the builder's session and the reviewer's called the shell (36 and 16 commands), the patch tool and the clock and nothing else, and the reviewer read the work and the source it must agree with in its own worktree ([PRODUCTION_PROOF.md](PRODUCTION_PROOF.md) section 21). |
