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

`--root` is defined on the top-level parser only (`cli.py:891-892`), so it must come **before** the subcommand:

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

**The reporting commands are not strictly read-only.** Every open of the plan store sets WAL mode and runs the schema migration inside a `BEGIN IMMEDIATE` transaction (`planstore.py:496-504`, `planstore.py:515-518`, `planstore.py:526-527`). Each reporting command therefore takes a brief write lock. If `intelligence.db` is missing, opening it creates an empty one (`planstore.py:498`), and the command reports no active objectives instead of failing.

### 2.1 `engineer` (`cli.py:549-694`)

The command runs these steps in order:

1. It bootstraps the runtime with `--evidence-profile` (`cli.py:563-564`).
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

**Scope of a run.** The loop always runs over **all** active objectives, including ones from earlier runs. This holds even when you pass a new `--objective` (`cli.py:610`).

The project lines and the `--report` payload use the list of active objectives taken **before** the run. An objective that completed during the run still appears in them (`cli.py:661-663`, `cli.py:674-676`).

#### `engineer` flags

| Flag | Type | Default | Behaviour |
|---|---|---|---|
| `--objective TEXT` | str | none | Text of a new objective. If it is absent or an empty string, the command **resumes** and does not report an error (`cli.py:570`). |
| `--objective-id ID` | str | none | **Fresh run:** the id of the new objective. If omitted, the id is `obj-eng-<YYYYmmddHHMMSS>`, which has one-second resolution (`cli.py:571-572`). **Resume:** selects which active objective the budget flags apply to (`cli.py:591-596`). It does **not** narrow the run. If you give it without any budget flag on a resume, it is silently ignored. |
| `--constraint TEXT` | str, repeatable | none | Passed to `add_objective` on a fresh run only (`cli.py:579`). It is **silently ignored on a resume**. |
| `--evidence-profile` | `READ_ONLY_REPORT` \| `READ_ONLY_GOVERNED` \| `SOURCE_MODIFYING_GOVERNED` \| `BY_CLASS` | `BY_CLASS` | Passed to `bootstrap` on **every** invocation, resumes included (`cli.py:563-564`). A resume can therefore run with a different profile from the original run. The `run` command defaults to `READ_ONLY_REPORT` instead. |
| `--max-contracts N` | int | not set | Budget key. See [Budget flags](#budget-flags). |
| `--max-attempts N` | int | not set | Budget key. Every attempt, including a retry, spends this budget (`taskforce.py:415-421`). |
| `--max-outside-specialists N` | int | not set | Budget key. |
| `--max-repairs N` | int | not set | Budget key. |
| `--max-retention-decisions N` | int | not set | Budget key. |
| `--report PATH` | path | none | Writes the closing state as JSON (`cli.py:670-691`). See [Report file](#report-file). |
| `--max-wall-seconds S` | float | `3600.0` | Wall-clock bound for **each** `rt.run` call, and it is checked only while waiting (`cli.py:874-876`). See [§5.3](#53-wall-clock-timeout---max-wall-seconds). |
| `--poll-seconds S` | float | `2.0` | Interval between polls while waiting on in-flight work (`cli.py:877-879`). |
| `--authorize-publication` | flag | off | After each **stable** round, grants one publication permit to every sequence that meets all of these conditions (`cli.py:633-647`): it is VERIFIED, has no permit, and belongs to a contract proposal of an objective that was active when the command started (the list captured at `cli.py:610`, before the run). The run then continues. See the warning below. |

#### Budget flags

- If you leave a budget flag out, its key is left out of the budget. The CLI never fills in a default, so the effective value comes from the plan store's defaults (`planstore.py:35-41`):

  | Key | Default |
  |---|---|
  | `max_contracts` | 12 |
  | `max_attempts` | 24 |
  | `max_outside_specialists` | 6 |
  | `max_repairs` | 6 |
  | `max_retention_decisions` | 12 |

- Zero is accepted. Negative values are **rejected** by `PlanStore.set_budget` with `ValueError` (`planstore.py:577-583`):
  - On a fresh run, the ACTIVE objective is created first (`add_objective` commits), and then the uncaught `ValueError` produces a traceback and exit 1. The objective is **left ACTIVE**.
  - On a resume, the same value gives `cannot change the budget` and exit 1 (`runtime.py:594-600`).
- On a fresh run, the budget keys go to `add_objective(budget=...)` (`cli.py:573-579`).
- On a resume, they go to `rt.set_objective_budget(objective_id, budget)` (`cli.py:585-604`). This **merges**: the keys you give replace the current limits, and the others keep their current values (`runtime.py:594-596`). See [§3](#3-default-behaviour-when---objective-is-omitted-resume).

#### Warnings about `--authorize-publication`

The help text says the flag grants "one use for each verified SOURCE_MODIFYING sequence of this run". The code does something broader:

- It does **not** check the finalization class. Any VERIFIED sequence without a permit qualifies.
- It includes sequences from **earlier runs**, as long as they belong to an active objective.
- It grants permits only after a **stable** round. A timed-out round grants none.
- The loop is capped at 17 rounds (`rounds > 16`). The grant is evaluated before the cap is checked, so the last round can grant permits and then stop without running again (`cli.py:645`, `cli.py:633-647`).
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

### 2.2 `objectives` (`cli.py:697-748`)

The command lists every objective with its status, id, author and timestamps. Objective text is cut at 160 characters.

**`--close ID`** tries to close the objective before printing the listing. It checks these conditions in order:

1. The objective exists. If not, the command exits 1.
2. No driver lease is live. If one is, the command exits 1. The test compares timestamps as strings, and it is **not atomic** with the close.
3. No unfinished sequenced task exists. If one does, the command exits 1. Only tasks that already have a `sequence_id` are checked, so **planned work without a sequence does not block closing**.
4. The objective's status is checked. If the objective is already non-ACTIVE, the command prints `already <STATUS>` and exits 0.

The listing is printed only when the close succeeds or the objective is already non-ACTIVE (`cli.py:735-744`). The refusal paths in steps 1-3 exit 1 before the listing (`cli.py:711-713`, `cli.py:719-721`, `cli.py:727-729`).

`objectives --close` **writes** to the plan store.

### 2.3 `status` (`cli.py:300-340`)

`status` changes no work state, though opening the plan store takes a brief schema-migration write lock (see [§2](#2-commands)). It does not take the lease, so it is safe to run beside a running driver. It prints:

- active objectives;
- sequences that are not in a terminal state. COMPLETED, ABANDONED and REJECTED sequences are hidden, and only COMPLETED ones are counted;
- **ACTIVE** projects only, with their workstreams, failed attempts and teams. Project titles are cut at 100 characters and provider errors at 160;
- the Taskforce summary.

The help text does not mention projects, but `status` prints them.

### 2.4 `taskforce` (`cli.py:751-814`)

`taskforce` prints:

- the Taskforce summary;
- all members, of every status;
- outside specialists;
- why each retained seat is held;
- the assignment history;
- **all** projects and their teams. `status`, by contrast, shows only ACTIVE projects.

The headline "members" count includes ACTIVE members only, while the `members:` list shows every status. Output is not truncated, so it can be long.

With `--json`, the command prints only the raw `taskforce_report`, without project data.

---

## 3. Default behaviour when `--objective` is omitted (resume)

`engineer` resumes when `--objective` is absent or empty (`cli.py:581-614`). It runs these steps in order:

1. **Budget change (optional).** This step happens only if at least one budget flag is given.
   - `--objective-id` is required. Without it, the command prints an error and **exits 1** (`cli.py:591-594`).
   - The command calls `set_objective_budget(objective_id, budget)`. A `ConfigError` from this call **exits 1** (`cli.py:595-600`).
   - On success it prints `budget set for <id>: k=v, ...` (`cli.py:601-604`).
2. **`signal_resume`** is always called. It emits `operator_resumed` for **every** active objective, which makes the Engineer reason again (`runtime.py:607-624`). If it returns anything, the command prints `resuming: ...` (`cli.py:607-609`).
3. **Run scope.** The loop runs over **all** active objectives, not just the one named by `--objective-id` (`cli.py:610`).
4. **No active objective.** The command prints to stderr and **exits 1** (`cli.py:611-614`). By this point the budget change and `signal_resume` have **already been applied**.

On a resume, `--constraint` is ignored. `--evidence-profile` still applies, with a default of `BY_CLASS`.

A fresh run (with `--objective`) sends the budget and constraints to `add_objective` and does **not** call `signal_resume` (`cli.py:570-580`).

---

## 4. Exit codes

### `engineer`

| Code | Meaning | Source |
|---|---|---|
| `0` | The last run was stable **and** no objective remains active. | `cli.py:692` |
| `1` | Bootstrap raised `ConfigError`. | `cli.py:565-567` |
| `1` | Budget flags were given on a resume without `--objective-id`. | `cli.py:591-594` |
| `1` | `set_objective_budget` raised `ConfigError`. | `cli.py:597-600` |
| `1` | Resume found no active objective. | `cli.py:611-614` |
| `1` | Building the engineer policy raised `ConfigError`. | `cli.py:617-619` |
| `1` (traceback) | Any other uncaught exception. Examples: an unwritable `--report` path, or the runaway guard's `RuntimeError` (more than 512 active steps, `driver.py:210-213`). A transport launch error (a non-zero exit from `autoswarm run`) escapes as an uncaught `RuntimeError` (traceback, exit 1) and leaves the attempt DISPATCHED; neither `launch_manifest`, `launch_with_manifest` nor `_drive` catches it. Provider-reported outcomes (session failures after a successful launch) are recorded as failed attempts (`taskforce.py:825-827`). | `autoswarm_transport.py:105-107` |
| `2` | The run **timed out**, **or** it was stable but objectives are **still active**. | `cli.py:692` |
| `2` | An argparse usage error, such as `--root` placed after the subcommand. | argparse |
| platform-dependent | Interrupted (Ctrl-C). A `KeyboardInterrupt` at any point during the command is not caught: `cmd_engineer` has only `try`/`finally` and `main()` has no handler. The process exits immediately; on POSIX the exit code is typically 130. | `cli.py:568-694`, `cli.py:993-994` |

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
| *(no subcommand)* | The program prints help and exits 1 (`cli.py:976-978`). | – |

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

An attempt is **in flight** if it is in one of these states (`taskforce.py:225-231`):

- `DISPATCHED`;
- `RUNNING`;
- `CANDIDATE_FROZEN`, but only while its sequence is also `CANDIDATE_FROZEN`. This means the evidence window is still incomplete (`taskforce.py:943-952`).

`DECLARED` and `RESERVED` attempts are **not** in flight.

While anything is in flight, quiet steps become **wait** steps. The driver polls every `--poll-seconds` and checks the wall clock.

The operator sees only bare attempt ids, in the line `attempts still in flight: …` (`cli.py:657-658`) and in the report's `in_flight` list (`driver.py:149`). The workstream and state are not shown.

> **A hung provider session never times out by itself.** The loop leaves a `RUNNING` attempt alone while it has no provider result (`taskforce.py:807-812`). The staleness rule `mark_member_stale` (`sequencer.py:3954`) exists, but nothing in `src` calls it. A hung session keeps every run waiting until `--max-wall-seconds`, and it does so on every later run too.

### 5.3 Wall-clock timeout (`--max-wall-seconds`)

- **It bounds waiting, not the whole run.** The clock is checked only in the wait branch, when a step was quiet but work is still in flight (`driver.py:215-220`). Steps that do something, including slow Engineer reasoning, never check it. The docstring of `driver.py:186` says it "bounds the whole run", which is not what the code does. The elapsed time is measured from the start of that `rt.run` call.
- **It applies to each `rt.run` call.** With `--authorize-publication`, each continuation round (up to 17) gets a fresh budget (`cli.py:624-647`). The total time can be many times the flag's value.
- **On timeout the run returns at once**, with `stable=False` and `timed_out=True`. Then:
  - nothing is aborted or drained, and no checkpoint is written;
  - **specialist sessions keep running at the provider**;
  - only the durable ledger and the plan-store state remain;
  - the CLI prints `TIMED OUT` on stdout and exits **2**. It does not report the timeout as an error;
  - no publication permits are granted in that round.

**Operator action after a timeout:** check `status`, then run `engineer` again without `--objective`. The new run adopts sessions that are still alive (see [§7](#7-resume-semantics)).

---

## 6. Provider failure handling

### What happens

1. The provider's outcome is normalized, and the attempt is finished with it (`taskforce.py:825-827`).
2. In the same sweep, retry rule t-s08 returns the sequence to **READY**, but only while the contract's retry budget lasts (`taskforce.py:1009-1045`).
3. READY does **not** restart anything. A new attempt needs an explicit `REPAIR_TASK` or `ASSIGN_SPECIALIST` authorization from the Engineer (`taskforce.py:248-257`).
4. When the retry budget is spent, which happens only after all `1 + max_retries` attempts have failed, a `retry_exhausted` event is emitted **once**, and the sequence **stays EXECUTING**. That work can continue only as a **new contract**.
5. The CLI prints the provider's error text under each workstream (`cli.py:232-238`). `status` cuts it at 160 characters.

### Retry budgets

Two independent budgets limit retries.

| Budget | Scope | Limit | Default |
|---|---|---|---|
| Contract retry policy | Per sequence | `1 + max_retries` attempts (`sequencer.py:375-381`) | `max_retries = 2` in the production configuration, so **three** attempts |
| Objective `attempts` budget | Per objective | `--max-attempts` | 24 when the flag is omitted (`planstore.py:35-41`) |

**The production effective retry policy is `max_retries = 2`.** The production defaults set it (`production_seed.py:234`). The compiler copies it into every contract that does not set its own (`compiler.py:175-176`), and the runtime applies the production defaults when the authority configuration supplies none (`runtime.py:409-414`). The Sequencer's fallback of `max_retries = 0` (`sequencer.py:375`) applies only to a payload with no `retry_policy`, which compiled contracts never lack.

After a provider failure, the sequence returns to **READY**. It runs again once the Engineer authorizes a `REPAIR_TASK` or `ASSIGN_SPECIALIST` (`taskforce.py:248-257`). `retry_exhausted` occurs only after all three attempts (`1 + max_retries`) have failed.

Every attempt, including a retry, also spends the objective's `attempts` budget (`taskforce.py:415-421`).

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

A later `engineer` run, whether a resume or a fresh run, keeps no memory of an earlier process. **It re-reads everything from the ledger on every sweep.** The record of which sessions were launched lives only in the old process (`taskforce.py:144`).

### What a later run detects

| Ledger state found | What the run does | Source |
|---|---|---|
| Active objectives | On a resume it emits `operator_resumed` for each one, which forces an Engineer cycle. Every run drives all of them. | `runtime.py:607-624`, `cli.py:610` |
| `DISPATCHED` attempt whose session the transport still has | `resume_interrupted` skips it; adoption happens in `reconcile_dispatched`, which each sweep calls first. | `taskforce.py:693-694`, `taskforce.py:1154`, `transport.py:139` |
| `DISPATCHED` attempt for which the transport reports a definite launch failure | `reconcile_dispatched` fails the attempt on the transport's positive launch-failure determination, then the provider-failure handling in [§6](#6-provider-failure-handling) applies. `resume_interrupted` leaves it alone. | `transport.py:166-169`, `taskforce.py:695-697` |
| `DISPATCHED` attempt in any other case | Re-launches it **under the same call id**. | `taskforce.py:689-702` |
| Attempt with no assignment row, or whose assignment status is not SELECTED or DISPATCHED | Not resumed. | `taskforce.py:686-688` |
| Verified sequences without a permit | Stay parked unless `--authorize-publication` is given. With the flag, sequences from earlier runs are included. | `cli.py:633-647` |

**How `reconcile_dispatched` handles DISPATCHED attempts.** `advance()` calls `reconcile_dispatched` before `resume_interrupted` (`taskforce.py:1154`). For each DISPATCHED attempt (`transport.py:139-175`):

- if the transport finds the session, it is adopted: the obj-11 witness is admitted and the attempt is set RUNNING;
- if the transport positively determines a launch failure, the attempt is failed via `fail_dispatched_attempt` (`transport.py:166-169`);
- otherwise the attempt stays DISPATCHED, awaiting the next check.

### Known dead end

If the `Sequencer` refused to start an attempt, its assignment is marked **ABANDONED** (`taskforce.py:575-586`). No attempt row was created, so the attempt number never advances. `allocate_ready` then **skips that sequence permanently**, even after a new Engineer authorization (`taskforce.py:655-663`). No way out was found in source. A test has not yet confirmed this.

---

## 8. Not yet verified

Do not rely on any of the following until it has been checked against source:

- what happens when a second driver starts while the lease is held;
- whether `rt.close()` releases the driver lease when the runaway `RuntimeError` propagates;
- the exact meaning of each `--evidence-profile` value;
- what happens when an auto-generated objective id collides with an existing one. Two fresh runs in the same second produce the same id;
- whether the runtime and the reporting commands always resolve the same data directory when `--root` is omitted (`cli.py:39-44`, `cli.py:144-151`).
