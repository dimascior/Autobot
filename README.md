# Autobot

Autobot (the `swarm-coordinator` package) is the layer that starts and tracks agent runs, so nobody has to launch them by hand. It reads a curated backlog, has Claude decide what runs, launches each run through **AutoSwarm**, and parks the result for a human to close.

It uses the agent CLIs' own subscription logins, never API keys, and it launches nothing until you turn dry-run off.

**If you're an agent, read [Read this first](#read-this-first) before you do anything else.** Most mistakes made in this system came from an agent drawing the wrong picture of it and then building confidently on that picture.

**Status:** the launch chain below has run live once, from one `--once` cycle. The timer loop has not run live, and the [known limits](#known-limits) still apply.

## Read this first

### The picture

```text
you or a timer
 └─ coordinator --once                        Autobot: backlog, ledger, safety caps
     ├─ claude -p                             planner: picks tasks, writes each prompt
     └─ autoswarm run --agent claude-code     Autobot's Claude session (route "opus")
         ├─ autoswarm run --agent codex --input <prompt file>    worker, e.g. Luna
         └─ autoswarm run --agent claude-code --input -          monitor
```

Each layer has one job:

- **Autobot** decides a task should run and starts a session for it. It doesn't do the task, and it only tracks the session it started.
- **Autobot's Claude session** is the orchestrator. It's an AutoSwarm child, not an interactive session. For a long run it starts the worker and the monitor through AutoSwarm, then ends.
- **AutoSwarm** runs each child. With `--input <file>` it reads the prompt file and sends the text as the child's first message.

The point of this arrangement is that a run starts with **no manual step** once Autobot is running. AutoSwarm on its own is already proven. Autobot starting the run is what this repo adds.

### Mistakes already made here

Each of these was made by an agent that was sure it understood the system.

| If you're about to... | Stop, because... | Do this instead |
|---|---|---|
| Run `autoswarm run` yourself for a backlog task | That's AutoSwarm alone. Autobot plans nothing and records nothing | Add a backlog task and run a cycle |
| Give the user commands to paste | That's the manual step Autobot exists to remove | Put the steps in a task file that Autobot's Claude session reads |
| Start a new session to "run the coordinator" | A wrapper session isn't the coordinator, and it may be on the wrong model | Start `coordinator` directly from a terminal or a timer |
| Add code so Autobot sends your long prompt | Autobot's job ends at starting the session | Point the task at a file, and let AutoSwarm read prompts with `--input` |
| Pick a model yourself | The models are part of the design | Use `[routes.*]` and `[models]` in `config.toml` |
| Tell a cold Codex session to read its instructions from a file | Its shell is gated and it doesn't yet know the gate | Send the whole prompt as message text |
| Move helpers into a file to shrink a prompt | The prompt that worked cold carried everything itself, and a file read added a new way to fail | Keep it self-contained until a file read is proven in that runtime |
| Believe a monitor's diagnosis | A monitor called a harmless cleanup error a write failure, and a fix was built for a problem that never happened | Check the session records yourself ([Reading a run](#reading-a-run)) |
| Assume a local `.command-gate` means ungated | That workspace's own hook runs the gate | See [The gate](#the-gate) |
| Say "ready" after reading the code | Reading proves nothing about a live path | Run the tests and a cycle, and say what you ran |

When the user corrects your picture, stop and restate it in their words before you change anything. Search the earlier sessions if you need to. Their words about what the system is for outrank what you infer from the code.

### Before you answer "is it ready?"

1. Say what you mean by ready: a dry cycle, one live task, or the timer loop.
2. Run the [checks](#run-it) and report their results.
3. Separate what you ran from what you only read.

## Run it

You need Python 3.11+, `uv`, the `autoswarm` CLI, and the agent CLIs your routes use (`claude`, `codex`), each logged in to its subscription.

From the checkout root, in PowerShell:

```powershell
$env:PYTHONPATH = 'src'
$env:AUTOSWARM_HOME = '<your AutoSwarm home>'
uv run --no-project --python 3.12 python -m unittest discover -s tests
uv run --no-project --python 3.12 python -m coordinator.discover --check
uv run --no-project --python 3.12 python -m coordinator.loop --once --dry-run
```

Or run `pip install -e .` and use the console scripts in [Entry points](#entry-points).

- **Set `AUTOSWARM_HOME` explicitly.** Inside a packaged desktop app, the default home can be redirected, so runs land where a normal terminal can't see them.
- **In a `.cmd` script, write `call uv ...`.** `uv` can be a batch shim, and without `call` the script ends after the first `uv` line.
- **Dry cycles count.** They use up `max_cycles_per_hour` and `daily_ceiling`, so don't run many.

Copy `backlog.example.toml` to `backlog.toml` (gitignored) for your own tasks. Machine paths are discovered, so `config.local.toml` (also gitignored) is only for overrides.

## Start a long run through Autobot

This is the full chain from [the picture](#the-picture).

### 1. Write the prompts

Follow [Writing a prompt for a cold session](#writing-a-prompt-for-a-cold-session) and [Writing a monitor prompt](#writing-a-monitor-prompt). Keep them as plain ASCII files.

### 2. Write a task file for Autobot's Claude session

```text
You are the swarm orchestrator for one run. Autobot started you through AutoSwarm.
No human is watching, so don't ask questions. Do these steps in order, report, and end.

Your shell is gated. Each command returns a gate template the first time. Complete it
for that command. If a command is refused for any other reason, stop and report the text.

1. Confirm the services the worker needs are answering. If not, stop and report.

2. Start the worker. AutoSwarm reads the prompt file and sends it as message text:
   autoswarm run --agent codex --model <model> --effort <effort> --cwd <worker workspace> --background --no-parent --input <worker prompt file> --provider-arg=--sandbox --provider-arg=danger-full-access --provider-arg=--ask-for-approval --provider-arg=never
   The call ID is at .data.call_id. If there is none, stop and report.

3. Start the monitor, with the call ID on the first line of its prompt:
   ("WORKER_CALL_ID=<call id>`r`n" + (Get-Content -Raw '<monitor prompt file>')) | autoswarm run --agent claude-code --model <model> --cwd <gate-bearing workspace> --background --no-parent --provider-arg=--dangerously-skip-permissions --input -

4. Confirm calls/<call id>/handle.json exists for both.

5. End with both call IDs and any step that failed. Don't wait for the worker.
```

Background runs keep going after this session ends. `AUTOSWARM_HOME` is inherited from Autobot.

### 3. Add one backlog task

```toml
[[task]]
id = "my-long-run"
domain = "example"
title = "Start the worker and its monitor through AutoSwarm"
description = "You are the swarm orchestrator. Read the file <path to task file> with your file-reading tool and carry out its steps exactly, in order. Keep this instruction and that file path word for word in the prompt."
task_class = "investigate"
route = "opus"
priority = 10
evidence = "Why this run exists."
depends_on = []
```

In a live cycle the planner rewrites the prompt from this text, so ask for the path to be kept word for word. Double any backslashes, because this is TOML.

### 4. Hold everything else back

Every open, unblocked task can launch in the same cycle. To hold one, add your task to its `depends_on`. It stays blocked until your task is closed.

Removing a task from `backlog.toml` doesn't retire it, because the ledger keeps it. See [Known limits](#known-limits).

### 5. Make a one-cycle live config

Copy `config.toml` to `config.<name>-run.toml` (gitignored) and change these values:

```toml
[caps]
max_concurrent = 1
max_dispatches_per_cycle = 1

[safety]
dry_run = false
```

`config.toml` stays dry, so nothing else goes live by accident. `config.local.toml` still layers on top.

### 6. Preview, then run

```powershell
uv run --no-project --python 3.12 python -m coordinator.loop --once --dry-run --config config.<name>-run.toml
uv run --no-project --python 3.12 python -m coordinator.loop --once --config config.<name>-run.toml
```

A healthy live summary reads `Status: completed`, `Dry run: False`, `Dispatched: 1`. `Parked` counts approval-class tasks, which don't launch.

### 7. Confirm it came up

Use the log line `dispatched <task> -> call_id=<id>`, then check [the run's records](#reading-a-run). Read the `result.json` of Autobot's Claude session, and confirm a new call folder exists for the worker and for the monitor.

### 8. Close it

Autobot tracks only the session it dispatched, not the worker that session started, so its verdict says nothing about the worker. Read the worker's own result first.

```powershell
uv run --no-project --python 3.12 python -m coordinator.loop --once --dry-run
uv run --no-project --python 3.12 python -m coordinator.authorize --list
uv run --no-project --python 3.12 python -m coordinator.authorize --resolution <n> --authority <who> --approve
```

The first command reads the result and proposes a verdict. Only an authority's approval closes a task, and closing it unblocks whatever you held back in step 4.

## Writing a prompt for a cold session

A child starts with no conversation and no memory. Don't count on the environment to fill that in: `~/.codex/AGENTS.md` may be empty, and codex-handoff's `SessionStart` hook fires only on compact or resume, not on a fresh start. **The message has to carry everything.**

**Deliver it as text.** Use `autoswarm run --input <file>`. Pasting through rendered Markdown can strip backslashes and backticks, and curl your quotes.

**Give exact commands.** A form the hook has already admitted beats a description. A cold run that had to work these out took several rejections:

- **Chain:** every gate after the first needs `previous_correlation_id` set to the last *admitted* ID, or it's rejected with `CHAIN LINKAGE`.
- **Terminal writes:** the body must be a PowerShell hashtable so the hook can read `enter`, or it's rejected with `WRITE FIELD`:
  ```powershell
  -Body (ConvertTo-Json -Compress @{
  data='your command here'
  enter=(1 -eq 1)
  })
  ```
- **Pipes:** a pipe in the outer command is `UNDECLARED CHAINING`. Pipes are fine inside `data='...'`.
- **Terminal reads:** `(Invoke-RestMethod -Uri '<url>/read?tail=N' -Method Get).buffer` prints the raw buffer. A bare call prints a table that hides it.
- **IDs:** put the time in the prefix and take numbers from one helper. The hook refuses an ID that already has evidence.
- **ASCII only:** the hook misreads non-ASCII, so such a command can never match its gate.

**State the runtime.** Codex's `exec` JavaScript has no `require`, `crypto`, `TextEncoder` or `process`. Give it any function it needs, such as SHA-256. If `exec` answers `Script running with cell ID N`, it must call `wait` and not start another `exec`.

**Tell it how to judge results:**

- A command was rejected only if the call throws or the output starts with `Command blocked by PreToolUse hook`. Terminal buffers can quote gate text, so searching the whole output miscounts.
- A closing marker also appears in the terminal's echo of the command, so test for it alone on a line.
- A failed delete of a rejected gate with `Failed to read ... cannot find the file` is harmless, because the hook already moved it.

**Use full-access flags for Codex workers.** Gate files live outside the worker's folder. On the default sandbox, the first write stalls on an approval that nobody answers, so pass `--sandbox danger-full-access --ask-for-approval never`.

**Watch the size.** A full eight-part investigation ended at 92% of a 258k window with a 21k-character prompt, just under compaction near 95%. Code the model retypes costs context twice and picks up typos, so give retyped code a self-test.

## Writing a monitor prompt

A monitor is a Claude Code child that only reads. Monitors have failed in each of these ways:

- **It ended itself.** A headless session ends the moment it replies without calling a tool, and nothing wakes it. One replied "next check-in in 3 minutes" and was gone. Tell it to write check-ins to a log file and reply only when finished.
- **It watched the wrong run.** `result.json` appears only when a call ends, so "the newest `result.json`" belongs to some older call. Pass the worker's call ID on the prompt's first line.
- **It read old artifacts.** Old findings files can look finished. Compare them with `started_at` in the worker's `handle.json`.
- **It stopped too soon.** One gave up after 30 minutes and reported a stall, while the worker finished 26 minutes later. Set stop conditions: the call's `result.json` exists, the deliverable is complete, or a long period passes with no progress.
- **It matched its own prompt.** A prompt contains words like `GATE_REJECTED`, so search for strings only a real event contains, such as `Command blocked by PreToolUse hook: HELIOS/1 DENY`.
- **It miscounted gates.** Rejected gates never reach `evidence/`, so count verdicts in the session ledger.
- **Its sleep failed.** A monitor's shell is gated too, so `Start-Sleep` needs its own gate each time, plus a tool timeout longer than the sleep. Allow that one exception in the prompt.

File tools aren't gated, so do everything else with them.

## The gate

If your routes run under the Helios command gate, every `Bash` and `PowerShell` call in every child goes through a `PreToolUse` hook.

**Who's gated:**

| Child | How it reaches the gate | Hook limit |
|---|---|---|
| Codex | `~/.codex/hooks.json` | Set there (60 s on the proven setup) |
| Claude Code, workspace with its own `.command-gate` | The global wrapper hands off to that workspace's `.claude/settings.json` hook | Set there (15 s on the proven setup) |
| Claude Code, any other workspace | The wrapper in `~/.claude/settings.json` runs the global gate | Set there (15 s on the proven setup) |
| Autobot's own `subprocess` launch | Not gated, and its children still are | n/a |

A "defer" in the global wrapper only passes the call to the workspace's hook. It doesn't mean ungated. Run `coordinator-verify` to see the verdict for each route.

**The handshake:**

1. Run your command. The hook answers `GATE REQUIRED` with a template, the detected tier and the file path.
2. Write the gate to that path with `command_sha256` empty, then retry.
3. The hook answers `GATE REJECTED` with the expected SHA. Patch it in and retry.

Gates are single-use, so a repeat run needs a new ID. `helios gate version` and `helios gate scaffold` are virtual commands: they consume no gate, and `scaffold` gives you prefilled segments when a pipe makes them hard to write.

**Traps:**

- A session's first command can take about 14 s against a 15 s limit, and a timed-out hook can fail open. Make the first command harmless, such as `helios gate version`.
- After a Helios Reset, every older session is denied with `INTEGRITY_DRIFT`, including for gates that are correct. The denial suggests running Reset, which won't clear it. Use a new session. File tools keep working.
- Never edit your own baseline, and don't route around a denial through another tool. Tell the user and let them choose.

## Reading a run

Check the records yourself before you believe anyone's summary, including your own.

| Question | Where to look |
|---|---|
| What was launched, and with which prompt? | `$AUTOSWARM_HOME/calls/<id>/task.json` |
| Is it running? Which session is it? | `calls/<id>/handle.json`: `started_at`, `native_session_id`, `phase` |
| How did it end? | `calls/<id>/result.json`: `outcome`, `content`. Written only when the call ends |
| Gates admitted and rejected | `<gate_root>/session/session-ledger-<native_session_id>.jsonl`: `"verdict":"ALLOW"` and `"verdict":"DENY"` |
| What a Claude child did | `~/.claude/projects/<slug>/<native_session_id>.jsonl`. The slug follows the real path, through any junctions |
| What a Codex child did | Codex Claude DevTools, below |

**Codex Claude DevTools** normalizes Codex rollouts, including the operations nested inside `exec` cells. Its web API is off in the desktop app until you turn on Settings → General → **Enable server mode**. Then, on port 3456:

- `GET /api/codex/sessions` lists sessions. Each `id` looks like `yyyy/mm/dd/rollout-*.jsonl`.
- `GET /api/codex/session?id=<id>` returns the session.

The useful fields are:

- `timeline`: agent messages and executions with status and output.
- `stats`: commands, failures and files written.
- `tokenUsage.lastTurn.inputTokens`: how full the context is.
- `runtime.turns[0].settings`: the real model, effort, sandbox and approval policy.

Reasoning is encrypted, so read the agent messages for the why. Responses are large, so summarize them rather than printing them. Without the API, `scripts/codex-export.ts` in that repo gives the same data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A Codex child stalls at its first gate write | Default sandbox asks for an approval nobody answers | Use the full-access flags in `routes.luna` |
| `autoswarm calls` shows nothing | The run landed in another AutoSwarm home | Set `AUTOSWARM_HOME` everywhere |
| `INTEGRITY_DRIFT` on every command | The session is older than the last Reset | Use a new session |
| `CHAIN LINKAGE` | The gate has no `previous_correlation_id` | Set it to the last admitted ID |
| `WRITE FIELD: cannot extract enter` | The write body is a JSON string | Use the hashtable form |
| `UNDECLARED CHAINING` | A pipe or `;` in the outer command | Move it inside `data='...'` |
| `correlation_id already used` | That ID already has evidence | Use a new number |
| `Failed to read ... .gate.json` from `apply_patch` | The hook already moved that gate | Ignore it during cleanup |
| A monitor ended after one check-in | It replied without calling a tool | See [Writing a monitor prompt](#writing-a-monitor-prompt) |
| `cycle cap` or `daily cap` | Dry cycles count too | Wait, or run fewer cycles |
| `concurrent cap` on every cycle | Dispatches are still active | See the dead-child limit below |
| A script stops after its first `uv` line | `uv` is a batch shim | Write `call uv ...` |
| `tomllib` import error | Python older than 3.11 | Use `uv run --python 3.12` |

## Known limits

- **Backlog prompts cold-start.** A task with no task file gets a one-paragraph prompt from the planner, with no gate or terminal instructions. That's fine for a Claude session, which can read files, but it isn't enough for a cold Codex worker. Don't route such tasks to Codex and expect them to finish.
- **A dead child holds its slot.** A dispatch leaves `running` only through `result.json`. If a child dies without one, it counts against `max_concurrent` until someone fixes the ledger.
- **Parked tasks can't be released.** The approval queue has no approve command, and `authorize` only handles resolutions.
- **Removed tasks stay open.** Deleting a task from `backlog.toml` leaves it `open` in the ledger, so it can still launch unless it's approval-class.
- **Verdicts are crude.** A `resolved` proposal means the run completed and a findings file appeared after dispatch, even one from an unrelated run. A `review` verdict can't be closed through `authorize` yet.
- **The timer loop is unproven.** Only one-shot `--once` cycles have run live.

## How a cycle works

Entry: `coordinator` → `load_config()` (merges `config.toml` and `config.local.toml`, then fills paths through `discover.apply_to_config()`) → `run_loop()` → `one_cycle()`. Each cycle, in order:

1. **`dispatch.sync_active_dispatches`:** reads `calls/<id>/result.json` for active dispatches. A completed run parks its task at `awaiting_resolution`, and a failed one goes to `failed`. This comes first so a full cap can't block reading results.
2. **`safety.run_safety_checks`:** `STOP` file, concurrency cap, cycles per hour, daily ceiling. Returns the free slots, or halts the cycle.
3. **`ingest.ingest`:** sweeps `backlog.toml` (the only dispatchable tasks), plus AutoSwarm calls, findings files and gate folders as evidence. Malformed JSON is quarantined.
4. **`resolve.evaluate_awaiting`:** proposes a verdict for each parked task. It never closes one.
5. **`state.compute_state`:** applies staleness and blocking, then ranks the dispatchable tasks.
6. **`planner.plan`:** drafts the batch through the `claude` CLI in a live cycle, or by rules in a dry one.
7. **Validation in `loop.one_cycle`:** drops unknown, non-task and non-open items, and trims auto items to the free slots.
8. **`safety.gate_batch`:** parks `modify`, `rebaseline`, `reset` and `rewrite` tasks.
9. **`dispatch.dispatch_batch`:** reserves a dispatch row before launching, so a restart can't launch twice, then runs `autoswarm run`. A dry run logs the intent and launches nothing.
10. **`ledger.complete_cycle`:** records the counts, and the summary is printed.

No cycle stage marks a task `completed`. Only `authorize`, driven by a named authority, does that.

## Reference

### Entry points

`pyproject.toml` installs four console scripts, each also runnable as `python -m coordinator.<module>`:

| Command | Module | Purpose |
|---|---|---|
| `coordinator` | `loop:main` | Run the cycle: `--once`, `--dry-run`, `--config` |
| `coordinator-discover` | `discover:main` | Dependency readiness: `--check`, `--json`, `--no-config` |
| `coordinator-authorize` | `authorize:main` | Close tasks: `--list`, `--resolution N --authority <id> --approve/--reject` |
| `coordinator-verify` | `verify_enforcement:main` | Report whether each route's shell calls reach the gate |

### Layout

```text
swarm-coordinator/
├── pyproject.toml         # package and console scripts
├── config.toml            # committed config: dry by default, machine paths blank
├── config.local.toml      # gitignored: optional machine overrides
├── config.*-run.toml      # gitignored: one-cycle live configs
├── backlog.example.toml   # sample tasks; copy to backlog.toml (gitignored)
├── coordinator.db         # gitignored: the ledger
├── docs/                  # ARCHITECTURE.md, SYSTEMS.md, ROUTES.md
├── src/coordinator/
│   ├── loop.py                # the cycle and entry point
│   ├── ingest.py              # backlog and evidence sweep
│   ├── ledger.py  state.py    # durable state, staleness, ranking
│   ├── planner.py             # picks the batch and writes prompts
│   ├── safety.py  authorize.py  # caps, approval parking, task closure
│   ├── dispatch.py            # autoswarm run, restart safety, result sync
│   ├── resolve.py             # proposes verdicts, never closes a task
│   ├── discover.py            # resolves dependencies per route
│   └── verify_enforcement.py  # per-route gate report
└── tests/test_coordinator.py
```

### Systems it depends on

Autobot discovers these at runtime and vendors none of them. What's required depends on your enabled routes:

| Program | Role | Required when |
|---|---|---|
| AutoSwarm | Execution engine (`autoswarm run`) | Always |
| `claude` / `codex` CLI | The route's agent | That route is enabled |
| codex-handoff | Restores Codex state across compaction | A codex route is enabled |
| Codex Claude DevTools | `codex-export.v1` reader and session API | A codex route is enabled |
| Helios command gate | Gates every shell command | Always (the gate is on) |
| CODEAPI, Akashic, hdb/HeliosEvidenceCatalog | Terminal routing, gate installer, evidence database | Reported, never block |

A route's workspace is the child's `--cwd`, not a dependency. It's resolved at runtime: a `claude-code` route uses the parent of `gate_root`, and a `codex` route uses the latest AutoSwarm session's `cwd`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SYSTEMS.md](docs/SYSTEMS.md) and [docs/ROUTES.md](docs/ROUTES.md).

### Safety

Dispatch is off until `safety.dry_run = false`. Even then, caps (`max_concurrent`, `max_dispatches_per_cycle`, `max_cycles_per_hour`, `daily_ceiling`), a `STOP` file and approval parking bound what a cycle can do. `require_approval` task classes never launch on their own.

## License

MIT, see [LICENSE](LICENSE).
