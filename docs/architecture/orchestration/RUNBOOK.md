# Operator Runbook: `autobot engineer`

## 1. Purpose and scope

This runbook is for the operator who runs the `engineer` command. It also covers the three reporting commands that go with it: `objectives`, `status` and `taskforce`.

Everything here was checked against the source:

- `src/intelligence/cli.py`
- `src/intelligence/driver.py`
- `src/intelligence/taskforce.py`
- the retry rule in `src/autobot/sequencer.py`
- `signal_resume` in `src/intelligence/runtime.py`

Line references are given as `file:line`. Some things could not be confirmed from source. They are listed in [Not yet verified](#8-not-yet-verified) and should not be relied on.

The commands `run`, `seed`, `bootstrap` and `query` are out of scope.

### Global option placement

`--root` is defined on the top-level parser only (`cli.py:983-984`), so it must come **before** the subcommand:

```
autobot --root <dir> engineer ...    # correct
autobot engineer --root <dir> ...    # argparse error, exit 2
```

### Configuration prerequisite

The reporting commands (`status`, `objectives`, `taskforce`) find the data directory by reading `config.toml` (`cli.py:120-151`). This needs `tomllib` (Python 3.11 or later) or the `tomli` package. If neither is available, the config is **silently treated as empty**. The commands then read `autobot.db` and `intelligence.db` from the project root, not from the configured `data_dir`. They may then show an empty or wrong state, or report "no authority database", without any warning.

---

## 2. Commands

| Command | Takes driver lease | Writes state | Purpose |
|---|---|---|---|
| `engineer` | yes | yes | Bootstraps the runtime, either adds a new objective or resumes, and drives the loop to a stable quiet state. Then reports. |
| `objectives` | no | schema migration; also with `--close` | Lists all objectives, and can close one. |
| `status` | no | schema migration only | Snapshot of active work. Safe to run while a driver is running. |
| `taskforce` | no | schema migration only | Report on the engineering organisation. |

**The reporting commands are not strictly read-only.** Every open of the plan store sets WAL mode and runs the schema migration inside a `BEGIN IMMEDIATE` transaction (`planstore.py:504-512`, `planstore.py:523-526`, `planstore.py:534-535`). Each reporting command therefore takes a brief write lock. If `intelligence.db` is missing, opening it creates an empty one (`planstore.py:506`), and the command reports no active objectives instead of failing.

### 2.1 `engineer` (`cli.py:641-786`)

The command runs these steps in order:

1. It bootstraps the runtime with `--evidence-profile` (`cli.py:655-656`).
2. If `--objective` is non-empty, it adds a new objective. Otherwise it resumes (see [§3](#3-default-behaviour-when---objective-is-omitted-resume)).
3. It calls `rt.run(policy)` until the loop reaches a stable quiet state or times out.
4. If `--authorize-publication` is set, it grants publication permits and runs again (see the flag table below).
5. It prints:
   - objectives that are still active;
   - proposals waiting on your authority;
   - attempts still in flight;
   - project lines, including the provider's error text for each workstream (`cli.py:232-238`);
   - the Taskforce summary.
6. If `--report` is set, it writes a JSON report.

**Scope of a run.** The loop always runs over **all** active objectives, including ones from earlier runs. This holds even when you pass a new `--objective` (`cli.py:702`).

The project lines and the `--report` payload use the list of active objectives taken **before** the run. An objective that completed during the run still appears in them (`cli.py:753-755`, `cli.py:766-768`).

#### `engineer` flags

| Flag | Type | Default | Behaviour |
|---|---|---|---|
| `--objective TEXT` | str | none | Text of a new objective. If it is absent or an empty string, the command **resumes** and does not report an error (`cli.py:662`). |
| `--objective-id ID` | str | none | **Fresh run:** the id of the new objective. If omitted, the id is `obj-eng-<YYYYmmddHHMMSS>`, which has one-second resolution (`cli.py:663-664`). **Resume:** selects which active objective the budget flags apply to (`cli.py:683-688`). It does **not** narrow the run. If you give it without any budget flag on a resume, it is silently ignored. |
| `--constraint TEXT` | str, repeatable | none | Passed to `add_objective` on a fresh run only (`cli.py:671`). It is **silently ignored on a resume**. |
| `--evidence-profile` | `READ_ONLY_REPORT` \| `READ_ONLY_GOVERNED` \| `SOURCE_MODIFYING_GOVERNED` \| `BY_CLASS` | `BY_CLASS` | Passed to `bootstrap` on **every** invocation, resumes included (`cli.py:655-656`). A resume can therefore run with a different profile from the original run. The `run` command defaults to `READ_ONLY_REPORT` instead. |
| `--max-contracts N` | int | not set | Budget key. See [Budget flags](#budget-flags). |
| `--max-attempts N` | int | not set | Budget key. Every attempt, including a retry, spends this budget (`taskforce.py:423-429`). |
| `--max-outside-specialists N` | int | not set | Budget key. |
| `--max-repairs N` | int | not set | Budget key. |
| `--max-retention-decisions N` | int | not set | Budget key. |
| `--report PATH` | path | none | Writes the closing state as JSON (`cli.py:762-783`). See [Report file](#report-file). |
| `--max-wall-seconds S` | float | `3600.0` | Wall-clock bound for **each** `rt.run` call, and it is checked only while waiting (`cli.py:966-968`). See [§5.3](#53-wall-clock-timeout---max-wall-seconds). |
| `--poll-seconds S` | float | `2.0` | Interval between polls while waiting on in-flight work (`cli.py:969-971`). |
| `--authorize-publication` | flag | off | After each **stable** round, grants one publication permit to every sequence that meets all of these conditions (`cli.py:725-739`): it is VERIFIED, has no permit, and belongs to a contract proposal of an objective that was active when the command started (the list captured at `cli.py:702`, before the run). The run then continues. See the warning below. |

#### Budget flags

- If you leave a budget flag out, its key is left out of the budget. The CLI never fills in a default, so the effective value comes from the plan store's defaults (`planstore.py:35-41`):

  | Key | Default |
  |---|---|
  | `max_contracts` | 12 |
  | `max_attempts` | 24 |
  | `max_outside_specialists` | 6 |
  | `max_repairs` | 6 |
  | `max_retention_decisions` | 12 |

- Zero is accepted. Negative values are **rejected** by `PlanStore.set_budget` with `ValueError` (`planstore.py:585-591`):
  - On a fresh run, the ACTIVE objective is created first (`add_objective` commits), and then the uncaught `ValueError` produces a traceback and exit 1. The objective is **left ACTIVE**.
  - On a resume, the same value gives `cannot change the budget` and exit 1 (`runtime.py:644-650`).
- On a fresh run, the budget keys go to `add_objective(budget=...)` (`cli.py:665-671`).
- On a resume, they go to `rt.set_objective_budget(objective_id, budget)` (`cli.py:677-696`). This **merges**: the keys you give replace the current limits, and the others keep their current values (`runtime.py:644-646`). See [§3](#3-default-behaviour-when---objective-is-omitted-resume).

#### Warnings about `--authorize-publication`

The help text says the flag grants "one use for each verified SOURCE_MODIFYING sequence of this run". The code does something broader:

- It does **not** check the finalization class. Any VERIFIED sequence without a permit qualifies.
- It includes sequences from **earlier runs**, as long as they belong to an active objective.
- It grants permits only after a **stable** round. A timed-out round grants none.
- The loop is capped at 17 rounds (`rounds > 16`). The grant is evaluated before the cap is checked, so the last round can grant permits and then stop without running again (`cli.py:737`, `cli.py:725-739`).
- Each continuation round gets a fresh `--max-wall-seconds` budget.

#### Report file

The report file:

- is written after a run completes normally, both on exit 0 and on exit 2;
- is **not** written on any exit-1 early return;
- silently **overwrites** an existing file;
- requires the parent directory to exist already.

If the path cannot be opened, the error is raised **after** the whole run, as a traceback.

The payload contains:

- the objectives that were active at the start;
- the projects;
- the Taskforce report;
- a run summary: `stable`, `timed_out`, `steps`, `waits`, `active_objectives` and `in_flight`.

#### Output streams

- Errors go to **stderr**.
- `TIMED OUT`, "waiting on your authority" and "attempts still in flight" go to **stdout**. These sections come from the **last** run result only.

### 2.2 `objectives` (`cli.py:789-840`)

The command lists every objective with its status, id, author and timestamps. Objective text is cut at 160 characters.

**`--close ID`** tries to close the objective before printing the listing. It checks these conditions in order:

1. The objective exists. If not, the command exits 1.
2. No driver lease is live. If one is, the command exits 1. The test compares timestamps as strings, and it is **not atomic** with the close.
3. No unfinished sequenced task exists. If one does, the command exits 1. Only tasks that already have a `sequence_id` are checked, so **planned work without a sequence does not block closing**.
4. The objective's status is checked. If the objective is already non-ACTIVE, the command prints `already <STATUS>` and exits 0.

The listing is printed only when the close succeeds or the objective is already non-ACTIVE (`cli.py:827-836`). The refusal paths in steps 1-3 exit 1 before the listing (`cli.py:803-805`, `cli.py:811-813`, `cli.py:819-821`).

`objectives --close` **writes** to the plan store.

### 2.3 `status` (`cli.py:392-432`)

`status` changes no work state, though opening the plan store takes a brief schema-migration write lock (see [§2](#2-commands)). It does not take the lease, so it is safe to run beside a running driver. It prints:

- active objectives;
- sequences that are not in a terminal state. COMPLETED, ABANDONED and REJECTED sequences are hidden, and only COMPLETED ones are counted;
- **ACTIVE** projects only, with their workstreams, failed attempts and teams. Project titles are cut at 100 characters and provider errors at 160;
- the Taskforce summary.

The help text does not mention projects, but `status` prints them.

### 2.4 `taskforce` (`cli.py:843-906`)

`taskforce` prints:

- the Taskforce summary;
- all members, of every status;
- outside specialists;
- why each retained seat is held;
- the assignment history;
- **all** projects and their teams. `status`, by contrast, shows only ACTIVE projects.

The headline "members" count includes ACTIVE members only, while the `members:` list shows every status. Output is not truncated, so it can be long.

With `--json`, the command prints only the raw `taskforce_report`, without project data.

### 2.5 `preflight`

Run `autobot preflight` before dispatching engineering work, especially after changing routing or provider configuration. Without `--probe`, it resolves the configured routing policy, Engineer model, specialist routes and AutoSwarm-reported provider availability; this report does not make a model call (`cli.py:318-389`, `preflight.py:1-26, 62`, `runtime.py:714-732`).

Use `--probe` when you need to check real session launches and responses. It runs only if the initial report has no problems, launches real sessions and asks the Engineer model a question. With a routing policy, it probes the default route with a specialist and independent reviewer, plus the escalation route when configured; utility is resolved but not probed. Without a routing policy, it probes distinct specialist provider/model/effort combinations. Probe sessions use isolated worktrees and a `READ_ONLY` task, check the returned answer, and check that the specialist and reviewer use distinct sessions (`cli.py:993-1004`, `preflight.py:11-20, 130, 240`, `runtime.py:733-758`).

Preflight creates no objective, contract or attempt, and its output is not accepted work state. `--timeout` defaults to 900 seconds for probe sessions; `--report PATH` writes the report as JSON. Exit **1** means bootstrap raised `ConfigError` or the report has problems; exit **0** means `report["ok"]` is true (`cli.py:323-326, 386-389`, `preflight.py:25-26`, `runtime.py:757-758`).

---

## 3. Default behaviour when `--objective` is omitted (resume)

`engineer` resumes when `--objective` is absent or empty (`cli.py:673-706`). It runs these steps in order:

1. **Budget change (optional).** This step happens only if at least one budget flag is given.
   - `--objective-id` is required. Without it, the command prints an error and **exits 1** (`cli.py:683-686`).
   - The command calls `set_objective_budget(objective_id, budget)`. A `ConfigError` from this call **exits 1** (`cli.py:687-692`).
   - On success it prints `budget set for <id>: k=v, ...` (`cli.py:693-696`).
2. **`signal_resume`** is always called. It emits `operator_resumed` for **every** active objective, which makes the Engineer reason again (`runtime.py:657-674`). If it returns anything, the command prints `resuming: ...` (`cli.py:699-701`).
3. **Run scope.** The loop runs over **all** active objectives, not just the one named by `--objective-id` (`cli.py:702`).
4. **No active objective.** The command prints to stderr and **exits 1** (`cli.py:703-706`). By this point the budget change and `signal_resume` have **already been applied**.

On a resume, `--constraint` is ignored. `--evidence-profile` still applies, with a default of `BY_CLASS`.

A fresh run (with `--objective`) sends the budget and constraints to `add_objective` and does **not** call `signal_resume` (`cli.py:662-672`).

---

## 4. Exit codes

### `engineer`

| Code | Meaning | Source |
|---|---|---|
| `0` | The last run was stable **and** no objective remains active. | `cli.py:784` |
| `1` | Bootstrap raised `ConfigError`. | `cli.py:657-659` |
| `1` | Budget flags were given on a resume without `--objective-id`. | `cli.py:683-686` |
| `1` | `set_objective_budget` raised `ConfigError`. | `cli.py:689-692` |
| `1` | Resume found no active objective. | `cli.py:703-706` |
| `1` | Building the engineer policy raised `ConfigError`. | `cli.py:709-711` |
| `1` (traceback) | Any other uncaught exception. Examples: an unwritable `--report` path, or the runaway guard's `RuntimeError` (more than 512 active steps, `driver.py:210-213`). A transport launch error (a non-zero exit from `autoswarm run`) escapes as an uncaught `RuntimeError` (traceback, exit 1) and leaves the attempt DISPATCHED; neither `launch_manifest`, `launch_with_manifest` nor `_drive` catches it. Provider-reported outcomes (session failures after a successful launch) are recorded as failed attempts (`taskforce.py:998-1000`). | `autoswarm_transport.py:106-108` |
| `2` | The run **timed out**, **or** it was stable but objectives are **still active**. | `cli.py:784` |
| `2` | An argparse usage error, such as `--root` placed after the subcommand. | argparse |
| platform-dependent | Interrupted (Ctrl-C). A `KeyboardInterrupt` at any point during the command is not caught: `cmd_engineer` has only `try`/`finally` and `main()` has no handler. The process exits immediately; on POSIX the exit code is typically 130. | `cli.py:660-786`, `cli.py:1098-1099` |

These are the cases established from source; the table is not claimed to be exhaustive.

> **Exit 2 is ambiguous.** It covers a timeout, parked work after a stable run, and a usage error. The code does not separate these cases. To tell them apart:
>
> - Look for `TIMED OUT` on stdout.
> - Look for the "waiting on your authority" and "attempts still in flight" sections.
> - Read `stable` and `timed_out` in `--report`.

### Other commands

| Command | Exit 1 when | Otherwise |
|---|---|---|
| `objectives` | The database is missing, the id is unknown, a lease is live, or unfinished sequenced tasks exist. | 0 |
| `status` | The authority database is missing. | 0, whatever the state of the work |
| `taskforce` | The authority database is missing. | 0 |
| *(no subcommand)* | The program prints help and exits 1 (`cli.py:1080-1082`). | – |

---

## 5. Runtime concepts

### 5.1 Stable quiet state

A step is **quiet** when both of these hold (`driver.py:46-54`, `driver.py:140`):

- the Engineer opened no planning cycle (`cycle_id` is None);
- the Taskforce sweep reported no activity: nothing was allocated, adopted, collected, verified, supervised or settled, and no authority-arc list has entries.

A run is **stable** after `quiet_steps` (default **2**) quiet steps **in a row** with nothing in flight (`driver.py:215-227`).

A wait step resets the quiet counter (`driver.py:216`). The run therefore always takes at least two more steps after the last attempt lands.

**Stable does not mean finished.** The loop also reaches a stable state while work is parked, for example:

- verified SOURCE_MODIFYING work that has no publication permit. `--authorize-publication` or an explicit permit releases it;
- work that needs human attestation (HUMAN_ATTESTED verification);
- `staffing_unavailable`, `budget_exhausted` or `retry_exhausted`;
- a FINISHED attempt with no candidate tree (`BLOCKED_NO_CANDIDATE_TREE`).

Only the exit code tells finished work apart from parked work: 0 means finished, 2 means parked.

**Runaway guard.** More than `max_active_steps` (**512**) steps that did something raise `RuntimeError` (`driver.py:210-213`). `engineer` does not catch it, so the command exits with a traceback.

### 5.2 In flight

An attempt is **in flight** if it is in one of these states (`taskforce.py:230-236`):

- `DISPATCHED`;
- `RUNNING`;
- `CANDIDATE_FROZEN`, but only while its sequence is also `CANDIDATE_FROZEN`. This means the evidence window is still incomplete (`taskforce.py:1116-1125`).

`DECLARED` and `RESERVED` attempts are **not** in flight.

While anything is in flight, quiet steps become **wait** steps. The driver polls every `--poll-seconds` and checks the wall clock.

The operator sees only bare attempt ids, in the line `attempts still in flight: …` (`cli.py:749-750`) and in the report's `in_flight` list (`driver.py:149`). The workstream and state are not shown.

> **A hung provider session never times out by itself.** The loop leaves a `RUNNING` attempt alone while it has no provider result (`taskforce.py:979-984`). The staleness rule `mark_member_stale` (`sequencer.py:3954`) exists, but nothing in `src` calls it. A hung session keeps every run waiting until `--max-wall-seconds`, and it does so on every later run too.

### 5.3 Wall-clock timeout (`--max-wall-seconds`)

- **It bounds waiting, not the whole run.** The clock is checked only in the wait branch, when a step was quiet but work is still in flight (`driver.py:215-220`). Steps that do something, including slow Engineer reasoning, never check it. The docstring of `driver.py:186` says it "bounds the whole run", which is not what the code does. The elapsed time is measured from the start of that `rt.run` call.
- **It applies to each `rt.run` call.** With `--authorize-publication`, each continuation round (up to 17) gets a fresh budget (`cli.py:716-739`). The total time can be many times the flag's value.
- **On timeout the run returns at once**, with `stable=False` and `timed_out=True`. Then:
  - nothing is aborted or drained, and no checkpoint is written;
  - **specialist sessions keep running at the provider**;
  - only the durable ledger and the plan-store state remain;
  - the CLI prints `TIMED OUT` on stdout and exits **2**. It does not report the timeout as an error;
  - no publication permits are granted in that round.

**Operator action after a timeout:** check `status`, then run `engineer` again without `--objective`. The new run adopts sessions that are still alive (see [§7](#7-resume-semantics)).

### 5.4 Execution routing

Staffing selects a specialist first; execution routing then selects the provider, model, reasoning effort and native session arguments. In `[routing]`, `default` covers specialist sessions, `escalation` handles escalated work, `fallback` applies while the default is disabled, `utility` is for lightweight work, and `escalate` can name capabilities or capability/role pairs (`routing.py:4-35`).

A Project Manager staffing decision can request an escalation class for an assignment. Route names are organization-defined, and configured provider and model identifiers are passed through. Routes are execution state: they can change between attempts while the specialist and work identities stay the same. The Engineer expresses work needs or escalation requests without choosing the provider or model; use preflight to see the resolved routes (`routing.py:36-52`, `cli.py:336-356`).

### 5.5 The sandbox edge

For a sandboxed `SOURCE_MODIFYING` assignment, only `git add [--] <path in the frozen scope>...` and `git commit -m "<message>"` may run outside the sandbox (`supervision.py:91-97`). `git add` must name at least one path and may use `-A`, `--all`, `-u`, `--update`, `-v` or `--verbose`. `git commit` must provide a message with `-m`, `--message` or `--message=...`; it may use `-q`, `--quiet`, `-v`, `--verbose`, `-s` or `--signoff`. Any path arguments must be plain paths inside the frozen scope; unsupported flags, absolute paths, `..`, pathspec magic and wildcards are refused (`supervision.py:98-100`, `supervision.py:294-347`).

The assignment must allow shell use and have `SOURCE_MODIFYING` finalization. The request must wrap exactly one plain command in a trusted system shell; any nonempty `commandActions` must contain exactly that command. The command must run from the assignment worktree root, whose `.git` pointer must still identify the repository-created linked worktree (`supervision.py:187-240`, `supervision.py:243-291`, `supervision.py:350-374`, `supervision.py:403-428`).

Every other sandbox-exit request is refused with a reason. Taskforce sends the decision and reason to the transport and records them in an `assignment_approvals` row, along with the request, assignment, call, tool, subject and decision time (`supervision.py:377-440`, `taskforce.py:913-930`).

### 5.6 Review verdicts

Reviewers are asked to put exactly one `AUTOBOT_REVIEW: EXAMINED` or `AUTOBOT_REVIEW: NOT_EXAMINED` line in their final response. `EXAMINED` means they read the work under review and the source it must agree with, name what they read as `path:line`, and state what is wrong or report `[]` findings; if they could not read either, they state `NOT_EXAMINED` and what stopped them (`prompts.py:140-154`).

`extract_review_verdict` reads the explicit verdict line; an absent, unrecognized or conflicting verdict produces no verdict, and prose is not used to infer one (`findings.py:91-114`). A review workstream's `review_verdict` is the latest collected attempt's value: `EXAMINED`, `NOT_EXAMINED`, or `NOT_STATED` when there is no verdict line; it is `None` until an attempt is collected (`query.py:193-203`).

Among completed reviews for a record, `unexamined_reviews` lists it when none says `EXAMINED` and at least one says `NOT_EXAMINED`; reviews that only say nothing do not list the record (`query.py:206-229`). `PROJECT_COMPLETE` is refused when a reviewed record has no completed review reporting `EXAMINED` and at least one completed review reporting `NOT_EXAMINED` (`query.py:206-229`, `policy.py:1097-1111`).

### 5.7 One assignment, one session

`SESSION_RULES` tells each specialist to do its own task in one session (`prompts.py:79-87`). It is included in both `READ_ONLY_CONTRACT` and `SHELL_NOTE` (`prompts.py:102-120`, `prompts.py:123-130`). `compose_specialist_prompt` adds `SHELL_NOTE` to `SOURCE_MODIFYING` prompts and adds either `READ_ONLY_CONTRACT` or `SHELL_NOTE` for other prompts, so each prompt gets one of the two (`prompts.py:241-253`).

For a provider that asks permission before each tool use (`can_use_tool`), `decide` checks `READ_TOOLS`, `SHELL_TOOLS` and `WRITE_TOOLS`; its last return denies any tool outside those lists (`supervision.py:76-79`, `supervision.py:443-525`).

For sandboxed Codex, the approval request is one of three native kinds: command execution, file change or permissions. `decide` sends these requests to `_decide_codex`, which handles the request as leaving the sandbox (`supervision.py:83-90`, `supervision.py:377-440`, `supervision.py:443-456`). That path asks about leaving the sandbox, not each tool use; the one-session rule on this provider is carried by the prompt contract alone (`supervision.py:377-394`, `supervision.py:443-456`, `prompts.py:117`, `prompts.py:129`, `prompts.py:241-253`).

### 5.8 What a session may reach

1. **What every specialist is told.** `TOOL_RULES` says that work may reach only this machine's files and the session's shell; connectors, apps, plugins, web access, browser or desktop control, and remote repository copies are outside the assignment (`prompts.py:94-100`). Both `READ_ONLY_CONTRACT` and `SHELL_NOTE` carry the rule (`prompts.py:118`, `prompts.py:129`), and prompt composition gives every specialist one of those texts (`prompts.py:241-249`).

2. **What a per-tool approval provider can reach.** For a provider that asks before each tool use (`can_use_tool`), `decide` recognizes only the three tool lists, and its last statement refuses any tool outside them. Connectors and the web are not in those lists (`supervision.py:76-79`, `supervision.py:523-525`).

3. **What the sandboxed default route can reach.** The default `luna-6-max` route uses `workspace-write` sandboxing, `on-request` approval and configured writable gate paths. Its session arguments switch off apps and plugins, computer and browser use, image generation, goals, web search and the configured `node_repl` MCP server (`config.toml:63`, `config.toml:91-106`). They also set `features.multi_agent=false`, which the comment says does not remove sub-agent tools; the execution contract forbids those tools (`config.toml:83-86`). The comment says the overrides switch off inherited apps and connectors, plugins, MCP servers, web search, browser and desktop control for this session only, without changing the operator's configuration (`config.toml:75-82`). `_route` preserves the route's argument values when it builds the route (`routing.py:93-123`).

---

## 6. Provider failure handling

### What happens

1. The provider's outcome is normalized, and the attempt is finished with it (`taskforce.py:998-1000`).
2. In the same sweep, retry rule t-s08 returns the sequence to **READY**, but only while the contract's retry budget lasts (`taskforce.py:1182-1218`).
3. READY does **not** restart anything. A new attempt needs an explicit `REPAIR_TASK` or `ASSIGN_SPECIALIST` authorization from the Engineer (`taskforce.py:253-262`).
4. When the retry budget is spent, which happens only after all `1 + max_retries` attempts have failed, a `retry_exhausted` event is emitted **once**, and the sequence **stays EXECUTING**. That work can continue only as a **new contract**.
5. The CLI prints the provider's error text under each workstream (`cli.py:232-238`). `status` cuts it at 160 characters.

### Retry budgets

Two independent budgets limit retries.

| Budget | Scope | Limit | Default |
|---|---|---|---|
| Contract retry policy | Per sequence | `1 + max_retries` attempts (`sequencer.py:375-381`) | `max_retries = 2` in the production configuration, so **three** attempts |
| Objective `attempts` budget | Per objective | `--max-attempts` | 24 when the flag is omitted (`planstore.py:35-41`) |

**The production effective retry policy is `max_retries = 2`.** The production defaults set it (`production_seed.py:234`). The compiler copies it into every contract that does not set its own (`compiler.py:175-176`), and the runtime applies the production defaults when the authority configuration supplies none (`runtime.py:459-464`). The Sequencer's fallback of `max_retries = 0` (`sequencer.py:375`) applies only to a payload with no `retry_policy`, which compiled contracts never lack.

After a provider failure, the sequence returns to **READY**. It runs again once the Engineer authorizes a `REPAIR_TASK` or `ASSIGN_SPECIALIST` (`taskforce.py:253-262`). `retry_exhausted` occurs only after all three attempts (`1 + max_retries`) have failed.

Every attempt, including a retry, also spends the objective's `attempts` budget (`taskforce.py:423-429`).

### Operator action

- **To read the error:** look at the failed-attempt lines in the `engineer` output or in `status`.
- **If the objective's `attempts` budget is spent:** raise it on a resume:

  ```
  autobot engineer --objective-id <id> --max-attempts <N>
  ```

- **After a first or second failure:** no action is needed for the retry itself. The sequence is READY, and the next attempt starts once the Engineer authorizes a `REPAIR_TASK` or `ASSIGN_SPECIALIST`. Each retry also spends the objective's `attempts` budget, so check it if retries are not starting.
- **If the error is `retry_exhausted`:** all three attempts have failed. Raising the objective budget does not help. The work has to continue as a new contract, which the Engineer must propose.
- **If an attempt hangs in `RUNNING`:** nothing will fail it automatically (see [§5.2](#52-in-flight)). Each run waits until the wall bound and then exits 2.

---

## 7. Resume semantics

A later `engineer` run, whether a resume or a fresh run, keeps no memory of an earlier process. **It re-reads everything from the ledger on every sweep.** The record of which sessions were launched lives only in the old process (`taskforce.py:149`).

### What a later run detects

| Ledger state found | What the run does | Source |
|---|---|---|
| Active objectives | On a resume it emits `operator_resumed` for each one, which forces an Engineer cycle. Every run drives all of them. | `runtime.py:657-674`, `cli.py:702` |
| `DISPATCHED` attempt whose session the transport still has | `resume_interrupted` skips it; adoption happens in `reconcile_dispatched`, which each sweep calls first. | `taskforce.py:865-866`, `taskforce.py:1327`, `transport.py:139` |
| `DISPATCHED` attempt for which the transport reports a definite launch failure | `reconcile_dispatched` fails the attempt on the transport's positive launch-failure determination, then the provider-failure handling in [§6](#6-provider-failure-handling) applies. `resume_interrupted` leaves it alone. | `transport.py:166-169`, `taskforce.py:867-869` |
| `DISPATCHED` attempt in any other case | Re-launches it **under the same call id**. | `taskforce.py:861-874` |
| Attempt with no assignment row, or whose assignment status is not SELECTED or DISPATCHED | Not resumed. | `taskforce.py:858-860` |
| Verified sequences without a permit | Stay parked unless `--authorize-publication` is given. With the flag, sequences from earlier runs are included. | `cli.py:725-739` |

**How `reconcile_dispatched` handles DISPATCHED attempts.** `advance()` calls `reconcile_dispatched` before `resume_interrupted` (`taskforce.py:1327`). For each DISPATCHED attempt (`transport.py:139-175`):

- if the transport finds the session, it is adopted: the obj-11 witness is admitted and the attempt is set RUNNING;
- if the transport positively determines a launch failure, the attempt is failed via `fail_dispatched_attempt` (`transport.py:166-169`);
- otherwise the attempt stays DISPATCHED, awaiting the next check.

### Known dead end

If the `Sequencer` refused to start an attempt, its assignment is marked **ABANDONED** (`taskforce.py:745-756`). No attempt row was created, so the attempt number never advances. `allocate_ready` then **skips that sequence permanently**, even after a new Engineer authorization (`taskforce.py:827-835`). No way out was found in source. A test has not yet confirmed this.

---

## 8. Not yet verified

Do not rely on any of the following until it has been checked against source:

- what happens when a second driver starts while the lease is held;
- whether `rt.close()` releases the driver lease when the runaway `RuntimeError` propagates;
- the exact meaning of each `--evidence-profile` value;
- what happens when an auto-generated objective id collides with an existing one. Two fresh runs in the same second produce the same id;
- whether the runtime and the reporting commands always resolve the same data directory when `--root` is omitted (`cli.py:39-44`, `cli.py:144-151`).

## Execution-evidence confirmation

Pilot 11: confirmed after F-TF-33, F-TF-34, F-TF-35 corrections. 2026-11-21.
