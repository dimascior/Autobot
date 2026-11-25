# Production proof

What has run in production, with the identities that let anyone check it.
Nothing here is a claim about code that was merely read. Every identity
below was read from the production stores (`intelligence.db`,
`autobot.db`, the content store, AutoSwarm's call records) and can be
read again with `autobot-authority query`.

The evidence bundle the numbers come from is
`pilot-evidence-20261118/` in the production checkout (gitignored, like
`pilot-evidence-20261116/`): database backups, before/after snapshots,
run logs, closing reports and the extracts quoted here.

It covers one production ledger and the ten runs recorded in it: the
SOURCE_MODIFYING authority proof of 2026-11-17, a founding run, and eight
pilots on 2026-11-18 and 2026-11-19. (The READ_ONLY authority proof of
2026-11-16 is in its own preserved bundle.) Nothing was re-run to make an
earlier result look better. Where a run exposed a defect, the run stays
as it happened and the correction is a later record.

The 2026-11-24 session advanced the live orchestration pilot
(`obj-live-orch-tf-v2`) through Opus → Engineer → Taskforce joined
execution, completing all 7 orchestration nodes and 6 project
workstreams. The transport fixes and operational tooling built during
that session are recorded in
[ENGINEERING_LOG_20261124.md](ENGINEERING_LOG_20261124.md).

| What is claimed | Proven by | Sections |
|---|---|---|
| The authority plane decides what becomes accepted state, for READ_ONLY and SOURCE_MODIFYING work | 29 sequences COMPLETED, 87 verification rows, 9 compare-and-swap receipts | 1, 22 |
| The Engineer, on Opus 4.6 1M, solves the technical problem and, as Project Manager, opens, staffs and completes the project | nine projects | 3, 4, 10, 13, 16, 18 to 21 |
| A project and its team are temporary; the Taskforce persists | nine completed projects with every team dissolved; four memberships that outlived them | 5, 9, 16, 22, 23 |
| Outside specialists take part without membership | 8 assignments; one specialist released with no membership | 6, 18, 22, 23 |
| Membership is earned by explicit retention on verified evidence, and retained members are reused | 4 retention decisions; 22 assignments through a seat | 7, 8, 9, 22 |
| Several workstreams run at once, wait for what they depend on, and receive accepted results | sessions at once in Pilots 2, 3 and 4; work that waited for what it depended on and was given the accepted result in Pilots 2 and 4 to 8 | 11, 12, 18, 19, 21 |
| Findings change what the Engineer does next | Pilot 2: two rounds of corrections after two reviews | 13 |
| Work whose attempt the provider failed is recovered | Pilot 1 | 4 |
| The default specialist route is Luna 6 Max; one assignment can be escalated; a route is replaced under unchanged identities | Pilots 3 to 8 | 17, 18, 24 |
| Specialists create the commits that are published. On the default route a builder leaves its sandbox only for `git add` in its frozen scope and `git commit -m` | Pilots 2, 4, 5, 6, 7 and 8; the narrowed rule from Pilot 5 on | 14, 19, 20, 21 |
| Independent review by another specialist in another session | on Claude Code models, Pilot 2 (its reviews found defects that were then corrected); on the default Luna route, Pilots 6 and 8, whose reviewers read everything through the governed shell. Pilot 7's review examined the work and read its source through a provider connector that the route has switched off since. Pilot 4's review was partial and Pilot 5's examined nothing, and neither is counted | 15, 21 |
| A session is one specialist's own work, on this machine | the contract rules of PRs 28 and 29 and the default route's session arguments; Pilots 7 and 8, every tool call of whose sessions was read | 21 |
| What is still open | findings of the organization's own specialists, and what stays open of the two findings read from session records | 25 |
| Provider-native session records (obj-13) enter the authority plane with original bytes | 127 Codex session records, 591/591 governed correlation, 26/26 terminal, registered policy identity in receipt | 27 |
| Execution-reach violations block acceptance | luna-local/v1 authorizes MACHINE_LOCAL only; connector or sub-agent activity produces INTEGRITY_FAILED | 27 |
| The organizational model generalizes to research and operations, not only engineering | WorkProfile (3), ResourceScope (9 kinds), Roles (5), ExecutionProfile (6 policies); 83 tests; 943 total | 28 |

## 1. Source baseline

| Proof | Date | Evidence |
|---|---|---|
| READ_ONLY authority plane, end to end | 2026-11-16 | PR 17 (`b19716a`); the preserved bundle `pilot-evidence-20261116/` |
| SOURCE_MODIFYING authority plane, end to end | 2026-11-17 | PR 18 (`0eca39c`) and PR 19; the specialist-created candidate commit `05e3d1ad91e247cfb767e3c25926f74d3fad9a35` on `main`; record [../../implementation/source_modifying_pilot.md](../../implementation/source_modifying_pilot.md) |

Two things about those runs are findings F-TF-3 and F-TF-4 in
[ARCHITECTURE.md](ARCHITECTURE.md): the driver exited while the specialist
was still executing, and the specialist's permission requests were
approved by a script outside the runtime. Both were closed before the
runs below.

What this document proves was published in these steps. Every merge is a
two-parent merge commit; no branch was rebased or squashed.

| PR | Change | Commit | Remote `main` after the merge | Regression evidence (Python 3.8.10 / 3.12.11 / coordinator / mutations) |
|---|---|---|---|---|
| 20 | The Engineer, projects and the Taskforce | `f6943adf2da2d87dbeca784010fbe9594ddfa399` | `3b03b6a3e93f0e26bdb950c15d42d76c6ad6575d` | 690 / 689 / 41 / 21 of 21 |
| 21 | Recover work whose attempt the provider failed (found by Pilot 1) | `9660c899afc0e65d1ec87b4ebc2b05bd611a87c0` | `99af58889b02ebc294cf252a3f3cec32fff085c8` | 697 / 696 / 41 / 25 of 25 |
| 22 | Give dependent work its predecessors' accepted results (found by Pilot 1) | `c7be90241b03e83aeacda20cda94b274c2e8a6a8` | `5c96da356965960d722e8793a7c1a9694127d1e7` | 699 / 698 / 41 / 26 of 26 |
| 23 | Pilot 2's three accepted RUNBOOK commits, unchanged | `e6c88ea0…`, `f53df432…`, `5894d5e2e7af31016898b7916e655b3fb9c166a9` | `518b59f57264780cb6241209e5487cd3e4f3b776` | source and tests unchanged from PR 22 |
| 24 | Execution routing | `13e6301ce17b3bcbdff90356b3db4bf2bef9285a` | `1732507c629d9608e6706987dc7bc86d5be175f4` | 739 / 738 / 41 / 34 of 34 |
| 25 | Pilot 4's accepted RUNBOOK commit, unchanged; and the sandbox-edge rule narrowed (found by Pilot 3) | `606663ade00219a4b2c1baef0df0aa81dc510067`, `b50fb746e954298bc46a3a378a37a004e56203b1` | `d01929ff18d066e9566f729f14c7de87bfb14233` | 747 / 746 / 41 / 38 of 38 |
| 26 | Pilot 5's accepted RUNBOOK commit, unchanged; and independent review made to count (found by Pilot 5) | `4abc57c249695748a3f5e56054e3eb82577fc1cc`, `f67acad3e28f7aee59c4c3f688a5c00ad7fc83eb` | `816aab4ed1d7a026ca1ef9122f06ac5dbd2c77f1` | 767 / 766 / 41 / 48 of 48 |
| 27 | Pilot 6's accepted RUNBOOK commit, unchanged; and this document as it stood after Pilot 6 | `53667c52daa3d8ea5d60ccfd883b8aa5d18513b5`, `4a29272e43262a4343baea41ac791bbfa6284ed5` | `9d97a34d7b0e29744d3b1f3a98d263d363e7bf88` | source and tests unchanged from PR 26 |
| 28 | One assignment, one session (found by Pilot 6) | `279fc9c1c6a36dad89236fcdf70892de528b9d9b` | `d320d1bf9139703d36c0a450939f088803f0291d` | 769 / 768 / 41 / 50 of 50 |
| 29 | Pilot 7's accepted RUNBOOK commit, unchanged; and a session stays on this machine (found by Pilot 7) | `b32fd6f7c0a75567e6a523b0ca4be733305f454e`, `456c9f51d7d223271ff41823b03a38169d985ef5` | `8ae0922f1e79819a1d927a215133be25561bee1f` | 771 / 770 / 41 / 52 of 52 |
| 30 | Production closure proof | `728cddc` | `5360493` | 826 / 0 |
| 31 | Execution-evidence authority: provider-native obj-13, registered reach policy, command correlation, terminality, policy binding, profile activation | `42e24ed`, `cf3040d` | `1364464` | 860 / 0 |
| 32 | Generalized organizational model: WorkProfile, ResourceScope, Roles, ExecutionProfile | `fda8337` | `320053e` | 943 / 0 |

The founding run and the first half of Pilot 1 ran on `3b03b6a`, the rest
of Pilot 1 on `99af588`, Pilot 2 on `5c96da3`, Pilots 3 and 4 on
`1732507`, Pilot 5 on `d01929f`, Pilot 6 on `816aab4`, Pilot 7 on
`d320d1b` and Pilot 8 on `8ae0922`. Pilot 8's own accepted commit and
this revision of the document are published together by the pull request
that follows PR 29; the final regression on those bytes is section 26.

The runs, in the order they happened. No record of an earlier run was
changed by a later one; where a later change corrected something an
earlier run exposed, the earlier record stands and the correction is a
new record.

| Run | Project opened to closed (UTC) | What it exposed | Corrected by | Confirmed in production by |
|---|---|---|---|---|
| Founding | 2026-11-18 06:12:38 to 06:13:52 | nothing | | |
| Pilot 1 | 06:16:39 to 13:28:57 (stopped at 06:19, resumed at 13:23) | attempts the provider failed were not recoverable (F-TF-15 to 17); accepted results did not reach dependent work (F-TF-18) | PR 21, PR 22 | Pilot 1's own resume; Pilot 2 |
| Pilot 2 | 13:51:50 to 14:29:18 | specialists were pinned to models and a route's effort and arguments never reached a session (F-TF-19 to 21); findings F-TF-23, 24 (open) | PR 24 | Pilots 3 and 4 |
| Pilot 3 | 18:35:34 to 18:49:00 | the sandbox-edge rule of PR 24 was too wide (F-TF-26, 27); four routing defects (F-TF-28) | PR 25; PR 26 | Pilot 5; Pilot 6 |
| Pilot 4 | 18:50:48 to 20:17:06 | a reviewer was not told what its worktree held (F-TF-29) | PR 26 | Pilot 6 |
| Pilot 5 | 21:00:32 to 21:29:31 | a review that examined nothing closed its project (F-TF-30) | PR 26 | Pilot 6 |
| Pilot 6 | 2026-11-19 03:17:37 to 04:16:51 | a builder's session started a sub-agent of its own provider and called its check an independent review (F-TF-31) | PR 28 | Pilots 7 and 8 |
| Pilot 7 | 08:00:45 to 08:40:19 | its reviewer read its source through a provider connector, outside the sandbox and the command gate; reading every session's record then showed a reviewer of Pilot 3 had done the same (F-TF-32) | PR 29 | Pilot 8 |
| Pilot 8 | 09:21:56 to 09:58:22 | nothing | | |

Sections 3 to 16 are the founding run and Pilots 1 and 2; 17 to 19 the
routing change and Pilots 3 and 4; 20 the sandbox edge and Pilot 5's
builder; 21 Pilot 5's review, what was wrong, the fix and Pilot 6, then
the two findings read from session records, their fixes and Pilots 7 and
8. The state all of it left is in sections 22 to 24, what it found in 25,
and the regression of the published bytes in 26.

## 2. The implementation (PR 20)

| Item | Value |
|---|---|
| Commit | `f6943adf2da2d87dbeca784010fbe9594ddfa399`, sole parent `05e3d1ad91e247cfb767e3c25926f74d3fad9a35`, tree `3f368eb2d3eea698a4e00259d928b869ed472399`, 43 files, +16497 / −1414 |
| Pull request | dimascior/Autobot#20, "Engineer, projects and the Taskforce: the organizational model above the authority plane" |
| Merge | `3b03b6a3e93f0e26bdb950c15d42d76c6ad6575d`, parents `05e3d1ad` and `f6943adf`, same tree |
| Python 3.8.10, pytest | 690 passed, 0 failed |
| Python 3.12.11, unittest | 689 tests, OK |
| Legacy coordinator | 41 tests, OK |
| Architecture mutations | 21 of 21 killed |

The repository has no CI status checks. The merge rests on the local
regression evidence above, which the commit message cites by gate.

## 3. The founding Taskforce run

Production's plan store was migrated from schema v1 to v2 on 2026-11-18
(a consistent backup of both databases was taken first). The migration
creates an organization with nobody in it: the snapshot taken before the
first run shows 0 members, 0 seats and 0 projects. The seed that followed
added no authority object (33 objects before and after; the only new
journal entries were two lease acquisitions).

The Taskforce's first member was therefore earned the way every later one
is.

| | |
|---|---|
| Objective | `obj-founding-driver-wait-001` — establish how the production loop behaves while a specialist is still executing |
| Project | `pj_a882418afd31e9d34be1ca9669a432d0` "Driver Wait-State Analysis", opened by the Project Manager (`OPEN_PROJECT`, cycle 43) |
| Workstream | `ws_77a939c08c5211d2c43d822120a040dd` `driver-wait-behavior`, research/investigator, READ_ONLY |
| Frozen work | sequence `sq_752547e3d0c32ea78850acc398e614f0`, record `sha256:8fe27d7b2a74ef2db53214e38a06400727fe75f73a1dc59ae15c522350a2073f`, contract digest `sha256:b85112c614b24898ebd931fcbba944868f2b631819a7cad417bbe1038a5bd0ed`, task contract `tc_8459005c9d0c328e4f5a5a70bc0ab206` |
| Assignment | `asg_59ff5b421b33a3b78a0a6e637288bf1d`: specialist `sp_aa671ebf47f9b3d37540e0a5ee07256d` (`claude-sonnet`), path OUTSIDE_SPECIALIST, decided by PROJECT_MANAGER, standing OUTSIDE, no seat |
| Execution | attempt `at_db2a041ebb600f107102f362acc04309`, call `call_1d0dc028932ca141485101d2a694513f` |
| Verification | `a1` PASS `sha256:8e0c859824ca9bb37f88a923134908e6faaa5a115478c98132e5c7bfc621083d`; `a2` PASS `sha256:81f3405e92960816d7091c757bb99f81defb8b1dc01a274590a836466ea30c61`; `role-transform/investigator` PASS `sha256:63ccd99e352aff74f50ce4a16db010112c82c6ce090d1428c8a45d35df9097b1` |
| Acceptance | acceptance `sha256:cca04799d10fcac0d8fcfc7c511e5bfeca6537e94b606ae4e929f1faa50b1ae2`, release `sha256:3f817ebd602e37eedb0b20e6fe4d6567fab318ef7b0759de89f89cd2a0281404`; sequence COMPLETED |
| Findings | `fnd:eaf5655777f318aa3bf726…` documentation_mismatch (the `max_wall_seconds` docstring is looser than the code); `fnd:918886a663cd3520e2231c…` verification_gap |
| Demonstration | `dem:ad9667c697cef6da0755e8fd69a809516da8aabba41266d768e8c16288590d80`, QUALIFYING, recorded with standing OUTSIDE |
| Workforce decision | `dec:69fc58f0ecb3ab500d7389afbf480b0ef8dc5f4d1a2a5b8a72318e3dd96165a6`, RETAIN, proposal `prop:ec5c668dccc3bb41eef3a88c81e0ae0a088d9dc51d4d53f6cdcc70397ce28027` (cycle 46), binding that demonstration |
| Membership | `tm_3725eabebe5943956125cbd56e765f8a`, ACTIVE since 2026-11-18T06:13:52.725Z |
| Seat | `ts_e5fcdb796547f1982c18496e3e66260a`, research/investigator, RETAINED |

The Engineer's two reasoning cycles, in its own words (recorded before
the proposals were disposed):

> New objective requires understanding ContinuousDriver's production-loop
> wait behavior. […] No retained seats exist, so I must explicitly assign
> an outside specialist.

> […] sp_aa671ebf47f9b3d37540e0a5ee07256d produced a qualifying
> demonstration, so retaining them and completing the project is the
> correct next step.

The project completed and its team was dissolved: the specialist's
participation in `pj_a882418a…` is RELEASED (`project_completed`) while
its membership is ACTIVE. Budget used: 1 contract, 1 attempt, 1 outside
specialist, 1 retention decision.

## 4. Pilot 1: recovery from attempts the provider failed

| | |
|---|---|
| Objective | `obj-pilot1-supervision-soundness-001` — can the assignment supervision policy ever grant a specialist a write outside its frozen contract? |
| Project | `pj_909c7b27324114ef2719c9473c334d78` "Supervision Policy Soundness Audit", opened by the Project Manager (cycle 48) |
| Workstreams | `policy-trace` (`ws_34393f1d52e84ba6bc6a04255da4de73`, research/investigator), `test-coverage` (`ws_537335fa1028d324ca86c595963c6722`, verification/reviewer), `policy-trace-review` (`ws_accebe99cbe9b22ee6a34e7821c0bae4`, verification/reviewer, a review of `policy-trace` that also waits for `test-coverage`) |

The Engineer laid all three out in one decision (`DECOMPOSE_TASK`) at
06:16Z and staffed the first two. Both sessions started at 06:18:25Z and
both ended within six seconds: the provider's usage window had run out.

| Workstream | Failed attempt | Call | The provider's own report (the admitted obj-11 witness) |
|---|---|---|---|
| `policy-trace` | `at_69fc5b024cdf0bcb66620da5722d240d` | `call_6f7125df62b7a4147a6e83dcd3bf5bbe` | outcome `failed`, "You've hit your 5-hour limit · resets 4am (America/New_York)", phase `execution` |
| `test-coverage` | `at_6f1c7cacdb066551cd7b2c753c143329` | `call_2897160929509e7baa226701ef4d4ff1` | the same |

What authority did, and what it did not:

- Both attempts were recorded FAILED (journal 418 and 420) with the
  provider's result as their witness. No candidate, no verification row,
  nothing accepted.
- The reasoning model was unreachable for the same reason (cycle 51
  records two model errors and no proposals). The loop went quiet with
  the objective active and the run ended with exit code 2.
- Both isolated worktrees were removed by the normal settlement; the only
  worktree left in the checkout is the one the 2026-11-17 pilot left
  behind (F-TF-9).

Then nothing could happen. That was a defect, not the provider's: the
pump never asked for the registered retry arc, so both sequences stayed
EXECUTING (F-TF-15), and the Engineer could not see why the attempts had
failed (F-TF-16). Both were fixed, tested and merged (PR 21, `99af588`)
before the run was resumed. The failed attempts were not deleted,
rewritten or hidden: they are the first two rows of the project's
assignment history.

The resume, on the same objective and the same project
(`engineer --objective-id … --max-attempts 8 --max-repairs 4
--max-outside-specialists 5`, 13:23Z):

| Step | Evidence |
|---|---|
| The operator raises the budget the failed attempts had spent | `budget_granted`; attempts 6 → 8, repairs 2 → 4, outside specialists 3 → 5 |
| The Engineer reasons over the project with the provider's report in view | cycle 52: "Both policy-trace and test-coverage workstreams failed their first attempts due to a provider rate limit ('5-hour limit'), not specialist error. […] No releases are warranted since the failures were provider-side." |
| Two `REPAIR_TASK` orders | `prop:bf17bdc9fe9b8865090fe886c…` (staffing `taskforce`), `prop:43500a49696ad3ae9956424ae…` (staffing `same_member`) |
| Both sequences return to READY by the registered retry arc | t-s08 for `sq_af140939b9995d96798e6551f6ab2f58` and `sq_d45210f208eb628af1879fb2d1b6795b`, applied by the Sequencer |
| Fresh attempts on new AttemptContracts | `at_4dff02b13777890439ae2b628f482aa8` (call `call_4bff805dc08b53d790a7c6a038b4adee`), `at_f8a92ebd4c077c1e48b1a2ccaba47872` (call `call_b9cae6b983a6e528b5ddb64f53c43b69`) |

## 5. Retained-member staffing

`policy-trace` named a capability and a role and nobody. The Taskforce
resolved the retained member, for both attempts:

| Assignment | Attempt | Specialist | Path | Decided by | Standing | Seat | Outcome |
|---|---|---|---|---|---|---|---|
| `asg_d6ad9d186824f8bb224e7ef1fe3c5b08` | 1 | `claude-sonnet` | RETAINED_SEAT | TASKFORCE | RETAINED | `ts_e5fcdb796547f1982c18496e3e66260a` | ATTEMPT_FAILED |
| `asg_3c97556c235c4314874d7fe1da42c301` | 2 | `claude-sonnet` | RETAINED_SEAT | TASKFORCE | RETAINED | `ts_e5fcdb796547f1982c18496e3e66260a` | VERIFIED_ACCEPTED |

Record `sha256:a9d4da8065cf7d1ea969acbf9613c2d96a806c17c5c366fff762d0dd38b2a43e`,
contract digest `sha256:db17cd6afdf0eaedbd5e72a38db0ea3cdc0b1501f310404a1ce3c953d1b020e2`.
Verification of the accepted attempt: `a1` PASS
`sha256:5d35ac427813f3e3a87ff3c96e2c30272b80040095a95b1d0c52ba1ec10592fb`,
`a2` PASS
`sha256:95bee0b12b2bdc4fe7f2d96898baba801a95d735be28fe5f3b2818547da941de`,
`role-transform/investigator` PASS
`sha256:c25f047fb4b18028425c4cb5366287d5f242231e8239772698e37db9d0743257`.

## 6. Outside specialist assignment

`test-coverage` needed verification/reviewer, which no member held. The
Project Manager selected an outside specialist explicitly
(`ASSIGN_SPECIALIST`, `prop:44e1e15d6bfddf04c48fa8d95…`), and kept it for
the repair:

| Assignment | Attempt | Specialist | Path | Decided by | Standing | Seat | Outcome |
|---|---|---|---|---|---|---|---|
| `asg_7349ef9751d004218297026a6fbca32f` | 1 | `claude-haiku` | OUTSIDE_SPECIALIST | PROJECT_MANAGER | OUTSIDE | none | ATTEMPT_FAILED |
| `asg_1ad60d40abc3935f9a62a66dc0f3ceb9` | 2 | `claude-haiku` | OUTSIDE_SPECIALIST | PROJECT_MANAGER | OUTSIDE | none | VERIFIED_ACCEPTED |

Record `sha256:f23c7a215f9593cfd489780466fabb97ad5ff1862db10dc17b20359f60053a7d`,
contract digest `sha256:1f93ac57655e08a10407f6893302f36954f5dec2ec8ab3e6ab665cdfb186b7f0`.
Verification: `a1` PASS
`sha256:2800c065838d15b42c0e64c64d1920e5b358f0727f50ff67263e2972934cbcf3`,
`a2` PASS
`sha256:4433e0b77700167d273f178b8763b19ab6c6b98f93ff83b07a5c3361b4a6559e`,
`role-transform/reviewer` PASS
`sha256:a852f32c55f12450cb0acc2c2f2a50ef1a260ed2e03b58bea820f64621f79b92`.

Both fresh attempts were assigned in the same sweep (cycle 53) and ran
concurrently on two specialists in two isolated worktrees.

At that moment `claude-haiku` had a project assignment, verified work and
no Taskforce membership: the snapshot of the Taskforce taken before the
run shows one member, and the membership table gained its second row only
with the decision in section 8.

## 7. Capability demonstration

| Demonstration | Specialist | Work | Outcome | Standing when recorded |
|---|---|---|---|---|
| `dem:692878fa2d463855b2b07eee77845837bd1079257f5fdb6910048b66b21d0c59` | `claude-sonnet` | `policy-trace`, attempt 1 | NOT_QUALIFYING (`ATTEMPT_FAILED`) | RETAINED |
| `dem:875a6f5cd27e6d11da86cc1cf17efe6d4bad9dd3c6f51fab55dbf32507d044c5` | `claude-haiku` | `test-coverage`, attempt 1 | NOT_QUALIFYING (`ATTEMPT_FAILED`) | OUTSIDE |
| `dem:bf0483816467cd79e1586dbfc02a5c7878b1a3e0cae8007ec7892ae9e55c173e` | `claude-sonnet` | `policy-trace`, attempt 2 | QUALIFYING | RETAINED |
| `dem:b03230b8d20f34dbda3e8be15eae5d80bafad91658f2584c1122de601fe25980` | `claude-haiku` | `test-coverage`, attempt 2 | QUALIFYING | OUTSIDE |
| `dem:a1f5b5f1b0f1b029bf27ddc178230fbfecaf29df198f751f61afad3273895989` | `claude-haiku` | `policy-trace-review` | QUALIFYING | RETAINED |

The two NOT_QUALIFYING records are the provider-failed attempts. They
were written before F-TF-16 was fixed, so they carry no provider report
of their own; project state reads the reports from the witnesses. They
did not block selection, repair or retention, and nobody was released for
them.

## 8. Explicit retention

The qualifying demonstration made `claude-haiku` a retention candidate
and nothing else. Membership came from a decision:

| | |
|---|---|
| Decision | `dec:d83b32f45b531da8c7fd0e288ec174906e3d1c7f2d96efa7b1aabdb645b91950`, RETAIN, proposal `prop:0caf20783a1cad7b3462d3e51…` (cycle 56) |
| Binds | `dem:b03230b8d20f34dbda3e8be15eae5d80bafad91658f2584c1122de601fe25980` — sequence `sq_d45210f208eb628af1879fb2d1b6795b`, attempt `at_f8a92ebd4c077c1e48b1a2ccaba47872`, the three verification receipts in section 6 |
| Membership | `tm_de73f28ceaee60eae40d2b45d880a104`, ACTIVE since 2026-11-18T13:26:52.304Z |
| Seat | `ts_bddb43b39c785f99430327912e364331`, verification/reviewer, RETAINED |

The Engineer's reason, as recorded: "The objective constraint says 'a
reviewer whose verified work qualifies is worth retaining.' […] The
organization needs verification/reviewer capability for the remaining
review."

## 9. Retained-member reuse

When the project was laid out, the review had been pre-assigned to a
third, outside specialist (`claude-opus`, decision
`prop:97b9657627b89984bdba23216…`). After the retention the Project
Manager handed the workstream back to the Taskforce
(`ASSIGN_SPECIALIST` with staffing `taskforce`,
`prop:edfecc3ed1f72d3730b103104…`); the earlier decision is recorded as
SUPERSEDED, and `claude-opus` never took part.

| | First assignment | Later assignment |
|---|---|---|
| Specialist | `sp_b2f72881be89546de36037d84fea287d` (`claude-haiku`) | the same |
| Assignment | `asg_1ad60d40abc3935f9a62a66dc0f3ceb9` | `asg_8f307019cf95872f6fc3139ed0f15c0d` |
| Workstream | `test-coverage` | `policy-trace-review` |
| Path, decided by | OUTSIDE_SPECIALIST, PROJECT_MANAGER | RETAINED_SEAT, TASKFORCE |
| Standing | OUTSIDE | RETAINED, seat `ts_bddb43b39c785f99430327912e364331` |
| Attempt, call | `at_f8a92ebd4c077c1e48b1a2ccaba47872`, `call_b9cae6b983a6e528b5ddb64f53c43b69` | `at_8b61bf6da1130eeb26c6f11e532bd9ef`, `call_1bc1f4dccdc730a064d61a05dd018fd2` |
| Verification | section 6 | `a1` PASS `sha256:29647b65f062ff7e439bb58c94014f60b2282ae973ca66e5c162a9cdf61de0c8`, `a2` PASS `sha256:26ad8ee8f76ea26ed467045b7db8d94773b5d2c37740fe824b1716ca5224d82d`, `role-transform/reviewer` PASS `sha256:8c74b2ea97c33903f1617a06892dd92b131c524b808a33e698197f7411744aaf` |

The review record is
`sha256:4e547321ca06b59a3dde4c5f8b7cd236472b16e333dcef736be5e57ba10ffad8`
(sequence `sq_99590f7232d8b5b68bf84709beb70519`). It was BLOCKED on both
predecessors from its admission and became READY only when both had been
accepted and released. The reviewer is not the author of the work under
review (`claude-sonnet`), and the retained seat changed who was staffed
and nothing about verification: the same three assertions, all PASS.

The project completed (cycle 59, `prop:db69348b0f226a6be631004dc…`) with
81 driver steps and 66 waits, stable. Its team was dissolved — both
participations RELEASED (`project_completed`) — and both memberships are
ACTIVE. Budget used: 3 contracts, 5 attempts, 2 outside-specialist
assignments, 2 repairs, 1 retention decision.

What the project found is in section 25. One of its findings is about
the pilot itself (F-TF-18): the reviewer was not given the research trace
it was to review, said so, and verified the question from source. That
was fixed before Pilot 2 (PR 22).

## 10. Pilot 2: a multi-workstream project

| | |
|---|---|
| Objective | `obj-pilot2-operator-runbook-001` — give the operator an accurate runbook for the `engineer` command, stating only behavior verified from source |
| Project | `pj_ead01e70c72ef31f187647cf702ec4ae` "operator-runbook-engineer", opened by the Project Manager (cycle 60), created 2026-11-18T13:51:49.997Z, completed 14:29:18.691Z |
| Source | `main` at `5c96da356965960d722e8793a7c1a9694127d1e7` |
| Run | one `engineer --authorize-publication` process, exit 0 |

The Engineer laid four workstreams out in one decision (`DECOMPOSE_TASK`,
cycle 60) and, as Project Manager, staffed the two the Taskforce could
not. The project grew to eight: two rounds of review findings each
produced a corrections workstream and its own review (section 13).

| Workstream | Class | Sequence | Record | Assignment | Specialist, path, decided by | Attempt, call |
|---|---|---|---|---|---|---|
| `cli-investigation` | READ_ONLY research/investigator | `sq_79f613b7e90fe039ecd78169353a9747` | `sha256:0db097683a674a851c335616e27b30fa2a90925541faba9a992d2a5c07c546f7` | `asg_3e6503c0c50600f880fc3b97980a1ae1` | `claude-sonnet`, RETAINED_SEAT, TASKFORCE | `at_938aa259d19adc3398c53a34a892bd38`, `call_4a2d78a7af68a1e11dbefeb5b87e8d7e` |
| `driver-investigation` | READ_ONLY research/investigator | `sq_74342da38c53a9481db9b8e6eed9570c` | `sha256:2c756510784a8eddda0027b13ddad47a837ce292deb06588459740ebfe28462d` | `asg_0a3d28bffb76f95acaccc4a18d83bc9a` | `claude-opus`, OUTSIDE_SPECIALIST, PROJECT_MANAGER | `at_7316f84b1c47dab0aad8c3acda6f0ee6`, `call_c0ee2df1ca9053a36028c2b95809bf2b` |
| `runbook-authoring` | SOURCE_MODIFYING documentation/builder | `sq_c0016eef57b0fcd266f27c147534abd3` | `sha256:03a27d815d9278cc39adcf318e3d2c896f2bd37d6d825eac3349878b0ef13e66` | `asg_736f4ba7f0b43cbae7f5bc3d80e853e0` | `claude-opus`, OUTSIDE_SPECIALIST, PROJECT_MANAGER | `at_21c27ad9ecbec5ec2721e0c8ed80f36e`, `call_227871f05b2ce70166fe8f3e444be97c` |
| `runbook-review` | READ_ONLY verification/reviewer | `sq_33abd9d18badfde28752ddb30b9c1a0e` | `sha256:714cabbdddd557ac01846c98c7b65939d18b71a5f69e44ceadef077cbdc4c91d` | `asg_45bfc3d94092e3b7b0fc4cc26c7a6c10` | `claude-haiku`, RETAINED_SEAT, TASKFORCE | `at_64e9607424c8016d3cb51f72475e5f3a`, `call_f127d0d543351198a8177818037bf561` |
| `runbook-corrections` | SOURCE_MODIFYING documentation/builder | `sq_00032bce9c68528d6fa7de35158869ed` | `sha256:73e6a4cfd7a7be499a8f24d0703ed7181c3f5c93e79c41b2a335a51c8626009f` | `asg_e7e15d32387897fc116437fcb9f1e30e` | `claude-opus`, RETAINED_SEAT, TASKFORCE | `at_e18fb2e6a8bdb62998fd9ba124417d11`, `call_56d43efc866e9b4b00e1187148f06366` |
| `corrections-review` | READ_ONLY verification/reviewer | `sq_b07effc902db7f245dcc674f970cad5f` | `sha256:68133aa28ab776f2ed3308275af0ae6b34b4bd5a5d8017823d0d38d8c6c67c34` | `asg_67ac890988978e421e83dd5e76a37278` | `claude-haiku`, RETAINED_SEAT, TASKFORCE | `at_6d651872b747cf821ae6c2c62e36125b`, `call_6a4b2d5b6c5a734b3778ee9fc8bc704c` |
| `runbook-corrections-2` | SOURCE_MODIFYING documentation/builder | `sq_6815520800bbe5c13821a032e94b1055` | `sha256:0f41375b56ab034a4a61526107b23844c217038634af2db97f0227dd600a4acc` | `asg_4a82910d75b7da78ded2fa5111f30e9d` | `claude-opus`, RETAINED_SEAT, TASKFORCE | `at_ca8c0f69dc754b5a4ffecb4d1745aaa0`, `call_9e0c2f06be0395461df36afa33421624` |
| `corrections-review-2` | READ_ONLY verification/reviewer | `sq_44e4ff838766f4fb431678d8a02d400d` | `sha256:3fdc89c91905da464793228a2d0f9fe700214c8d26b3699fb266f1695d77d30c` | `asg_8828bc89e1f6b8b9880207efdc32d7bc` | `claude-haiku`, RETAINED_SEAT, TASKFORCE | `at_1ca2505d3115976e4f8d583b8f4f78c9`, `call_34352d72a4bcfb79bc835b5c3eed4f07` |

Every attempt was the first attempt of its sequence and every one was
VERIFIED: `a1`, `a2` and the role-transform assertion PASS for all eight
(24 verification receipts; they are listed with the attempts in
`extracts/obj-pilot2-operator-runbook-001.txt` and
`extracts/timeline-obj-pilot2-operator-runbook-001.txt`). Budget used: 8
contracts, 8 attempts, 2 outside-specialist assignments, 0 repairs, 2
retention decisions.

Both staffing paths were used in the one project. The Engineer's reason
for the outside selection, as recorded: "Investigation (b) must run
concurrently with (a), so it cannot use the same retained
research/investigator member."

The same specialist was staffed both ways inside this project.
`claude-opus` wrote the runbook as an outside specialist; the Project
Manager then retained it for documentation/builder (decision
`dec:b42d454675cf0d9a5c8f2e44b3a2f6e4d2c14ad23796e392ae8cfe1dac363f5d`,
cycle 68, binding demonstration
`dem:e70535b3f51e389f418c799abe06e065944a8f9096ff834f9811276c31c1be02`;
membership `tm_1b65882e902b7a5b38279d92cf24a2c2`, seat
`ts_c31fff88a7cf30af7a2f73740370f927`); and both corrections workstreams
were then resolved to it by the Taskforce through that seat. Its
qualifying research demonstration, by contrast, earned no seat: the
Project Manager released it for research (`RELEASE_OUTSIDE`,
`dec:302795c959274e2ea5e0f9021c6ef09e53bb9c006422a04f8ac0fa3284106bc2`,
cycle 64) because "the organization already holds an established retained
seat for that exact capability and role", and no membership changed.

## 11. Concurrent execution

The two investigations were assigned in the same sweep and ran at the
same time on two specialists, in two sessions and two isolated worktrees,
while the work that depends on them stayed blocked. From the authority
journal:

| Sequence | Transition | Journal | Time (UTC) |
|---|---|---|---|
| `runbook-review` | DECLARED → BLOCKED (unsatisfied `review-of`) | 503 | 13:56:24.698 |
| `driver-investigation` | DECLARED → READY | 504 | 13:56:24.702 |
| `cli-investigation` | DECLARED → READY | 505 | 13:56:24.712 |
| `runbook-authoring` | DECLARED → BLOCKED (unsatisfied `on-cli-investigation`, `on-driver-investigation`) | 506 | 13:56:24.722 |
| `driver-investigation` | READY → EXECUTING | 507 | 13:56:24.810 |
| `cli-investigation` | READY → EXECUTING | 510 | 13:56:25.824 |
| `driver-investigation` attempt | DISPATCHED → RUNNING | 514 | 13:56:26.923 |
| `cli-investigation` attempt | DISPATCHED → RUNNING | 516 | 13:56:29.003 |
| `cli-investigation` attempt | RUNNING → FINISHED | 518 | 13:57:53.654 |
| `driver-investigation` attempt | RUNNING → FINISHED | 536 | 13:58:27.565 |

Both attempts were RUNNING from 13:56:29.003 to 13:57:53.654, 84.7
seconds, as native sessions `164d7d41-a5c8-4262-ab2a-1ca9c68165f5` and
`be80368c-9781-4223-be63-564699656c38`. `runbook-authoring` was BLOCKED
for all of it.

## 12. Dependency release

Nothing started before authority released what it depended on.

| Dependent | Waited for | Predecessor released (t-s41) | Dependent BLOCKED → READY (t-s02) | Started (t-s06) |
|---|---|---|---|---|
| `runbook-authoring` | `cli-investigation` and `driver-investigation` | 13:57:54.142 (journal 533) and 13:58:28.050 (journal 551) | 14:00:23.520 (journal 553) | 14:00:23.602 |
| `runbook-review` | `runbook-authoring` | 14:06:46.781 (journal 593) | 14:10:07.359 (journal 595) | 14:10:07.428 |
| `corrections-review` | `runbook-corrections` | 14:18:56.717 (journal 658) | 14:19:31.416 (journal 660) | 14:19:31.497 |
| `corrections-review-2` | `runbook-corrections-2` | 14:27:00.728 (journal 726) | 14:27:48.088 (journal 728) | 14:27:48.168 |

Release receipts, as the ledger holds them (acceptance, release):
`cli-investigation` `sha256:87fffa12deebab439a57a75ada0e3ebf589b93407c25f2da594d3b479b8f3cb8`,
`sha256:52f8ef9e010c20a7162757d4c5df9ed434823796851c542cd0da8c132dfeec15`;
`driver-investigation` `sha256:2bd97a86c3e10160263fb140b4a8a97355fa7eeb9a5745511d84a737e96aece7`,
`sha256:e4ae6fe7a452b0a72f67fb334b522bc5b9d94a656f55836cd375f8029a3bc17e`;
`runbook-authoring` `sha256:93f112fcfba85247d156c57f73b8445bae0d10857f6f8ac2fb83d54748d505d2`,
`sha256:7a047cfa3f86afb2b400875a658e25997acf6ece773227be1599194195f8fc0e`.

Dependent work was given its predecessors' accepted results through the
authority path (D-TF-25, the fix PR 22 made after Pilot 1). Each
assignment records what it was given:

| Assignment | Given | From (sequence, accepted attempt) | Content |
|---|---|---|---|
| `runbook-authoring` | `cli-investigation.md` | `sq_79f613b7…`, `at_938aa259…` | `sha256:cf84125fd1d6bc333aa805a29f1a74e9b1326c5dc240b137269045f778040cdd` |
| `runbook-authoring` | `driver-investigation.md` | `sq_74342da3…`, `at_7316f84b…` | `sha256:7f2dd3a74dd8c9deba04b00e1c8c1f650bc8f0920c649a8b92ac418e6a03e420` |
| `runbook-review` | `runbook-authoring.md` | `sq_c0016eef…`, `at_21c27ad9…` | `sha256:9ff4b5abd352282ca91aa47ec8d01aae9a4706e340e08cafb77e398812a6f865` |
| `runbook-corrections` | `runbook-review.md` | `sq_33abd9d1…`, `at_64e96074…` | `sha256:bbd587877ed099377aa9a6d4dfd3c62d5f3f33db03d74993fb1fded866b87285` |
| `corrections-review` | `runbook-corrections.md` | `sq_00032bce…`, `at_e18fb2e6…` | `sha256:1d5eacf33b8c229abc80f9d18424c78510ac2ffcef91687ef498a091ee7fe5e4` |
| `runbook-corrections-2` | `corrections-review.md` | `sq_b07effc9…`, `at_6d651872…` | `sha256:744c9eaa93f8515386537373c7ac80484de4d08a79302d0cfae4edfce48bd5ac` |
| `corrections-review-2` | `runbook-corrections-2.md` | `sq_68155208…`, `at_ca8c0f69…` | `sha256:0ba08ae1d2e295e253230709cb1bbcdaafc2cc90f8ee52072a7e41b89069ffe7` |

A review also read the landed change itself: its worktree was created at
the revision the reviewed commit had just moved `main` to
(`runbook-review` at `e6c88ea0…`, `corrections-review` at `f53df432…`,
`corrections-review-2` at `5894d5e2…`).

## 13. Findings feedback into Engineer reasoning

Specialists reported 51 structured findings in this project. They were
admitted with the attempts that reported them, consumed by the Engineer's
next reasoning cycle, and changed the plan twice.

| Cycle | What had arrived | What the Engineer decided | Recorded reasoning |
|---|---|---|---|
| 64 | 26 findings from the two investigations | no new workstream; release the outside specialist for research | "the runbook-authoring task scope […] is broad enough to encompass every finding classified 'within' the requirement, and the specialist will receive both investigation results as .autobot/inputs/ files" |
| 67 | 5 findings from the builder | nothing to change; wait for the permit | "The five new findings from the builder are informational […] No plan adjustment is needed." |
| 71 | 8 findings from `runbook-review` | `DECOMPOSE_TASK`: `runbook-corrections` and `corrections-review` (`prop:f052b14621264bddedabfef99…`) | "the review (runbook-review) reported eight defects in RUNBOOK.md, including a high-significance error in §6 where the retry policy states max_retries=0 instead of the production default max_retries=2 […] The constraint mandates adjusting the plan before completing" |
| 78 | 7 findings from `corrections-review` | `DECOMPOSE_TASK`: `runbook-corrections-2` and `corrections-review-2` (`prop:600f49ea083c5f7b8ee298ee9…`) | "two medium-significance issues (budget validation semantics where PlanStore rejects negatives, and transport launch errors that escape as uncaught exceptions) and five low-significance issues […] This uses the last two contract slots" |
| 85 | 3 findings from `corrections-review-2` | `PROJECT_COMPLETE` | "three low-significance citation issues […] All three confirm the described behavior is correct — only source-line citations and a minor phrasing are imprecise." |

The path is the one the architecture describes: execution, a finding
admitted with a verified result, the planning view, the Engineer's
reasoning, an adjusted plan, more work. Two examples by identity:
`fnd:67f85fa03bae42b8daa6e2…` (the retry default, high) was reported by
the reviewer, consumed in cycle 71, and fixed by commit `f53df432…`;
`fnd:5814bdf27021abb3dfc47d…` (a transport launch error escapes as an
uncaught exception) was reported by the second reviewer, consumed in
cycle 78, and written into the runbook by commit `5894d5e2…`.

Some of what the specialists found is about Autobot itself and is still
open: section 25.

## 14. SOURCE_MODIFYING workstreams

Three workstreams changed the repository, each allowed to change exactly
one file, `docs/architecture/orchestration/RUNBOOK.md`. For each, the
specialist made the commit in its isolated worktree and reported it; the
Git adapter derived the candidate from Git and checked parent and scope;
the registered verifiers ran; your permit (`--authorize-publication`,
granted after a stable round) admitted one use; the EffectIntent moved
`refs/heads/main` by local compare-and-swap; the CommitReceipt was
admitted; the sequence was ACCEPTED and COMPLETED.

| | `runbook-authoring` | `runbook-corrections` | `runbook-corrections-2` |
|---|---|---|---|
| Frozen source revision | `5c96da356965960d722e8793a7c1a9694127d1e7` | `e6c88ea05c225231db894a5029290169969d4368` | `f53df432a9cb6990e5748c0d50cfa9e33a1f82d2` |
| Candidate receipt | `sha256:a063dc77427f5c7205fd901448b790848e553ec4ce24c2c9be4cd42df30f0ef4` | `sha256:8b7983b13dd5a06fe67ff23c5197c8d8a4edb57c19ac307c4d02516a4433ed38` | `sha256:21cb70c91acc9c90b2be40f6382ce24a3533415b252a94b10c80be461f97cc87` |
| Candidate tree | `d4b09f0711c9478a78c18d8c26525c3362fd6834` | `60a4b465d0ac3e55539ab9c157ede9b141bdd3fc` | `496bf97f9069f0c2768b420982915f5b786a537c` |
| Execution receipt | `sha256:deca73d92fb205f29c41bd764884913ac0fc7b848cf35360a5a2bc8da755241e` COMPLETE | `sha256:825623372ce10faada9ad07fbe59c939a6942b74db55fa9b27d5916405d881eb` COMPLETE | `sha256:997bb6501cb39b1546402cd03fca6b7e6c31defdadb430e64d36d67c7c7dcfd4` COMPLETE |
| Verification `a1` | `sha256:75511f7fadb0a8fd4dbf83fa296d5ff355435c70e8a00a929b1d3b16191e1894` | `sha256:96e275d673acc4c1dd964c295c9ede10b7ffafc231f099a74a2478eee087b913` | `sha256:43f46e4e7301a87ed460887a795448a92293813904cc0345ed47dd510818d28f` |
| Verification `a2` | `sha256:dd7dcfc3cd3b127404cdb5bd2ebccdc321a1023a6ae6a8d1fa8c9a7f063e862e` | `sha256:cf74bdab2a29ddc3e613f23040928b538b8270f8128283b09364f6a2f68b1f87` | `sha256:d600be677612341879118506331de1a89b01aea149e7e762181f54108520d637` |
| Verification `role-transform/builder` | `sha256:a95c5624549922b8b789d100b8bdaf3c2e4d94fef6d9d9863822489b59252e5e` | `sha256:f0f8b2a4dc06db7610a57dbf2ea1ba3b086a4e3d831da45f6c2549f351934fe0` | `sha256:dbba652c3d61de3e81f294342949647d937989f5f784065f40e5ec0d674037d2` |
| Permit granted (journal) | 14:06:46.611 (586), 1 use, consumed | 14:18:56.538 (651), 1 use, consumed | 14:27:00.557 (719), 1 use, consumed |
| Reservation | `rv_2284fe7d0795879394558b8f374b2f94` | `rv_c87a4cc46aeee07061e036096e1ef2c2` | `rv_0d4f1bdb541fa551e90a0873bc04a3e1` |
| EffectIntent | `in_fe1e3a255959e14b1a73c7c69eec57cf`, HAPPENED | `in_08fc4123a7f3e98d5070722cbd2bd811`, HAPPENED | `in_b9ad3e0d0e2bc88c317c120bd47fec33`, HAPPENED |
| Compare-and-swap on `refs/heads/main` | `5c96da35…` → `e6c88ea05c225231db894a5029290169969d4368` | `e6c88ea0…` → `f53df432a9cb6990e5748c0d50cfa9e33a1f82d2` | `f53df432…` → `5894d5e2e7af31016898b7916e655b3fb9c166a9` |
| CommitReceipt | `sha256:d739b8e88d89af74211e3571ed0c76c251ef5a0eece4a6059872f009d3fad41b` | `sha256:9a51408eb2e007c7c326f70685aefe6eaa66762ef1f977472cec257840ccc952` | `sha256:57c5a3f47ccff23890299d397f96af9426101c6c8bf019b0984cea1e8c30b7c8` |
| COMMITTING → COMMITTED → ACCEPTED (journal) | 588, 591, 592 | 653, 656, 657 | 721, 724, 725 |
| Supervision (requests allowed, denied) | Bash 2, Edit 3, Write 5; 0 denied | Edit 14, PowerShell 1, Write 2; 0 denied | Bash 1, Edit 11, Write 2; 0 denied |

The candidate stayed the specialist's. The commit authority published is
the commit the specialist created: each compare-and-swap's new value is
that commit id, and no commit was made on its behalf. The three commits
were later published to the remote unchanged (PR 23, section 1).

## 15. Independent review

Each change was reviewed by a specialist that did not write it, in its
own session, and the review's own result went through the same
verification as any work.

| Review | Reviewer | Reviewed work | Its author | Reviewer's call, session | Author's call, session |
|---|---|---|---|---|---|
| `runbook-review` | `claude-haiku` (`sp_b2f72881be89546de36037d84fea287d`) | `runbook-authoring` | `claude-opus` (`sp_17a8384fb1d2fe7756e5db8e7a7072e5`) | `call_f127d0d543351198a8177818037bf561`, `9e677e64-71d6-4329-90e8-a1de9d3757a7` | `call_227871f05b2ce70166fe8f3e444be97c`, `b5e78364-5e87-41d8-9378-6124db1bfc93` |
| `corrections-review` | `claude-haiku` | `runbook-corrections` | `claude-opus` | `call_6a4b2d5b6c5a734b3778ee9fc8bc704c`, `89046fd2-71ad-4acd-9acf-0a62957b554c` | `call_56d43efc866e9b4b00e1187148f06366`, `8dd6b43b-16f8-48d9-9f59-1768867c0ece` |
| `corrections-review-2` | `claude-haiku` | `runbook-corrections-2` | `claude-opus` | `call_34352d72a4bcfb79bc835b5c3eed4f07`, `d2568ff5-02cd-4fb6-8cf0-bea0b8087655` | `call_9e0c2f06be0395461df36afa33421624`, `7232dda6-57a9-45b9-a262-0328068184d0` |

The reviewer consumed the reviewed work through the authority path: the
accepted result of the reviewed attempt (section 12) and a worktree at
the revision the reviewed commit landed as. Review verification: `a1`,
`a2` and `role-transform/reviewer` PASS for all three
(`sha256:44fd9140aad37cfbdc8fdc24dd72d1e1e4d2fcd43247bbfae16cb13538117c2b`,
`sha256:c6a2ef4fffe5ea007778543be989d85b3ea7cee6da9068e0dfcd32b5691ededb`,
`sha256:3c2a06db1f9a3da9962869ee240ad6d1fbf4f8ad4fe31b9e26cb93b2b96aea5f`
for the first). The reviews were not ceremony: they found eight, then
seven, then three defects.

## 16. Project completion

`PROJECT_COMPLETE` (`prop:45de65fe74558f8fb1175ccdedea3be651a512e666521034b528ee788cbea9da`,
cycle 85) closed the project at 14:29:18.691Z, with all eight sequences
COMPLETED. The command ran four rounds, each to a stable quiet state (two
consecutive quiet steps with nothing in flight), granting one permit
after each of the first three:

| Round | Reported | Then |
|---|---|---|
| 1 | stable after 198 steps (174 waits) | permit for `sq_c0016eef57b0fcd266f27c147534abd3` |
| 2 | stable after 197 steps (166 waits) | permit for `sq_00032bce9c68528d6fa7de35158869ed` |
| 3 | stable after 195 steps (167 waits) | permit for `sq_6815520800bbe5c13821a032e94b1055` |
| 4 | stable after 53 steps (44 waits) | objectives still active: none; exit 0 |

The team was dissolved: all three participations are RELEASED
(`project_completed`) and all three memberships are ACTIVE.

## 17. Execution routing: the policy, and its preflight

Everything in sections 3 to 16 ran with each specialist pinned to a
Claude Code model: the catalog entries `claude-sonnet`, `claude-haiku` and
`claude-opus` were `claude-code` with the model aliases `sonnet`, `haiku`
and `opus`. (An alias is whatever the CLI resolves it to: the three
runbook commits of section 14 carry the trailer the `opus` session wrote,
"Co-Authored-By: Claude Opus 5.5".) That history is unchanged and is not
re-run.

On 2026-11-18 the organization's routing policy was declared (PR 24,
section 1) and production was switched to it.

| Who | Route | Provider's own identifiers |
|---|---|---|
| Engineer, and the Engineer as Project Manager | `[models] planner` | `claude-code`, model `claude-opus-4-6[1m]` |
| every specialist session: retained member, outside specialist, independent reviewer | `luna-6-max` (default) | AutoSwarm provider `codex`, model `gpt-6-luna`, reasoning effort `max` |
| escalated work | `opus-4-6-1m` (escalation) | `claude-code`, model `claude-opus-4-6[1m]` |
| specialist sessions while the default is switched off | `sonnet` (fallback) | `claude-code`, model alias `sonnet` |
| lightweight utility work | `haiku` (utility) | `claude-code`, model alias `haiku` |

"Luna 6 Max" was resolved from the live catalog, not assumed. `codex
debug models` (codex-cli 0.158.0-alpha.15.2) lists `gpt-6-luna`, display
name GPT-6-Luna, with reasoning efforts low, medium, high, xhigh and max;
no slug is literally "Luna 6 Max", and `max` is the top effort Luna has.
AutoSwarm 2.0.0.dev0 reports `codex` and `claude-code` available.

**The switch.** The production catalog (`config.local.toml`, not in Git)
no longer gives any specialist an agent or a model. The copy as it was is
kept in the evidence bundle (`db/config.local.before-routing.toml`). The
preflight command printed the catalog on both sides of the edit:

| Specialist | Before (`logs/cli-preflight.log`) | After (`logs/cli-preflight-2.log`) |
|---|---|---|
| `claude-sonnet` `sp_aa671ebf47f9b3d37540e0a5ee07256d` | its own: `claude-code` / `sonnet` | `luna-6-max`: `codex` / `gpt-6-luna` / `max` |
| `claude-haiku` `sp_b2f72881be89546de36037d84fea287d` | its own: `claude-code` / `haiku` | `luna-6-max`: `codex` / `gpt-6-luna` / `max` |
| `claude-opus` `sp_17a8384fb1d2fe7756e5db8e7a7072e5` | its own: `claude-code` / `opus` | `luna-6-max`: `codex` / `gpt-6-luna` / `max` |
| `opus` `sp_2f653905288b836bca70b5f75ba329ec` | its own: `claude-code` / `sonnet` | `luna-6-max`: `codex` / `gpt-6-luna` / `max` |
| `specialist-delta` `sp_5e24b9e318c0f20c9a86748a050243ee` | not in the catalog | `luna-6-max`: `codex` / `gpt-6-luna` / `max` |
| `specialist-epsilon` `sp_452860f878668e094d86dfcd1b0756c8` | not in the catalog | `luna-6-max`: `codex` / `gpt-6-luna` / `max` |

The four identities that existed before the switch are the same four
after it. The handles `claude-sonnet`, `claude-haiku` and `claude-opus`
no longer describe a route; they are kept because a specialist's handle
is its identity, and their memberships, seats and verified history hang
on it. The two new entries are outside specialists: available to
projects, members of nothing.

**The probe.** `preflight --probe` then exercised each route with a real
session before any engineering work was dispatched on it. Each specialist
probe ran in its own isolated worktree at `main`, on the READ_ONLY
contract production work is frozen on, with its requests answered by the
supervision policy.

| Probe | Route | AutoSwarm call | Native session | Launched with | Result |
|---|---|---|---|---|---|
| specialist | `luna-6-max` | `call_600a238a2e7443fc8562045e2cff010e` | `01a1efeb-1d33-7923-b497-6b913c9aaded` | model `gpt-6-luna`, effort `max` | OK, 362.1 s |
| independent reviewer | `luna-6-max` | `call_186c79e94416473383c754c80bd8cccc` | `01a1efeb-239c-75f2-ac1f-68dba2fccdfc` | model `gpt-6-luna`, effort `max` | OK, 100.7 s |
| escalation | `opus-4-6-1m` | `call_1f69460fa4bb4a9a88d8561e6da444f7` | `a0b07c53-08ce-4d49-9d17-1b99daae59bf` | model `claude-opus-4-6[1m]` | OK, 13.9 s |
| Engineer | — | — | `87b2a060-1d42-4c78-aed2-6cbf61e5698d` | served by `claude-opus-4-6[1m]` (the provider's own usage record) | OK, 4.6 s |

The specialist and the independent reviewer ran at the same time as two
calls and two sessions on the one route. The report is
`reports/preflight-probe.json`.

**What the route is like to work on.** A Luna session reads through a
shell, and on this machine every shell command of every Codex session
passes the Helios gate. The session completes the gate handshake from the
refusal text alone, and Helios keeps its decisions where the collector
already reads them. The cost is time: the two default-route probes needed
100 and 362 seconds to read one line of one file, where the Claude Code
session, which has file-reading tools, needed 14. Two earlier probes in
scratch worktrees (gates 129 and 131) fixed the session configuration:
inside the `workspace-write` sandbox a linked worktree cannot stage or
commit, because its index and objects are in the repository's Git
directory (F-TF-22); with requests relayed, `git add` and `git commit`
each raise one command request, which the supervision policy grants for
SOURCE_MODIFYING work and for nothing else.

## 18. Pilot 3: the route replaced under the same identities

The first engineering work on the new routes. Nothing from sections 3 to
16 was re-run or rewritten: this is a new objective for the same
organization, with the Taskforce exactly as Pilot 2 left it.

| | |
|---|---|
| Objective | `obj-pilot3-route-migration-001` — establish, from source, whether the new execution-routing and sandbox-supervision code can ever let a specialist session act outside what its frozen contract allows, or let a provider or model leak into an identity that must not depend on one |
| Project | `pj_372788162c37ef8f69a229beb0d54fbb` "Route-and-sandbox integrity audit", opened by the Project Manager (`OPEN_PROJECT`, `prop:457ccf2bb892b5335fcea7b66…`, cycle 86), 2026-11-18T18:35:34.518Z to 18:49:00.132Z |
| Source | `main` at `1732507c629d9608e6706987dc7bc86d5be175f4` |
| Run | one `engineer` process, exit 0, "stable after 293 steps (278 waits)" |
| Budget used | 4 contracts, 4 attempts, 2 outside-specialist assignments, 0 repairs, 2 retention decisions |

| Workstream | Sequence, record | Assignment: specialist, path, decided by | Attempt, AttemptContract | Call, native session | Ran on |
|---|---|---|---|---|---|
| `route-trace` research/investigator | `sq_0a79b2a94f7736c0ad1144295d851d28`, `sha256:72b774890a2686849981abd829402f2d526d2fe6f0f42c6609bfddd5499238bb` | `asg_950577f78a2f099966e88ebd139d5bdc`: `claude-sonnet`, RETAINED_SEAT, TASKFORCE | `at_ff6cadca7c88d0f8b959505317c114bd`, `sha256:95ae71afef946905d90f3c6e9cb0c5c3eda4567e8de86fa48e2e330202349d46` | `call_4ea95fed4e08d8dd7c5e6935b851d228`, `01a1eff2-d0aa-72b2-95fa-ab504542e6b0` | `codex` / `gpt-6-luna` / `max` |
| `sandbox-review` security_review/reviewer | `sq_dd22b433d60e7062891873da26614a03`, `sha256:6fb03643327defb2d411054808c70ab2e43a0aaceb2c5c87a826ea178272864e` | `asg_6a8c2c5d3d6c3350725b0b68a0660036`: `specialist-delta`, OUTSIDE_SPECIALIST, PROJECT_MANAGER | `at_bc7f10ab557cff5c767a132dd823b8f0`, `sha256:9c45adbdd0abb9d990bb6f1fd735fab6ae512d8c13fd568ef6bc2d2d8ae0372d` | `call_7071011184a1f65eee33ef6453c72e34`, `01a1eff2-dac9-70c0-8485-8b376cdb873a` | `codex` / `gpt-6-luna` / `max` |
| `identity-review` architecture/reviewer | `sq_1833dcec50248da05d0ce9f7954f3806`, `sha256:5bb5d4c223ece19dc9ac0f4813925716fb42ab2e80207641a107532c2fa49a2f` | `asg_4baa11edb6bf98f722fe4923baff5188`: `specialist-epsilon`, OUTSIDE_SPECIALIST, PROJECT_MANAGER, escalation `high_context` | `at_16eb4f7c0cc5672fffd015da28526583`, `sha256:c6ae0e837a55f671e2e706d0473500412359cf40cb793aca6cda313146f5d397` | `call_22464338956d9e4bbaf2e93273ce0460`, `31402c3b-6946-459b-80bf-ff4b19bd54b3` | `claude-code` / `claude-opus-4-6[1m]` |
| `argument-review` security_review/reviewer | `sq_3522eae36d57e221bc5c6c85eb374b1d`, `sha256:629dfce3b1523f31ae43e3978ca504ce53b222a577f88976c5e34531af43c081` | `asg_871d0e67036cba8a9535f5e695b423e6`: `specialist-delta`, RETAINED_SEAT, TASKFORCE | `at_017aa0ff5e037eae586845235e8a3426`, `sha256:8db4a62e722cce160f1eec76ade7d1bae95a18b9dd1f78e952e95456a656f913` | `call_a189632d237b6b84750cc4541272a7bc`, `01a1eff8-07ad-78b0-8d33-c960da78c31b` | `codex` / `gpt-6-luna` / `max` |

Every attempt was the first of its sequence and every one was VERIFIED:
`a1`, `a2` and the role-transform assertion PASS for all four (twelve
receipts, listed with the acceptance and release receipts in
`extracts/timeline-obj-pilot3-route-migration-001.txt`). The first three
attempts were RUNNING together from 18:37:14.635Z to 18:40:46.368Z, 211.7
seconds, as three sessions in three isolated worktrees.

How the fourth session, `argument-review`, read its source was found a day
later from its session record: through its provider's connector to the
remote repository, not from its worktree. What it read was the revision
its worktree was at, and its result stands as it was accepted; the channel
is F-TF-32 (section 21).

**The same member, another route.** `route-trace` named a capability and
a role. The Taskforce resolved the member that has held the
research/investigator seat since the founding run, and routing then sent
that member's session where the policy now sends every specialist
session:

| Assignment of `sp_aa671ebf47f9b3d37540e0a5ee07256d` | Selected (UTC) | Attempt | Call | Ran on |
|---|---|---|---|---|
| `driver-wait-behavior` (founding run) | 06:13:11 | `at_db2a041ebb600f107102f362acc04309` | `call_1d0dc028932ca141485101d2a694513f` | `claude-code` / `sonnet` |
| `policy-trace`, attempt 1 (Pilot 1) | 06:18:25 | `at_69fc5b024cdf0bcb66620da5722d240d` | `call_6f7125df62b7a4147a6e83dcd3bf5bbe` | `claude-code` / `sonnet` |
| `policy-trace`, attempt 2 (Pilot 1) | 13:24:36 | `at_4dff02b13777890439ae2b628f482aa8` | `call_4bff805dc08b53d790a7c6a038b4adee` | `claude-code` / `sonnet` |
| `cli-investigation` (Pilot 2) | 13:56:24 | `at_938aa259d19adc3398c53a34a892bd38` | `call_4a2d78a7af68a1e11dbefeb5b87e8d7e` | `claude-code` / `sonnet` |
| `route-trace` (Pilot 3) | 18:37:08 | `at_ff6cadca7c88d0f8b959505317c114bd` | `call_4ea95fed4e08d8dd7c5e6935b851d228` | `codex` / `gpt-6-luna` / `max` |

| Unchanged across the change of route | Value |
|---|---|
| Specialist identity | `sp_aa671ebf47f9b3d37540e0a5ee07256d` |
| Membership | `tm_3725eabebe5943956125cbd56e765f8a`, ACTIVE since 2026-11-18T06:13:52.725Z, by decision `dec:69fc58f0ecb3ab500d7389afbf480b0ef8dc5f4d1a2a5b8a72318e3dd96165a6` |
| Seat | `ts_e5fcdb796547f1982c18496e3e66260a`, research/investigator |
| Capability history | every earlier demonstration, as recorded; the new work added `dem:8a8cbe85d588f7fecf6da0d9c6d5d7e9550b92d4f93024447917fa3272f3a502`, QUALIFYING, to the same seat |

| New with the attempt | Value |
|---|---|
| AttemptContract | `sha256:95ae71afef946905d90f3c6e9cb0c5c3eda4567e8de86fa48e2e330202349d46` |
| ExecutionManifest | provider assignment `codex` / `gpt-6-luna`, persisted by AutoSwarm under the call |
| Call, native session | `call_4ea95fed4e08d8dd7c5e6935b851d228`, `01a1eff2-d0aa-72b2-95fa-ab504542e6b0` |
| Route recorded on the assignment | name `luna-6-max`, provider `codex`, model `gpt-6-luna`, effort `max`, served as `default` |

`reports/closure-route-proof.json` reads the route of every attempt from
the assignment and from AutoSwarm's own record of what it launched, and
carries the provider assignment of each persisted ExecutionManifest
beside them. Assignment and launch record agree for all 30 assignments
production has made (section 24). The other two members
of that morning made the same move in Pilot 4 (section 19):
`sp_b2f72881be89546de36037d84fea287d` after six assignments on
`claude-code` / `haiku`, and `sp_17a8384fb1d2fe7756e5db8e7a7072e5` after
four on `claude-code` / `opus`.

**An outside specialist on the default route, retained on evidence,
reused.** No member held security_review/reviewer. The Project Manager
selected an outside specialist (`ASSIGN_SPECIALIST`,
`prop:63a505547018c56e9cb2c9834…`), and the organization then did with it
what it does with anyone:

| Step | Evidence |
|---|---|
| Outside assignment, on Luna | `asg_6a8c2c5d3d6c3350725b0b68a0660036`, standing OUTSIDE, no seat; verified `a1` `sha256:91a876c3b0d46de650867509795c762a4c3b853e5b606000205407414d939adc`, `a2` `sha256:143dded0202ca257296eeae5df4429ba8b556bb4c40bc106abd29900cf8a6cbd`, `role-transform/reviewer` `sha256:54e013b78fe9891ea82f1178eebd71efb4c253947b4b0d2aa4991d7f5f82e829` |
| Demonstration | `dem:5f02414e7579620a85629a0646ec42e76d17f81053618231f0f4fc3081cc11aa`, QUALIFYING, recorded with standing OUTSIDE |
| Explicit retention | `dec:4ffbcd603269f8bbb9d62a885abc706d8901bb00b11be962ea127de14a490d66`, RETAIN, proposal `prop:67de75f79c575c0a0bdbea540…` (cycle 90), binding that demonstration |
| Membership, seat | `tm_1acdaee2aaef2fe765f131c16cd70de8`, ACTIVE since 18:41:50.549Z; `ts_9a3c2dbe5f33568f34944158353466a8`, security_review/reviewer, RETAINED |
| Reuse through the seat | `argument-review` named no one; the Taskforce resolved `asg_871d0e67036cba8a9535f5e695b423e6` (RETAINED_SEAT, standing RETAINED) 57 seconds after the retention; `dem:c8173cb4f4f2891c111b615d5e0bbee5578f193a803c77d59e391c542102388a`, QUALIFYING |

The Engineer's reason for the retention, as recorded: "three
high-confidence security issues in supervision.py, each with precise line
evidence, all verified and accepted. The organization needs security
review again immediately".

**Released without membership.** `specialist-epsilon`'s review was
verified and its demonstration qualifies
(`dem:b07f01302f3c3c968271efb2c8dcec1701bf87999dd78a2cfd6f22d6bbdd1000`).
The Project Manager released it all the same (`RELEASE_OUTSIDE`,
`dec:508055e83f2a3fb78f95b5c773fa3217c285eb5e94785502ab69d18979847038`,
cycle 89): no remaining work needed architecture review. It took part in
the project and is a member of nothing.

**Escalation, asked for in words about the need.** The Engineer was told
that `identity-review` spans seven modules and was left to decide whether
that needs more than standard execution depth. Its staffing decision
(`prop:dc6d659cf69f4cdc7495c27b0…`) asked for `high_context`, with the
reason: "The workstream requires holding seven modules in view
simultaneously, warranting high_context escalation." Routing resolved
that word to the escalation route, and the assignment records it: route
`opus-4-6-1m`, `claude-code` / `claude-opus-4-6[1m]`, served as
`escalation`. The other three assignments stayed on the default route.
The Engineer is not shown routes (D-TF-22), and its recorded reasoning
for this project, quoted in
`extracts/obj-pilot3-route-migration-001.txt`, names no provider and no
model.

**What the work found.** Ten verified findings, admitted with the
attempts that reported them and consumed by the Engineer's next cycles
(89, 90, 93, 94):

| Finding | Class, significance | Statement | Now |
|---|---|---|---|
| `fnd:e244b20c8f47d57fa586ec…` | security_issue, high | the sandbox-edge rule granted path-capable Git commands without looking at their paths | fixed: section 20 |
| `fnd:24503a988b444569e37938…` | security_issue, high | the rule read a command by its first two words: a second command chained after `git status` would have been approved, and so would any program named `git` | fixed: section 20 |
| `fnd:d035cef1a5baa357f5ea20…` | security_issue, high | workspace containment held when either spelling of a path matched, so a link inside the workspace that leads out of it counted as inside | fixed: section 20 |
| `fnd:50c4d3654fb8d44fd22393…` | implementation_defect, high | a stop between dispatch and the slot binding, followed by a routing change, relaunches on a route that was never the recorded one | fixed: PR 26 (F-TF-28, sections 21 and 25) |
| `fnd:d17406f9d5e714fcf7c859…` | security_issue, medium | a relaunch reads the route's session arguments from the live policy | fixed: PR 26 (F-TF-28) |
| `fnd:1b4b53755757500f087b40…` | security_issue, medium | staged session arguments are keyed by call id alone | fixed: PR 26 (F-TF-28) |
| `fnd:451997b7ff9c1bedbd5330…` | implementation_defect, low | a placeholder that is not lower-case letters in braces passes validation and stays literal | fixed: PR 26 (F-TF-28) |
| `fnd:8e551532a412c8c1abc732…`, `fnd:08042f7662c2379476ff13…` | verification_gap, low | no provider, model or route reaches a TaskContractRecord, a membership, a seat or a demonstration; the assignment row carries the route as execution state and its identity does not | confirms sections 23 and 24 |
| `fnd:bc96fcc6835867944d13e5…` | verification_gap, low | a specialist registered with no handle gets one made from its provider and model, so its identity is then derived from them | open; every production specialist has a handle |

The first three are about the permission code this proof's own change set
had introduced (PR 24). The organization's reviewers found them on the
first day that code was in production. Section 20 is what was done about
it.

**Completion.** `PROJECT_COMPLETE`
(`prop:f97b4062565d1da6086aeffd6055d4affb8cc7ee2ff6a7e9b400bbe8ca3b7afe`,
cycle 94) at 18:49:00.132Z, all four sequences COMPLETED. The Engineer's
words: "The six unaddressed findings […] are the audit's deliverables:
they document the attack surface the objective asked to establish, and
remediating them is work for a future objective." The team was dissolved;
the two members' memberships are ACTIVE.

## 19. Pilot 4: a multi-workstream project on the default route

Every specialist session of this project ran on Luna 6 Max: two
concurrent investigations, a SOURCE_MODIFYING builder that created its
own candidate commit from inside the sandbox, and an independent review.
The work was real: the routing change had moved the lines RUNBOOK.md
cites (F-TF-25).

| | |
|---|---|
| Objective | `obj-pilot4-runbook-refresh-001` — bring `docs/architecture/orchestration/RUNBOOK.md` back in step with the source, and cover the `preflight` command and what the routing policy means for an operator |
| Project | `pj_0dea84d80f0b59c91ec0b893de5dc2c3` "RUNBOOK reference refresh", opened by the Project Manager (`prop:c8fba922df89ed39eb08e40ad…`, cycle 95), 2026-11-18T18:50:48.360Z to 20:17:06.789Z |
| Source | `main` at `1732507c629d9608e6706987dc7bc86d5be175f4` |
| Run | one `engineer --authorize-publication` process, exit 0 |
| Budget used | 4 contracts, 4 attempts, 1 outside-specialist assignment, 0 repairs, 1 retention decision |

The Engineer laid the four workstreams out in one decision
(`DECOMPOSE_TASK`, `prop:fdc194bd923d2547d208cfc2b…`, cycle 95) and, as
Project Manager, staffed the one the Taskforce could not.

| Workstream | Class | Sequence, record | Assignment: specialist, path, decided by | Attempt, AttemptContract | Call, native session |
|---|---|---|---|---|---|
| `references-cli` | READ_ONLY research/investigator | `sq_2d5f516ff08e63df7de8e536e51a57cb`, `sha256:3936400a683a6ca51930242d401313239f765cb3a17ca6054045d3024ae0bb51` | `asg_ad57120b8096ab28f27c993191774e6e`: `claude-sonnet`, RETAINED_SEAT, TASKFORCE | `at_ef4f402ef668de57dbb52f40e4ec9779`, `sha256:4bb534f0302893aa128305c42a3390d0caa8473321b4a7eda87b58d2371a34cf` | `call_c67c85db39b3a5397a19ea519569876b`, `01a1f001-980e-75b0-9648-a4dfd2d0bec9` |
| `references-runtime` | READ_ONLY research/investigator | `sq_e4c4cbb7b04c7c8f8d09cc8e4d90fbd2`, `sha256:fd09c5ee52a66230a718181faa6d350f5d087a55bd8fea3fafe8490d9fa983f0` | `asg_89755d62fa4f74d8405dd8adc86ae978`: `specialist-delta`, OUTSIDE_SPECIALIST, PROJECT_MANAGER | `at_688ac2b4738616823e6222cfdfb341a1`, `sha256:d2395df1f95015f71ba38f363c4b6d83df791fc80447a2917c6fa529d05732d0` | `call_fd043bdd791931e8fd10e15c15c3d50a`, `01a1f001-9cf1-78e1-925d-2279b959c8a1` |
| `runbook-refresh` | SOURCE_MODIFYING documentation/builder | `sq_f42a9a651d6a8a3e4aa51fbd02ee8a63`, `sha256:1f5464cbe05c02cca5bd12af320a259e0dae8a18505c405b246b5b9b98e6bafe` | `asg_18164d04d874a3d08c151774118f1f00`: `claude-opus`, RETAINED_SEAT, TASKFORCE | `at_d502fd2b515b757ca64d4eebc5a79a78`, `sha256:7dba99ff2ee529c1eae590d7d76ca8bb94974320403b1ea787a5eadbda0b01d9` | `call_43514ac8729b53c3102727c3835433e4`, `01a1f01a-6a20-7ff3-a6ff-2588ab798503` |
| `refresh-review` | READ_ONLY verification/reviewer | `sq_03b3992322246395d292a415244856e3`, `sha256:2054d7b270c51abbb4b154bf8f18baddf6c7ddf9e7de5e6759720558441bd77e` | `asg_d0dcb9394c67eead03706c300c19917c`: `claude-haiku`, RETAINED_SEAT, TASKFORCE | `at_66914214b93ad048da7ab7f7155c0ca4`, `sha256:33a66b328d73d20870f57e2b4ab97ca98549d945b0384e008caa4fdf690c9979` | `call_7eb58d5c27a4b250ffdbfc4a3e975805`, `01a1f039-fc6a-7130-b1e0-de90f8a3b05d` |

All four ran on `codex` / `gpt-6-luna` / `max` (route `luna-6-max`,
served as `default`), each as the first attempt of its sequence, and all
four were VERIFIED: `a1`, `a2` and the role-transform assertion PASS
(twelve receipts, in `extracts/timeline-obj-pilot4-runbook-refresh-001.txt`).
Three of the four are the members retained earlier that day on Claude
Code models, now executing on Luna under the identities, memberships and
seats they already had (section 18).

**Both staffing paths, and a member staffed as an outsider.** The
Taskforce holds one research/investigator seat and the two investigations
had to run at the same time. The Project Manager selected
`specialist-delta` for the second (`ASSIGN_SPECIALIST`,
`prop:e62f0708dc4448114c0b45cfb…`). That specialist is a Taskforce member,
for security review; for research it holds no seat, so it joined this
project as an outside specialist (D-TF-23) and its demonstration
(`dem:6a329c445571f8c3630cdf51dca1223472c5cd3621c679b53e93cbd12a37c30f`,
QUALIFYING, standing OUTSIDE) earned it nothing by itself. The Project
Manager then decided, explicitly, not to retain it for research
(`RELEASE_OUTSIDE`,
`dec:8f80d4e5bd3e327872b0a2e96f96cc96c921a7c0e4769bada25ee977ac83d0b7`,
cycle 99): "The research/investigator capability is already covered by
the retained seat […] The concurrent investigation that required a second
investigator was a one-time need". Its membership and its security-review
seat are untouched.

**Concurrent execution.** From the authority journal:

| Sequence | Transition | Journal | Time (UTC) |
|---|---|---|---|
| `refresh-review` | DECLARED → BLOCKED (unsatisfied `review-of`) | 893 | 18:53:17.352 |
| `references-cli` | DECLARED → READY | 894 | 18:53:17.362 |
| `references-runtime` | DECLARED → READY | 895 | 18:53:17.370 |
| `runbook-refresh` | DECLARED → BLOCKED (unsatisfied `on-references-cli`, `on-references-runtime`) | 896 | 18:53:17.378 |
| `references-cli` attempt | DISPATCHED → RUNNING | 904 | 18:53:19.711 |
| `references-runtime` attempt | DISPATCHED → RUNNING | 906 | 18:53:21.780 |
| `references-cli` attempt | RUNNING → FINISHED | 935 | 19:06:06.653 |
| `references-runtime` attempt | RUNNING → FINISHED | 998 | 19:19:11.490 |

Both attempts were RUNNING from 18:53:21.780 to 19:06:06.653, 12 minutes
45 seconds, as two Luna sessions in two isolated worktrees, while the two
sequences that depend on them stayed BLOCKED.

**Dependency release.**

| Dependent | Waited for | Predecessor released (t-s41) | Dependent BLOCKED → READY (t-s02) | Started (t-s06) |
|---|---|---|---|---|
| `runbook-refresh` | `references-cli` and `references-runtime` | 19:06:07.437 (journal 950) and 19:19:12.409 (journal 1013) | 19:20:24.062 (journal 1015) | 19:20:24.139 |
| `refresh-review` | `runbook-refresh` | 19:54:12.470 (journal 1107) | 19:54:53.153 (journal 1109) | 19:54:53.234 |

The builder was given both accepted investigation results through the
authority path (D-TF-25): `references-cli.md`
(`sha256:1cb13fd7da71539893f21947c5010a1e1d85c22d32f3003efdfd8dc612b4d17b`,
from `at_ef4f402e…`) and `references-runtime.md`
(`sha256:8da038a4006699f8aa38e8b29f53cabe5d83c0067bed590275f2a9e20243dc55`,
from `at_688ac2b4…`). The reviewer was given the builder's accepted
result (`runbook-refresh.md`,
`sha256:9c67161ab206fd172c916aca51b2ca65819276c0058e841cedb65a65f58fb31f`)
and a worktree at the revision the reviewed commit had just moved `main`
to, `606663ad…`.

**Findings into the Engineer's reasoning.** Ten findings, each admitted
with a verified result and consumed by the next cycle:

| Cycle | What had arrived | What the Engineer decided | Recorded reasoning |
|---|---|---|---|
| 98, 99 | 7 findings from the two investigations: five listing every stale reference with its old and new line numbers, two saying the runbook lacks the `preflight` command and the routing policy | no new workstream; release the outside investigator | "their findings confirm exactly the scope the runbook-refresh workstream already covers […] No plan adjustment is needed." |
| 102 | 2 findings from the builder | nothing to change; wait for the permit | "all stale line references were updated and the preflight/routing sections were added, which is exactly what the investigations called for" |
| 106 | 1 finding from the reviewer (below) | `PROJECT_COMPLETE` | "The review's verification_gap finding […] concerns the review's own input tree composition — it is not a defect in the document." |

The Engineer had been told to add a corrections workstream if a review
reported a defect in the document, as it did twice in Pilot 2 (section
13). Here the review reported none, and it did not.

**The SOURCE_MODIFYING workstream.** `runbook-refresh` was allowed to
change exactly one file. The specialist's session ran in the
`workspace-write` sandbox of its route. It edited the file there, and
twice asked to leave the sandbox, because a linked worktree's index and
objects are in the repository's Git directory (F-TF-22):

| Request (AutoSwarm) | Decided (UTC) | Command | Answer of the supervision policy |
|---|---|---|---|
| `approval_8bba201709805e328a7afdc785e70c8b` | 19:46:14.225 | `git add docs/architecture/orchestration/RUNBOOK.md` | allow |
| `approval_7a2bfbe09d135c90b88e2a4d7efe654a` | 19:47:23.034 | `git commit -m "docs: update orchestration runbook"` | allow |

Nothing else was asked, and nothing was denied. The policy that answered
was the one PR 24 had merged; section 20 is about that policy.

| | `runbook-refresh` |
|---|---|
| Frozen source revision | `1732507c629d9608e6706987dc7bc86d5be175f4` |
| Candidate commit, made by the specialist | `606663ade00219a4b2c1baef0df0aa81dc510067`, parent `1732507c…`, "docs: update orchestration runbook", one file, +72 / −58 |
| Candidate receipt | `sha256:29e6c5607ccde2b172e3ac10df23b52c8173acc71492ff69d27fe7bba8aefd6a` |
| Candidate tree | `4acf8bb481724689b2998b97d3470bce0c039180` |
| Execution receipt | `sha256:2e9a98d2927e8b267f9aa279b182c4e656fedde0e0dd3bdca3de4fbd556127e5` COMPLETE |
| Verification | `a1` `sha256:21cffe155a776c6ba5b4ad050b2279c25f3b784ea8345f397bf20084f7ca7a80`, `a2` `sha256:798240fac3c062100de48c78b21441363d6ad2c3f7ec660c2e5810773ebecf73`, `role-transform/builder` `sha256:27646ba8c0a13ae7247f6c89267d1e032a3c3f646aae60c77eb5f702f3bb34dc`, all PASS |
| Stable round, then the permit | "stable after 1744 steps (1727 waits)"; permit granted 19:54:12.246 (journal 1100), 1 use, consumed |
| Reservation | `rv_bef89272ebd8fd0217ed600ec7268981` (journal 1101, consumed 1104) |
| EffectIntent | `in_2340524d98605ee01323e79400f4d067`, HAPPENED |
| Compare-and-swap on `refs/heads/main` | `1732507c629d9608e6706987dc7bc86d5be175f4` → `606663ade00219a4b2c1baef0df0aa81dc510067` |
| CommitReceipt | `sha256:8eae348ae2f2f3a9d103fce7fd822b5e840c60fce4d2ce071b0b5706d3f59a79` (expected, new and observed values as above) |
| VERIFIED → COMMITTING → COMMITTED → ACCEPTED (journal) | 1102, 1105, 1106; acceptance `sha256:82f20cfb5f89220682ddea0efec3c3652255223bee1ae19b4af517416b025dfa` |
| Completed | 19:54:12.586 (journal 1108) |

The commit authority published is the commit the specialist created. It
was published to the remote unchanged (section 1).

**Independent review.**

| | Reviewer | Author |
|---|---|---|
| Specialist | `sp_b2f72881be89546de36037d84fea287d` (`claude-haiku`), retained verification/reviewer seat `ts_bddb43b39c785f99430327912e364331` | `sp_17a8384fb1d2fe7756e5db8e7a7072e5` (`claude-opus`), retained documentation/builder seat `ts_c31fff88a7cf30af7a2f73740370f927` |
| Call | `call_7eb58d5c27a4b250ffdbfc4a3e975805` | `call_43514ac8729b53c3102727c3835433e4` |
| Native session | `01a1f039-fc6a-7130-b1e0-de90f8a3b05d` | `01a1f01a-6a20-7ff3-a6ff-2588ab798503` |
| Route | `codex` / `gpt-6-luna` / `max` | `codex` / `gpt-6-luna` / `max` |

Two specialists, two calls, two sessions, one route. The review's own
result went through the same verification as any work (`a1`
`sha256:0fc05b48bd0bfc353b7ba9691c9f2d30206f84a57ad0fa8856fbc03c029aa079`,
`a2` `sha256:c33b4673d50d7762ddff62e3119a7fe78d488ed94ffc1f2a85ac553fc41bb986`,
`role-transform/reviewer`
`sha256:b59006263a5b57febf2e76cbd99126869d9217500d61fe7c04872d06977bbd0b`).

What the review established is narrower than what was asked of it, and
this document does not claim more for it. The reviewer checked the two
new sections against `cli.py`, `runtime.py`, `preflight.py` and
`routing.py`, spot-checked updated references in `cli.py`, `planstore.py`
and `autoswarm_transport.py`, and compared the runbook with its earlier
text. It then reported (`fnd:b0f182d0e32919c7ac610f…`, verification_gap,
medium) that it could not confirm every reference the investigations had
flagged: it had been given the builder's result and not the
investigations', and it did not recognize its own worktree as the landed
change. Its result opens with "full acceptance is not yet verifiable".
The Engineer read that as a statement about the review's inputs, not
about the document, and completed the project. That gap in what a
reviewer is given is F-TF-29, fixed afterwards by PR 26 (section 21).
This review stays what it was.

So the exhaustive check was made outside the organization, mechanically,
and is recorded here as exactly that. Each of the 104 `file:line`
references the runbook carried before the refresh was taken through the
line shifts of `git diff --unified=0` between the revision the references
were verified at (`5c96da3`) and the revision the refresh was written
against (`1732507`), and the result was compared, as a multiset per file,
with the references the published runbook carries:

| | |
|---|---|
| References before the refresh | 104, to 10 source files |
| Expected to move, and moved correctly | 75 |
| Expected to stay, and stayed | 29 |
| Expected by the line shifts and absent from the published runbook | 0 |
| References in the published runbook that no earlier one accounts for | 16, all in the two new sections (runbook lines 177 to 181 and 294 to 296) |

(`extracts/pilot4-facts.txt`.) The refresh is correct for every reference
that existed. F-TF-25 is closed.

**Completion.** `PROJECT_COMPLETE`
(`prop:6811947d3176d2afc28d552acf5d5f533fff17d5f9fb0ecb5bf700d88efa9d3c`,
cycle 106) at 20:17:06.789Z with all four sequences COMPLETED: two rounds
to a stable quiet state (1744 steps with 1727 waits, then 667 with 658),
one permit between them. The team was dissolved and all four specialists
are members.

**What the route costs.** The project took 86 minutes for four sessions:
13 and 26 minutes for the investigations, 33 for the builder, 22 for the
review. Pilot 2's whole project, eight sessions on Claude Code models,
took 37 minutes. Almost all of the difference is section 17's: a Luna
session
reads through a shell, and on this machine every shell command of every
Codex session completes a Helios gate handshake first.

## 20. The sandbox edge: found by the organization, fixed, run again

Pilot 3 asked a security reviewer whether the new permission rule for
sandboxed providers could allow a request that a frozen contract does not
grant. The rule was part of this proof's own change set (PR 24), and the
answer was yes.

**What was reported, and whether it was true.** Three findings of high
significance from `sandbox-review` (section 18). Each was checked against
the source before anything was changed, and each held:

| Finding | The rule as PR 24 wrote it | Consequence |
|---|---|---|
| `fnd:24503a988b444569e37938…` | read the provider's description of a command and split it on blanks; the first word had to be named `git`, the second had to be one of seven subcommands | a script that chained anything after `git status` was approved to run outside the sandbox; so was any program named `git` |
| `fnd:e244b20c8f47d57fa586ec…` | granted `rm`, `mv`, `reset` and `restore` without looking at their paths | the frozen scope did not bound what those commands touched |
| `fnd:d035cef1a5baa357f5ea20…` | asked whether the working directory was inside the workspace under either spelling | a link created inside the worktree that leads out of it passed, and the approved command would have run where it led |

The third has the root cause Pilot 1's specialists had reported for file
reads and writes (`_relative_inside`, section 25), which had been left
open.

**What production had actually asked.** Two requests in total, both in
Pilot 4, both the commands the frozen contract tells a builder to run:
`git add docs/architecture/orchestration/RUNBOOK.md` and
`git commit -m "docs: update orchestration runbook"` (section 19). The
rule as first written was the production rule from 18:25Z, when PR 24
merged, to 20:59Z, when the checkout moved to the merge of PR 25, and no
request used the gap. The two requests are now test fixtures, as
AutoSwarm recorded them, and the narrowed rule still grants both.

**The rule now** (PR 25, [ARCHITECTURE.md](ARCHITECTURE.md) section 6.3,
D-TF-28 as narrowed, D-TF-30):

- A location is where it resolves. A path is inside a root when its
  resolved location lies inside the root's resolved location: for reads,
  writes, gate files and the sandbox edge alike.
- Two commands may leave a sandbox and nothing else:
  `git add [--] <path in the frozen scope>...` and
  `git commit -m <message>`.
- The command that would run is judged, not a description of it. The
  request must be a shell the operating system supplies, started to run
  exactly one script, described by the provider as that same script; the
  script must be one plain command, read with the shell's own quoting
  rules, with no operator, substitution, variable, wildcard or escaped
  quote; `git` must be written as `git`; every path must be a plain path
  inside the frozen scope; the command must run from the root of the
  assignment's worktree; and that worktree's `.git` must still be the
  pointer into the repository's own worktree registry.
- A refusal says which of these did not hold.

| | |
|---|---|
| Commit | `b50fb746e954298bc46a3a378a37a004e56203b1`, sole parent `606663ad…` (Pilot 4's accepted commit), tree `91c9ab42850e01002b388a8de30243149a6db2d9`, 4 files, +691 / −117 |
| Pull request | dimascior/Autobot#25, "Supervise the sandbox edge by the command that would run": 2 commits, 5 files, +763 / −175 |
| Merge | `d01929ff18d066e9566f729f14c7de87bfb14233`, parents `1732507c…` and `b50fb746…`, the same tree |
| Tests | 13 pin the sandbox edge (6 before, rewritten), 1 new test pins resolved locations with real directory junctions; 6 mutations of the rule are each killed (32, 33, 35 to 38) |
| Regression on those bytes | Python 3.8.10: 747 passed. Python 3.12.11: 746 tests, OK. Legacy coordinator: 41 tests, OK. Mutations: 38 of 38 killed |

What a rule that decides before a command runs cannot close is recorded
with F-TF-26: the checks are made at decision time and the command runs
afterwards; Git reads the repository's own configuration and hooks, which
are trusted as the operator's; and inside the sandbox a session can still
write any file of its worktree, in scope or not, which the candidate
scope check refuses afterwards.

**Run again: Pilot 5.** A rule that had just been rewritten was not left
to its tests. A fifth objective ran a SOURCE_MODIFYING builder on the
default route under the narrowed rule, on merged `main`, and asked for
the change to be independently reviewed.

| | |
|---|---|
| Objective | `obj-pilot5-sandbox-edge-001`: add a subsection to RUNBOOK.md that tells an operator what the sandbox edge allows, from source, and have it independently reviewed |
| Run | 2026-11-18T21:00:32Z to 21:29:31Z, exit code 0, on merged `main` `d01929ff…` (PR 25). Budget used: 2 contracts, 2 attempts |
| Project | `pj_2850006f1ea8dfa1bfe3881ab3c5bc56`, "Sandbox edge runbook note", COMPLETED |
| Builder workstream | `sandbox-edge-note`, `ws_62948e99fac41db2dd5800f586b395f0`, sequence `sq_ff709a6a85cee21328a10c8ad812aafa`, SOURCE_MODIFYING, scope `docs/architecture/orchestration/RUNBOOK.md` |
| TaskContractRecord | `sha256:7dd10b863a9236e34e2b4321f9a685b898961b9a6c0b2e700a397c838c9292db`, contract digest `sha256:b1adc5618cd9d21a383f1409359a5f3f29305e5ecb9363a81d0273686697c0db` |
| Assignment | `asg_288444eeff48894b67454eeac12f6c50`: specialist `sp_17a8384fb1d2fe7756e5db8e7a7072e5`, resolved by the Taskforce from retained seat `ts_c31fff88a7cf30af7a2f73740370f927` |
| Attempt, call, session | `at_8b62667b86a4922ed93022e48f661b17`, `call_50e928e6276bb1a7db9b68389ba56fbb`, Codex session `01a1f076-f7b1-7931-b901-b9267ea6aab8` |
| Route recorded and launched | `luna-6-max`: provider `codex`, model `gpt-6-luna`, effort `max`, served as the default |

The builder's session asked to leave its sandbox three times. Each
request and each answer is a row in `assignment_approvals`:

| Time (UTC) | Request | Answer | Why |
|---|---|---|---|
| 21:06:07.958 | a file change: a gate file addressed to the command-gate directory itself, not to its `pending` folder (`approval_1e7e4ea1ee915a7db928823b8c04d7f0`) | deny | "the sandbox already holds every location this assignment may write (its worktree and the command-gate pending directory); a write it refused is outside the assignment" |
| 21:14:45.768 | `git add docs/architecture/orchestration/RUNBOOK.md` (`approval_c7060d65086350b2a287f5a5af03f00a`) | allow | "git add for the assignment's own worktree, inside the frozen scope; Helios governs the command itself (cap-20)" |
| 21:15:38.475 | `git commit -m "docs: add subsection 5.5 The sandbox edge to RUNBOOK.md"` (`approval_745d418c9c195881a1046edd64a73092`) | allow | "git commit for the assignment's own worktree, inside the frozen scope; Helios governs the command itself (cap-20)" |

The session corrected the address of its gate file after the refusal and
went on. Nothing else was asked for and nothing else was granted.

| Authority, in journal order | Identity |
|---|---|
| Candidate frozen (t-s07, 1228) | CandidateReceipt `sha256:e86b3ce8d7be5834419858357d916d214251b87ba3da69702739b8b6f4450d52`, tree `488dc5d0be721a14a2ab322437201d96f81b066d` |
| Execution receipt (1229) | `sha256:8fc04e5360e767194bc3ae42b5b8169ac246e6002bf4ac4bbcd14f6f9f7b803b`, COMPLETE |
| Verification (1233 to 1235), all PASS | role-transform/builder `sha256:790ddfd53cda9b8ab6ae516a4cff326ca1a06719bfafc5775c3344340d4b9ba8`; a1 `sha256:d65ead9152cd39fa061f779f6ade0e01381ff9317b7764d800f5251a1d25b13c`; a2 `sha256:fda34e85057e73ec679240d2fd03a74144bd4438fccd2678f146976dc9138f11` |
| VERIFIED (t-s10, 1237) | |
| Standing permit granted (1238, 21:19:18.631Z) | one use, by the operator's `--authorize-publication` |
| Reservation (1239), COMMITTING (t-s15a, 1240) | `rv_92dac3f46d7c6cbd45128ce397bec1c7`; intent `in_137b6f0e44e058c7ed24e3e0d3d0393b`, effect `eff-local-canonical-cas`, expected old `d01929ff18d066e9566f729f14c7de87bfb14233` |
| COMMITTED, point of no return (t-s16, 1243) | CommitReceipt `sha256:c6811cb44c02f47eff89697e209f90b2155bbee947ecd25a00b2dd9ef2cb1d55`: `refs/heads/main` observed at `4abc57c249695748a3f5e56054e3eb82577fc1cc` |
| ACCEPTED (t-s20, 1244) | AcceptanceRecord `sha256:a85de04550f6384d3a49ed8ea22f728ce29dc2875515916a7c1a92d922dddbbd`, 3 verification rows |
| Dependencies released, COMPLETED (t-s41, t-s42, 1245, 1246) | release `sha256:08edea4ab30e3a46927bbd63fe455c4def54204d3d3239cd5f94331d45f47be9`, successor `sq_9bb47f8bbcbfef59db1b01cb9b57c0b9` |

The published commit `4abc57c249695748a3f5e56054e3eb82577fc1cc` has the
one parent `d01929ff…`, changes the one authorized file by +8 lines, and
is the specialist's own commit: authority moved `refs/heads/main` to it by
compare-and-swap and wrote nothing itself. It reached the remote
unchanged, as the first commit of PR 26 (section 21).

**The text itself, checked directly.** Subsection 5.5 makes nine
citations (`supervision.py:91-97`, `98-100`, `294-347`, `187-240`,
`243-291`, `350-374`, `403-428`, `377-440`, `taskforce.py:768-785`). Each
was read against the source at `4abc57c2` by the operator session,
separately from any review: every citation points at the code its
sentence describes, and every statement of the subsection holds. This
check is not a review by the organization and is not counted as one.

**The review of it did not happen.** The project's second workstream was
an independent review of this change. It was accepted by authority and it
had examined nothing. Section 21 is about that.

## 21. Independent review: one that examined nothing, why, the fix, and what the confirming runs showed

Pilot 5's project was completed on an independent review that had
reviewed nothing. This section keeps that record as it is, says exactly
what happened in it, what was changed, and what later production runs
then showed. Pilot 5 does not establish independent review. Pilot 6
does. Its session records, and those of the run after it, each showed
one more thing a provider offers a session that no assignment grants;
the rest of this section is those two findings, what was changed for
each, and the production run that confirmed each change.

### What Pilot 5's review was

| | |
|---|---|
| Workstream | `sandbox-edge-review`, `ws_443f57b597e87ff81c5f0946503b2669`, sequence `sq_9bb47f8bbcbfef59db1b01cb9b57c0b9`, READ_ONLY, an independent review of `sandbox-edge-note` (section 20) |
| TaskContractRecord | `sha256:12e6b24740c3eef86c8bf4b0d8f117fb635bf1cf9cbfa9da737441b9f0e671ed`, contract digest `sha256:39d97960dd880c460052b53dde7b48d30a2b867a6c1ae9406bae479057b2bdca` |
| Assignment | `asg_bdaaab8e1d9778f375f730152cfe3804`: specialist `sp_b2f72881be89546de36037d84fea287d`, resolved by the Taskforce from retained seat `ts_bddb43b39c785f99430327912e364331` |
| Attempt, AttemptContract | `at_197bbe11ae746f26d358abc00a8047d2`, `sha256:b1fdd8b12f358609bed59dddfe264543e03268913b6ea1583a46b3925aab7092` |
| Call, manifest, session | `call_3128adedd4dc2ec162fc29491690c373`, ExecutionManifest `sha256:094489019c88aef1213ad0d75aa668f31e98f1eea23f7a9d24436af51c7db0fd`, Codex session `01a1f087-d171-7a23-a9ed-25d7226a80f4` |
| Route recorded and launched | `luna-6-max`: `codex`, `gpt-6-luna`, effort `max` |
| Executed | RUNNING 2026-11-18T21:19:57.049Z, FINISHED 21:29:30.291Z |

**It was provisioned correctly, and it was independent.**

| Question | Answer, from the records |
|---|---|
| Its own assignment, AttemptContract, call and session? | Yes: every identity above differs from the builder's (`asg_288444ee…`, `at_8b62667b…`, `call_50e928e6…`, session `01a1f076-…`), and its specialist is another specialist (`sp_b2f72881…`, not `sp_17a8384f…`). The assignment records `independent_of` the builder |
| The accepted result it was to review? | Staged in its worktree as `.autobot/inputs/sandbox-edge-note.md`, the bytes of `sha256:df6b7333be6da438e2358da29b750779f58fa5b67be919b56ecd481ad93998f2`: the builder's accepted artifact (journal 1225), from the content store, not from the builder's workspace |
| The source? | Its worktree `autobot-wt-ymforolh` was a checkout at `4abc57c249695748a3f5e56054e3eb82577fc1cc`, the published commit: the changed file and the source it cites were both in it |
| Readable? | Yes. Its first command read the diff of the change. Two probe sessions later read the directory, a tracked file, a staged input and Git history through the same route (below) |

**What its session did**, from the provider's own session record
(`rollout-2026-11-18T16-19-55-01a1f087-d171-7a23-a9ed-25d7226a80f4.jsonl`):

| Time (UTC) | Command | Shell the session chose | What happened |
|---|---|---|---|
| 21:20:15 | `git diff HEAD~1 HEAD -- docs/architecture/orchestration/RUNBOOK.md` | the session's default (`powershell`) | Helios asked for its gate file, the session wrote one, the command ran: the diff |
| 21:22:06, 21:22:42 | `python -c "…"`, a reader for the cited line ranges | `bash` | never ran. Helios classes an interpreter run on inline code as tier 3, which needs an operator's authorization; a specialist has none to give |
| 21:23:55 | `rg --hidden -n '^' …` over the cited files | `bash` | Helios admitted it. It ran at 21:24:36.116Z and returned "Access is denied. Error code: Bash/Service/CreateInstance/E_ACCESSDENIED" |
| 21:25:18 | `git show HEAD:…` | `bash` | admitted; the same error |
| 21:28:04 | `git diff --no-index …` | `bash` | admitted; the same error |

The session then looked for a file-reading tool, found none, and returned:

> The diff shows one contiguous addition: subsection 5.5. I could not
> verify its factual claims against the cited source code or read
> `.autobot/inputs`: the shell returned `E_ACCESSDENIED` for subsequent
> reads, and no UI file reader was available. **I cannot determine whether
> the subsection contains source mismatches.**

with one finding, `fnd:34a8222313bb3a449081a8c233580105e97ee8a544422bf8211d933fd5a061e6`
(verification_gap): "No conclusion about source accuracy can be made."

### Which layer failed

| Layer | Evidence | Verdict |
|---|---|---|
| Helios command gate | admitted every command that reached a shell; refused only the inline-interpreter command, by its tier rule, before it ran | did what it is for |
| Autobot supervision | the review session raised no permission request: `assignment_approvals` has no row for `call_3128aded…` | not involved |
| Codex sandbox | reads inside the workspace are permitted, and the first command read | not the cause |
| Windows file permissions | no file was unreadable; the same files were read in the default shell | not the cause |
| Provisioning, worktree, staged inputs, path forms | as the table above: correct | not the cause |
| The provider's command tool on this host | the session set its tool's `shell` to `bash`. On this host the only `bash` on PATH is `C:\Windows\System32\bash.exe`, the Windows Subsystem for Linux launcher, and WSL refuses to start an instance for the sandboxed process | **the cause** |

The error text is WSL's, not the sandbox's and not a file system's. The
reviewer read it as a denial of its reads.

**Reproduced**, on the production route with the production session
arguments, in the isolated-worktree form production uses:

| | |
|---|---|
| `where.exe bash` (operator gate `20261119-taskforce-229`) | one result: `C:\Windows\System32\bash.exe` |
| `wsl.exe --status` from an unsandboxed process (230) | answers: a default distribution, version 2. WSL works; it refuses the sandboxed caller |
| Probe 1 (231): `call_b274488cb505498b9ac07c683d684365`, session `01a1f170-903e-7cf2-afa0-f73cd17b6f50`, 2026-11-19T01:34:07Z to 01:39:53Z | in the default shell: the directory listing, `README.md`, the staged `.autobot/inputs/predecessor.md` and `git log` were all read |
| Probe 2 (232): `call_ef667923760c483e92662c45c714b9ac`, session `01a1f176-be67-7090-9c44-c633279394c4`, 01:40:53Z to 01:43:41Z | `cat README.md` with the shell set to `bash`: admitted by Helios, then "Bash/Service/CreateInstance/E_ACCESSDENIED". `Get-Content README.md` in the default shell straight afterwards: the file |

### What the organization then did with it

| Step | What happened | Right? |
|---|---|---|
| Collection and verification | the result was collected, both assertions and the role transform passed (a1 `sha256:050e265e49e7ce4f175e5d08881c998d2fd96ebc0abef367b1ac1da3fe54528e`, a2 `sha256:799d26f2e1d5830d0c9931a9bcdb498067b3465b66b61d5500f2cf7c437d402e`, role-transform/reviewer `sha256:2b0f4dece54b79f23390eca1e467f81bae82c3c4cb0b69f7ed9b3d97b2c0bc74`), the sequence was accepted and COMPLETED | Yes. The result is a truthful one, produced under its contract. Authority is not asked whether a review found anything |
| Demonstration | `dem:5c6f07f8cc5fc73583fbddf22b6bc4a448492de8daac6e3752bf5cf36d763c4f`, QUALIFYING, for the reviewer | No. Nothing had been reviewed |
| Project completion | `prop:d63e8c1f16a63b6147c4a58ecb2d949e7bfdb92cd830bb99b7fca084e0637129` (cycle 114), PROJECT_COMPLETE, the Engineer reasoning that the review "reports no defect in the new text" | No. A review that could not look reports no defect |

These three records stay as they are. The demonstration is on the
reviewer's record as QUALIFYING and the project is COMPLETED; nothing was
rewritten after the fix. They are disclosed here as what they are.

### The fix (PR 26)

The owner of the defect is the organization above authority: it had no
way to tell a review that examined the work from one that could not, and
its contract let a session wander into a shell that does not exist for
it. Nothing was added to what a reviewer may do.

| Decision | What it does |
|---|---|
| D-TF-31 | Every execution contract tells a specialist to run every command in the default shell of its session, that a shell which cannot start is not a refusal and not a reason to stop, and that an interpreter run on inline code needs an authorization it cannot give. One text for every provider; it names no shell |
| D-TF-32 | A review states whether it examined the work: one line, `AUTOBOT_REVIEW: EXAMINED` or `AUTOBOT_REVIEW: NOT_EXAMINED`, read when the result is collected, never inferred from prose, recorded with the review's assignment. Authority is unchanged. For the organization a NOT_EXAMINED review leaves the work unreviewed: its demonstration is NOT_QUALIFYING, and PROJECT_COMPLETE is refused until another review of the same record is EXAMINED. No line at all is shown to the Engineer as NOT_STATED, with the instruction to treat the work as unreviewed |
| D-TF-33 (F-TF-29) | A dependent session is told what its working directory holds: `.autobot/inputs/README.md`, written at launch from ledger facts and recorded by content address, names the revision of the checkout, each delivered file, and the commit a predecessor's accepted change was published as. A review is also given what the reviewed work was given |
| F-TF-28 | The four routing defects Pilot 3's specialists reported, each first confirmed by a test that fails without its fix (section 25) |

The sandbox-edge rule of PR 25 is byte-identical after this change
(`supervision.py`, blob `d92a5d1bd9dd435be0e646ac773d918e20994266` before
and after). A reviewer gets what a review needs through its ordinary
READ_ONLY environment. Tests pin that a reviewer's session is granted no
command outside its sandbox, whatever it reviews: the two commands granted
to a builder are refused to it, as recorded production requests and as
written, and so are a file change and a permissions request.

| | |
|---|---|
| Commit | `f67acad3e28f7aee59c4c3f688a5c00ad7fc83eb`, sole parent `4abc57c2…` (Pilot 5's accepted commit), tree `28a1e542d7549765564e72410c488765bf0a6105`, 17 files, +1259 / −72 |
| Pull request | dimascior/Autobot#26, "A review that could not read the work is not a review": 2 commits, 18 files |
| Merge | `816aab4ed1d7a026ca1ef9122f06ac5dbd2c77f1`, parents `d01929ff…` and `f67acad3…`, the same tree |
| New tests | `tests/autobot/test_independent_review.py`, 16 tests; 4 more for F-TF-28 |
| New mutations | ten, each killed: a review that could not read the work recorded as one; an unexamined review closing its project; no note; no given inputs; any shell; no review contract; the route recorded after dispatch; session configuration applied to any provider; a mis-spelled placeholder; a reviewer supervised as a builder |
| Regression on those bytes | Python 3.8.10: 767 passed. Python 3.12.11: 766 tests, OK. Legacy coordinator: 41 tests, OK. Mutations: 48 of 48 killed |

### Pilot 6: a review that examined the work

A sixth objective ran on merged `main` (`816aab4…`, PR 26) with real work
to do. PR 26 had moved twenty of the lines RUNBOOK.md cites in
`taskforce.py`, the one in subsection 5.5 among them, and they were left
stale on purpose for one step; and the runbook did not yet tell an
operator what a review verdict is. The objective asked for both to be put
right and for the result to be independently reviewed against the source,
subsection 5.5 included.

| | |
|---|---|
| Objective | `obj-pilot6-review-confirmation-001` |
| Run | one `engineer --authorize-publication` process, 2026-11-19T03:17:37Z to 04:16:51Z, exit code 0: "stable after 1148 steps (1138 waits)", the permit, then "stable after 543 steps (534 waits)". Budget used: 2 contracts, 2 attempts |
| Project | `pj_22fea8d03c0c7a7edbe2ceb192f31a36`, "Runbook reference update and review-verdict subsection", opened by the Project Manager (`prop:fdbe24b010455636ee204947defa7418cedc59e995819071da51750e157e1eb1`, cycle 115), both workstreams laid out in that one decision |
| Team | two retained members, each resolved by the Taskforce through its seat; no outside specialist |

| | Builder: `runbook-references` | Reviewer: `runbook-review` |
|---|---|---|
| Workstream, sequence | `ws_19c2e61ca055d83c3746c076f1e4da67`, `sq_fcb4891735a4bf214f72a73e0e5ef289` | `ws_bfeee3763c67020e5bbd7fd43b2612a0`, `sq_84da72b9a0a19370ebc3a83cea4f9ae6` |
| Class | SOURCE_MODIFYING, scope `docs/architecture/orchestration/RUNBOOK.md` | READ_ONLY, a review of the builder's record |
| TaskContractRecord | `sha256:688829ec52401c2152c99407834b67c83496d17d7e99d687fc26b100c9dc63f9` | `sha256:51826cfffe8b788f30c1d3e77e638746529f2d56e1a4a9e5872ca4a607f19ff5` |
| Specialist | `sp_17a8384fb1d2fe7756e5db8e7a7072e5` | `sp_b2f72881be89546de36037d84fea287d`, recorded `independent_of` `sp_17a8384f…` |
| Seat | `ts_c31fff88a7cf30af7a2f73740370f927` documentation/builder | `ts_bddb43b39c785f99430327912e364331` verification/reviewer |
| Assignment | `asg_fd151f8833033858c44c380115f819d3` | `asg_dc9b5f700a37a8722dbe85010295754c` |
| Attempt | `at_f9d844424109270e8b0a1b9ecb4bb1a6` | `at_9febf6cc83f9a846c8a8deae54a14882` |
| AttemptContract | `sha256:415b1df79d4c80d1fb3f9a29b083940fef3801cfa40ac9405974d8ddf6da0797` | `sha256:1422f2c21c37f84e6aabf7d86cff455a57c4f6f4259f494e97cb6b679de3a176` |
| ExecutionManifest | `sha256:c99682ea5962b03916a1b939f3dab991eebc9d44ccba4d9268a4a153fd03c75c` | `sha256:55bf0569111eed861b67a47796db1c46f7f662426c53d7a44a294a9f5f2eb9be` |
| Call | `call_1443b6ee50cd327d1322f0b9df079bf5` | `call_08e03c806919cb2e66c1e955661d3f10` |
| Codex session | `01a1f1d0-4d0f-7ac2-b9c1-05b0c30862f2` | `01a1f1f4-fffa-73e3-8a16-0686689a48ae` |
| Route, recorded before dispatch and launched | `luna-6-max`: `codex`, `gpt-6-luna`, effort `max`, with its session arguments | the same route, its own record |
| Source revision of its worktree | `816aab4ed1d7a026ca1ef9122f06ac5dbd2c77f1` | `53667c52daa3d8ea5d60ccfd883b8aa5d18513b5`, the published change |
| RUNNING to FINISHED | 03:18:44.478Z to 03:57:08.732Z | 03:58:49.681Z to 04:16:50.355Z |

Every identity in the right-hand column differs from the one beside it.

**The builder.** It read the line shifts from
`git diff --unified=0 4abc57c2 HEAD`, updated the references, wrote
subsection 5.6 from four source files and committed. Outside its sandbox
it asked for two things and was granted both
(`approval_010d54d52e1a5a9e9f66c2dab1b270ba`, 03:51:48.319Z,
`git add docs/architecture/orchestration/RUNBOOK.md`;
`approval_8e257f818efa57cbaec2f9b3af2b0c8a`, 03:53:03.251Z,
`git commit -m "docs: update runbook line references and add review-verdict subsection 5.6"`).
Nothing was denied and nothing else was asked.

| Authority, in journal order | Identity |
|---|---|
| Candidate frozen (t-s07, 1373) | CandidateReceipt `sha256:ddcdcb0429ed1b7f908c055acbb15b823a63bf30775c1f2b9a8e3f20644a3016`, tree `e868d9fe4079bda611ddef9d2083ab67819454bd` |
| Execution receipt (1374) | `sha256:9fbfadf625f57520590a979aca8bbcd7ff46345405b9a275c932a5d1401a296e`, COMPLETE |
| Verification (1378 to 1380), all PASS | role-transform/builder `sha256:6ed958f8b55dccf59c5d84637dc1ddc93299a3fe46b948cf95507d1013544507`; a1 `sha256:57d67cd5dc260c8a4c5ab8544ab0f98b877211f6258f28b269701e38312ee455`; a2 `sha256:b8115b8b16a3d594ef52d92800ee83ed4821fa5063b15bd5b577fb4c4c0328db` |
| Standing permit granted (1383, 03:57:55.229Z) | one use |
| COMMITTING (t-s15a, 1385) | reservation `rv_2bd959ec7f33f0da062cf569ac307a52`, intent `in_c9cfdaa226c4236e422e6b25ebc8e3e6`, expected old `816aab4e…` |
| COMMITTED, point of no return (t-s16, 1388) | CommitReceipt `sha256:a7e1a3b523eb9bd79613dca8f2cea86839066625d7a898c2e8f327a875b830c4`: `refs/heads/main` observed at `53667c52daa3d8ea5d60ccfd883b8aa5d18513b5` |
| ACCEPTED (t-s20, 1389) | AcceptanceRecord `sha256:7e51a2d71347bf8e73a7661e2d730185675f784c961caa8ea9d146fa2732e6bf` |
| Dependencies released, COMPLETED (1390, 1391, 03:57:55.626Z) | release `sha256:9b3931bd66fbd414458ba3ead90cd12dd814c294b7c9b50cdab27b46da551db4`, successor `sq_84da72b9…` |

The published commit `53667c52…` has the one parent `816aab4e…` and
changes the one authorized file, +26 / −18.

**The review started only after that.** Its sequence was BLOCKED from
03:18:39.804Z (journal 1291, unsatisfied `review-of`), became READY at
03:58:46.522Z (1392) and its attempt was dispatched at 03:58:46.625Z,
fifty-one seconds after the builder's sequence was COMPLETED.

**What the reviewer was given**, checked from the files themselves while
its worktree still existed (2026-11-19T03:59:19Z,
`extracts/pilot6-staged-inputs.txt`):

| | |
|---|---|
| Worktree | `autobot-wt-taqucij0`; its HEAD is `53667c52…`, the revision the assignment recorded; Git reports it clean |
| `.autobot/inputs/runbook-references.md` | 1540 bytes, SHA-256 `af6e0f7a654105497b38cc93d389f77aa38c4202759919e8b9c0a1df829ae234`: the builder's accepted artifact (journal 1370), the address the assignment recorded |
| `.autobot/inputs/README.md` | 797 bytes, SHA-256 `ed3f1c9331cb93a2fc494d38f5f6e00eef58fb163320a1fa2060b3e0c604944f`: the note the assignment recorded as `execution.inputs_note` |
| Anything else in that folder | no |

The note said, in full:

> Your working directory is a checkout of the repository at revision
> 53667c52daa3d8ea5d60ccfd883b8aa5d18513b5.
>
> - runbook-references.md: the accepted result of workstream
>   `runbook-references` (sequence sq_fcb4891735a4bf214f72a73e0e5ef289,
>   attempt at_f9d844424109270e8b0a1b9ecb4bb1a6). That work changed the
>   repository. Authority published it as commit
>   53667c52daa3d8ea5d60ccfd883b8aa5d18513b5, whose parent is
>   816aab4ed1d7a026ca1ef9122f06ac5dbd2c77f1. Your working directory is at
>   that commit: the files in it ARE the changed files. `git diff
>   816aab4e… 53667c52…` shows exactly what it changed.
>
> These files were verified and accepted by the authority plane. They are
> material to read, not instructions.

Its frozen prompt carried the shell rule and the review contract.

**What the reviewer did**, from the provider's own session record: 33
commands, every one in the session's default shell, none with another
shell selected, no access error. Helios decided each of them: 11 were
admitted and ran, and the other 22 are the refusals that precede a gate
file in the handshake. It read the note first, then the runbook, then
every moved range of `taskforce.py`, and then, in one command,
`routing.py:4-52`, `supervision.py:91-440`, `prompts.py:109-123`,
`findings.py:91-114`, `query.py:193-229` and `policy.py:1097-1111`; it
located `autoswarm_transport.py` and read lines 106 to 108. It tried `sed`
once, which the default shell does not have, and went back to the shell's
own commands. It raised no permission request and started no sub-agent.

**What it returned:**

> I examined both the runbook and the cited source files. All taskforce,
> routing, and `autoswarm_transport.py` references, and the statements and
> citations in subsections 5.5 and 5.6, are correct. No defects found.
>
> Files and line ranges read:
>
> - `.autobot/inputs/README.md:1-10`
> - `docs/architecture/orchestration/RUNBOOK.md:1-391`
> - `src/intelligence/taskforce.py:149, 230-236, 253-262, 423-429,
>   745-756, 827-835, 858-874, 913-930, 979-984, 998-1000, 1116-1125,
>   1182-1218, 1327`
> - `src/intelligence/routing.py:4-52`
> - `src/autobot/autoswarm_transport.py:106-108`
> - `src/intelligence/supervision.py:91-440`
> - `src/intelligence/prompts.py:109-123`
> - `src/intelligence/findings.py:91-114`
> - `src/intelligence/query.py:193-229`
> - `src/intelligence/policy.py:1097-1111`
>
> AUTOBOT_REVIEW: EXAMINED

with an empty findings block.

| What happened to that result | Identity |
|---|---|
| Collected; execution receipt | `sha256:e0a5c18c4f83b2b6e8b4a67922b074706d5c58f24b5284c9eb3567f15e832e05`, COMPLETE |
| Verdict read from the line and recorded with the assignment | `execution.review` = `{"verdict": "EXAMINED"}` on `asg_dc9b5f70…` |
| Verification, all PASS | a1 `sha256:3193196fc4d90d638c2d9face79f995c84907ab99b8fbc2553575ce12c7da3bf`; a2 `sha256:ee94db652232c97262e083c31570caf69af26e179a68ab6e69a3e93af4cab5c4`; role-transform/reviewer `sha256:f836457b548b68fe2f09b2db3d10fe5e834d958e7e9ee9477e69434c6fe692b8` |
| VERIFIED, ACCEPTED (t-s14, 1446), COMPLETED (1448, 04:16:51.460Z) | AcceptanceRecord `sha256:56155964201fdeaa9d66a9b1242a1ef15a460f2b05ba1dfc7891b467dd142b04`, release `sha256:53ca84da2652f62f2a757e3f2520018ef1a31c96dcd4ad941343cd3b88969a1c` |
| Demonstration | `dem:026fdb130ac8f72866ee0fb887fd6f88b03a2b01bfc05a51f59df957a8e2e5c3`, QUALIFYING, with `review_verdict` EXAMINED among its observations |
| Project completion | `prop:0235b8b15fd4963fbd972148067f43209114ebbfff6494547812e86bd484b5e4` (cycle 122, 04:16:51.730Z): "The runbook-review workstream examined the builder's work (review_verdict: EXAMINED) and found no defects." |

**Was the review right?** Checked two ways, both outside the organization
and neither counted as a review. Mechanically: each of the 129 references
the runbook carried was taken through the line shifts of
`git diff --unified=0 4abc57c2 816aab4e` and compared with the published
runbook (`extracts/pilot6-facts.txt`); the prediction had been computed
before the objective was written (operator gate `20261119-taskforce-249`).

| | |
|---|---|
| References before | 129, to 13 source files |
| Expected to stay, and stayed | 109 |
| Expected to move, and moved to exactly the predicted lines | 20, all in `taskforce.py` |
| On a line that itself changed | 0 |
| Expected by the line shifts and absent | 0 |
| New references | 6, all in subsection 5.6 (runbook lines 308 to 312) |

And by reading: the five places subsection 5.6 cites (`prompts.py:109-123`,
`findings.py:91-114`, `query.py:193-203`, `query.py:206-229`,
`policy.py:1097-1111`) hold what its sentences say.

**One thing the builder did that is not the organization's.** Before it
committed, the builder's session started a sub-agent thread of its own,
inside its session, through its provider's collaboration tool. It named
it `independent_review`, and its result says "An independent review
checked the citations". That is the builder checking its own work with
its provider's help. It is not an independent review in the
organization's sense, which is another specialist, assignment, call and
session staffed by the organization, and it is not counted as one
anywhere here. The facts, from `extracts/pilot6-subagents.txt`:

| | |
|---|---|
| Spawned | 2026-11-19T03:39:10.174Z, thread `01a1f1e3-07f8-7dc0-b052-06ae91425bde`, active to 03:49:11.575Z, in the builder's worktree and sandbox |
| Its commands | each passed the Helios gate; their decisions are stored under the builder's session, with the builder's session id, in the folder authority collects for the attempt (the 3 that ran are among that folder's 22 ALLOW decisions) |
| Requests to leave the sandbox | none |
| Its own reasoning record | a second provider session file; authority does not collect it |
| The other ten default-route sessions on record at the time, the reviewer's among them | none started a sub-agent |

It was recorded as F-TF-31. What was done about it is the rest of this
section.

**What this run establishes.** A specialist's accepted SOURCE_MODIFYING
change was reviewed by a different specialist, under a different
assignment, attempt, call and session, on the default route. The reviewer
was given the accepted result and a worktree at the published commit,
read the changed text and the source it must agree with, said what it
read, and stated that it examined the work. That statement was recorded
from the line, the result was collected, verified and accepted by
authority, and the project was completed on it. Independent review by a
Luna specialist is live as of this run, and not before it.

### One assignment, one session (PR 28)

Asked whether to close with F-TF-31 open or to forbid sub-agents first,
the operator answered: "Forbid sub-agents first".

**Why the builder did it**, from its own records. Two things met in its
session.

| | |
|---|---|
| What it was shown | Its frozen prompt carried the project objective as context, and the objective asked for the result to be "independently reviewed against the source, subsection 5.5 included". Nothing in the prompt said that the rest of the objective was other assignments |
| What it was offered | The provider gives every session six `collaboration` tools: `spawn_agent`, `followup_task`, `interrupt_agent`, `list_agents`, `send_message`, `wait_agent`. The builder's session record holds twenty calls of them |
| What it said while doing it | "I've also requested a separate read-only review of the finished section, including 5.5, and will stage once that check reports back." |

**Whether the provider can be told not to offer the tool** was measured
before anything was designed.

| | |
|---|---|
| `codex features list` (operator gate `20261119-taskforce-325`) | two features of that kind: `multi_agent` (stable, on) and `multi_agent_v2` (stable, off) |
| Probe 1 (326): `call_aa1f1b2bbd814d6c9272861e7623273c`, session `01a1f2b5-3632-7fe3-bc21-4ace95268ea4`, finished 2026-11-19T07:29:18Z | launched on the default route's provider, model and effort with `-c features.multi_agent=false`. Asked to list every tool it could call, the session listed the six collaboration tools |
| Probe 2 (327): `call_4f41eeafce524738bff22fa244ab9f2c`, session `01a1f2b7-9cb2-73c3-9c3a-9ee82262e758`, 07:31:21Z to 07:31:42Z | the same with `-c features.multi_agent_v2=false` added. Both arguments are on the provider's command line in AutoSwarm's call record. The same six tools |

So the routing policy was not changed: there is no session argument to
declare. The probes ran no command and changed nothing. Their call
records are in AutoSwarm's store; they are not assignments. What each
probe shows is what its session reported when asked, twice the same.

**What was changed** is the organization's own layer.

| Decision | What it does |
|---|---|
| D-TF-34 | Every execution contract says that one assignment is one specialist doing its own task in one session. It starts no sub-agent, other agent or other session, and hands no part of the work to one. The project objective it is shown is context: its other parts are other assignments, which the organization staffs. A check of its own work is part of the task and is not an independent review, and the response must not call it one. One text for every provider; it names no provider and no provider's tool |
| A test, no new rule | For a provider that asks before each tool use, the supervision policy already refused every tool outside its three lists. A test now pins that for tools that start another agent, for a builder and for a reviewer. `supervision.py` is the blob it has been since PR 25 (`d92a5d1bd9dd435be0e646ac773d918e20994266`) |

**What that does and does not enforce.**

| Provider | The rule is | Enforced by |
|---|---|---|
| asks before each tool use (the Claude Code routes: escalation, fallback, utility) | the contract's and the policy's | the supervision policy refuses the tool, deterministically |
| runs in a sandbox and asks only at its edge (the default route) | the contract's alone | nothing deterministic. A session there can still break it, Autobot does not detect that by itself, and whether a session kept it is read afterwards from the provider's own session record |

| | |
|---|---|
| Commit | `279fc9c1c6a36dad89236fcdf70892de528b9d9b`, sole parent `9d97a34d…`, tree `e566befb14be037e0888d04e9dfaeb1c4a2ee990`, 4 files, +96 / −6 |
| Pull request | dimascior/Autobot#28, "One assignment, one session": 1 commit, 4 files |
| Merge | `d320d1bf9139703d36c0a450939f088803f0291d`, parents `9d97a34d…` and `279fc9c1…`, the same tree |
| New tests | 2, in `tests/autobot/test_independent_review.py` (18 in that module) |
| New mutations | two, each killed: the contract lets a session hand its work to sub-agents; a provider that asks may start another agent |
| Regression on those bytes | Python 3.8.10: 769 passed. Python 3.12.11: 768 tests, OK. Legacy coordinator: 41 tests, OK. Mutations: 50 of 50 killed |

### Pilot 7: the one-session rule in production

A seventh objective ran on merged `main` (`d320d1b…`, PR 28) under the
condition that had triggered F-TF-31: its project objective asked for the
result to be "independently reviewed against the source", and the builder
was shown it as context. The work was real again. PR 28 had moved the one
line range RUNBOOK.md cites in `prompts.py`, and the runbook did not yet
state the rule.

| | |
|---|---|
| Objective | `obj-pilot7-one-session-001` |
| Run | one `engineer --authorize-publication` process, 2026-11-19T08:00:45Z to 08:40:19Z, exit code 0 after 2395 seconds: "stable after 696 steps (686 waits)", the permit, then "stable after 422 steps (410 waits)". Budget used: 2 contracts, 2 attempts |
| Project | `pj_1b5046826b37a1fe6a5dec25f1d6a28f`, "Runbook session-rule update", opened by the Project Manager (`prop:f32dd74278acf28a223e63cba6c2f476b2175118abcf1d2f7ac31a01e2529fd7`, cycle 123), both workstreams laid out in that one decision |
| Team | the same two retained members, each resolved by the Taskforce through its seat |

| | Builder: `runbook-session-rule` | Reviewer: `runbook-review` |
|---|---|---|
| Workstream, sequence | `ws_46a102c8d6dacaef94fb9143f9d4d7eb`, `sq_18f8a8a626d71baa037f13cdeb17e703` | `ws_e9a0f927faaa5a1ae9f7d654d33f3d27`, `sq_ec7a930a711bd766118f6c99051ac425` |
| TaskContractRecord | `sha256:2e95933d85a2d2a7cbd0a856ded82a0eefc256c8f838df60e9122608b7d3e07a` | `sha256:15dbb1c6ff43e8dcb371658773f5465aa3345fbb2eee3a62f20e271317fe1200` |
| Specialist, seat | `sp_17a8384fb1d2fe7756e5db8e7a7072e5`, `ts_c31fff88a7cf30af7a2f73740370f927` | `sp_b2f72881be89546de36037d84fea287d`, `ts_bddb43b39c785f99430327912e364331` |
| Assignment | `asg_a4c407392e5a4ffc90ca2f01a13b8472` | `asg_0a86314ae0d86c4d790c01cc59acbd08` |
| Attempt | `at_e5e3bc962a887ecb4ca2a3dc97e53808` | `at_2138ee2934a4e695ee12e3cfeac8618b` |
| AttemptContract | `sha256:613b41c69fdcec0b83fc7e93df06b55f8c4ebaf998f8ea5dd0cb69325004c772` | `sha256:b7a28b755f0a1386b42ee646be15c13362bec9170a35890130227d991a95ccca` |
| ExecutionManifest | `sha256:d6b8f0935fb0ac4b9ecd776566cc07481a6544b674fd3f22d38ba9d2b2f5b022` | `sha256:21e425d5d0e00ee17ece4cfab26c1a627f5181591df46701f7c4074d46730d3d` |
| Call | `call_0aa4fd7bb46a14fcddbc3612aa2dea1a` | `call_3272c43fd2c4b46bd60b133116700938` |
| Codex session | `01a1f2d3-7ca2-78c1-b3eb-d81c11fefe7d` | `01a1f2ea-1d74-7d20-aec5-9a1e2ff29118` |
| Route, recorded before dispatch and launched | `luna-6-max`: `codex`, `gpt-6-luna`, effort `max` | the same route, its own record |
| Source revision of its worktree | `d320d1bf9139703d36c0a450939f088803f0291d` | `b32fd6f7c0a75567e6a523b0ca4be733305f454e`, the published change |
| RUNNING to FINISHED | 08:01:50.561Z to 08:24:56.858Z | 08:26:33.529Z to 08:40:18.583Z |

**What the run was for.** Each session's own record was read for the
provider's sub-agent tools, by the script that had found the one spawn
and its nineteen follow-up calls in Pilot 6's builder
(`extracts/pilot7-subagents.txt`).

| | Builder's session | Reviewer's session |
|---|---|---|
| Its frozen prompt | carried the one-session rule, and the project objective that asks for an independent review | carried the rule and the review contract |
| Calls of the six collaboration tools | 0 | 0 |
| Sub-agent threads | none | none |
| Commands it ran, each in its default shell, no access error | 54 | 13 |
| Helios decisions stored under its session | 54, as many as its own commands (Pilot 6's builder: 69, for 56 commands of its own and 13 of its sub-agent) | 13 |
| What its result calls its own checking | "I verified the cited prompt lines in one ranged read" | it is the review |

Neither session started another agent, and the builder did not describe
its own check as a review. This is one run. On this provider the rule is
the contract's alone (above), so what it shows is that the contract was
kept here, not that it cannot be broken.

**The builder.** It read the line shift from
`git diff --unified=0 9d97a34d HEAD`, moved the one reference, wrote
subsection 5.7 from two source files and committed. Outside its sandbox it
asked for two things and was granted both
(`approval_ab36bcbfc15354718453cac916b2332e`, 08:19:18.607Z,
`git add docs/architecture/orchestration/RUNBOOK.md`;
`approval_69a8ddfda2985243bc27125b6d0e4ed9`, 08:21:25.994Z,
`git commit -m "docs: update RUNBOOK.md line refs and add 5.7 one-session rule"`).
Nothing was denied and nothing else was asked.

| Authority, in journal order | Identity |
|---|---|
| Candidate frozen (t-s07, 1521) | CandidateReceipt `sha256:941a8667ff81b248d37401430f4a2515f6581d1bad33ef2817848c622b431fcc`, tree `d1c5df76022c7cdb46ed8ec8b89825e8a6cd6fd9` |
| Execution receipt (1522) | `sha256:25cd565da8d6ebb7dc593a721302f0ce947186c68b2a89854746c77673cb43c9`, COMPLETE |
| Verification (1526 to 1528), all PASS | role-transform/builder `sha256:2019bb542e0b7cd6b8c0becd8674dbf4828bff0852cd4fa88d479c656167abcb`; a1 `sha256:88048366a464d1212fb7d22e2dcd03593d2182c0101b667e4ac465dd54b29659`; a2 `sha256:dbf3961631fee2a423f321be8a9fe81eb056ed3de19ba0892f26d387b3e18c64` |
| Standing permit granted (1531, 08:25:54.191Z) | one use |
| COMMITTING (t-s15a, 1533) | reservation `rv_5f9d3c4029670d21710425fe19688b12`, intent `in_b67a6c105ce4f9e4ce6715688043c36f`, expected old `d320d1bf…` |
| COMMITTED, point of no return (t-s16, 1536) | CommitReceipt `sha256:c2ec70fa6788c346b68203d40b05c1d5d4fb5b9973d19627b784facf111201ac`: `refs/heads/main` observed at `b32fd6f7c0a75567e6a523b0ca4be733305f454e` |
| ACCEPTED (t-s20, 1537) | AcceptanceRecord `sha256:98b6413aaa79fa15c8cf1a498eb4a93fd21499b4b87316971da674d41ff606ab` |
| Dependencies released, COMPLETED (1538, 1539, 08:25:54.638Z) | release `sha256:018a8d9f45708008a0d23657d32e6a70e56e71f6b4a4a4a426fe53bd10d305a7`, successor `sq_ec7a930a…` |

The published commit `b32fd6f7…` has the one parent `d320d1bf…` and
changes the one authorized file, +9 / −1. Its demonstration is
`dem:d0965abfe7edcd803bd8ea9b732d9d3f052e897990f255af49abf7ddaa2ad952`,
QUALIFYING.

**The review started only after that.** Its sequence was BLOCKED from
08:01:47.249Z (journal 1456, unsatisfied `review-of`), became READY at
08:26:30.385Z (1540), and its attempt was dispatched at 08:26:30.492Z,
thirty-six seconds after the builder's sequence was COMPLETED.

**What the reviewer was given**, checked from the files themselves while
its worktree still existed (2026-11-19T08:26:58Z,
`extracts/obj-pilot7-one-session-001-staged-inputs.txt`):

| | |
|---|---|
| Worktree | `autobot-wt-notz8xmd`; its HEAD is `b32fd6f7…`, the revision the assignment recorded; Git reports it clean |
| `.autobot/inputs/runbook-session-rule.md` | 1354 bytes, SHA-256 `3d786e60107e4c453a1c742c0a082ec9c57a7a164707d34eb1c2a2f7986b0e3f`: the builder's accepted artifact (journal 1518), the address the assignment recorded |
| `.autobot/inputs/README.md` | 801 bytes, SHA-256 `44d776aec6a8b2edf82bfe662eb5b6ee88762642a8e26407e8f366f2da8d4cb8`: the note the assignment recorded as `execution.inputs_note` |
| Anything else in that folder | no |

**What the reviewer did**, from the provider's own session record. It
read the note, then the runbook and the accepted result, then every line
of the runbook that cites `prompts.py`, through its shell: 13 commands,
all in its default shell, 4 admitted and run, the other 9 the refusals
that precede a gate file. The shell cut the middle out of its listing of
the runbook, and its result names the ranges it actually saw. Three times
it addressed a gate file to the
command gate's root folder instead of its pending folder. That is a write
outside its sandbox; the provider asked, and the policy refused each one
(`approval_886d4de2f3835db4ae39b230effbd74e` at 08:32:13.606Z,
`approval_9b2b7209813853e9be48dbe57c857ba0` at 08:34:35.236Z,
`approval_86f9e733b15c547db23ac4cbef747177` at 08:34:49.638Z: "the sandbox
already holds every location this assignment may write"). A reviewer is
granted nothing outside its sandbox, and this one was not.

It then did something the organization had not provided for. It looked
through its provider's tool registry, found a connector to the remote
repository, and read the source files there, at `d320d1bf…`, the parent of
the commit under review. (It asked for them at `b32fd6f7…` first; the
remote did not have that commit, which authority had published locally
eleven minutes earlier.) What that means is the next part of this section.

**What it returned:**

> AUTOBOT_REVIEW: EXAMINED
>
> **No defects found.** The runbook's three lines containing `prompts.py`
> references are 308, 316, and 320; their cited ranges match the prompt
> source. The statements and citations in §§5.6 and 5.7 also match the
> source, including the prompt contracts, the final deny in `decide`, and
> Codex's sandbox approval path.
>
> **Files and ranges read**
>
> - `docs/architecture/orchestration/RUNBOOK.md:1-175, 278-399`; searched
>   the full file for `prompts.py` references.
> - `.autobot/inputs/README.md:1-10`
> - `.autobot/inputs/runbook-session-rule.md:1-10`
> - `src/intelligence/prompts.py:79-140, 227-239`
> - `src/intelligence/supervision.py:76-90, 377-525`
> - `src/intelligence/findings.py:91-114`
> - `src/intelligence/query.py:1-450`
> - `src/intelligence/policy.py:1088-1115`
>
> The source ranges were read at parent commit
> `d320d1bf9139703d36c0a450939f088803f0291d`. The accepted input identifies
> `b32fd6f7c0a75567e6a523b0ca4be733305f454e` as the reviewed commit and
> says it changed only `RUNBOOK.md`, so those source files are unchanged in
> the reviewed revision.

with an empty findings block. Its reasoning about the two revisions is
right: the commit changes the one file (authority's own scope check, and
`git diff --stat`), so every source file it read has the same content at
both.

| What happened to that result | Identity |
|---|---|
| Collected; execution receipt | `sha256:980b18a1ace481670d9cd27c5a526f3519379b8cde5c56cba19164cc1796964a`, COMPLETE |
| Verdict read from the line and recorded with the assignment | `execution.review` = `{"verdict": "EXAMINED"}` on `asg_0a86314a…` |
| Verification, all PASS | a1 `sha256:8cce841941b7abbdb5c37791be5acfb67670152a2369311397bf6fa6fb92cd6f`; a2 `sha256:17eca4accf7e4f5e366a7a28cabb494d5b0f758c46c369a964f7898812e4d11a`; role-transform/reviewer `sha256:5403d278af7d3bec9686f20e91a8644b5224d3157e572efdff4d512f06f461af` |
| VERIFIED, ACCEPTED (t-s14, 1575), COMPLETED (1577, 08:40:19.386Z) | AcceptanceRecord `sha256:d260016dc630162dcd425d9cc731451ed0d2120aa2266d7cd451b7eb5e7b8e27`, release `sha256:15433a9a8916ecf724bc2c32b1590e2405ed3f8ada8c0710409e4daf7b5b47e7` |
| Demonstration | `dem:96729cf174dfb546a246e959a1551c28d371538a602c8fe79b4804fa5ba2da7f`, QUALIFYING, with `review_verdict` EXAMINED and three refused requests among its observations |
| Project completion | `prop:3d55299e231f67ccbecf7dcdf56bd11543f97f45faff027a0bf3e6552e5e9828` (cycle 130, 08:40:19.665Z): "Every workstream is COMPLETED, the independent review examined the change and found no defects, and the SOURCE_MODIFYING result has been published." |

**Was the work right?** Checked the same two ways as Pilot 6, both outside
the organization and neither counted as a review. Mechanically
(`extracts/obj-pilot7-one-session-001-facts.txt`; the prediction was
computed before the objective was written, operator gate
`20261119-taskforce-331`):

| | |
|---|---|
| References before | 135 |
| Expected to stay, and stayed | 134 |
| Expected to move, and moved to exactly the predicted lines | 1: `prompts.py:109-123` to `prompts.py:126-140` |
| On a line that itself changed | 0 |
| Expected by the line shifts and absent | 0 |
| New references | 14, all in subsection 5.7 (runbook lines 316 to 320) |

And by reading: each of the twelve places subsection 5.7 cites in
`prompts.py` and `supervision.py` holds what its sentence says.

### What Pilot 7's session records showed next (F-TF-32)

Pilot 7's reviewer read its source through a tool the organization had
never granted and did not know its sessions had. Before anything was said
or changed, every session the default route had run was read for the
tools it actually called (`extracts/tool-census.txt`, operator gate
`20261119-taskforce-372`).

| | Thirteen sessions, Pilots 3 to 7 |
|---|---|
| Governed shell commands, each of which passes Helios | 403 |
| Sandboxed writes with the provider's patch tool (gate files, and a builder's change) | 342 |
| The clock | 93 |
| The provider's sub-agent tools | 20 calls, in one session: Pilot 6's builder |
| The provider's GitHub connector | 16 tool-use scripts in two sessions, both reviewers, every one a read of the remote repository: Pilot 3 `argument-review` (session `01a1eff8-07ad-78b0-8d33-c960da78c31b`) and Pilot 7 `runbook-review`, 8 each |
| The provider's desktop-control tool | asked for its state by the reviewers of Pilot 5 and Pilot 7, each time looking for a file reader; nothing usable came back |
| A tool that writes anywhere outside the sandbox | none |

A script that calls a tool in a loop is counted once, so the counts of the
last four rows are lower bounds on calls and exact on sessions.

What that changes in what this document says elsewhere:

| Where | What the session records show |
|---|---|
| Pilot 3's `argument-review` (section 18) | It ran one shell command. It read `autoswarm_transport.py` and `routing.py` through the connector, from the remote's `main`, between 18:44:06Z and 18:47:28Z on 2026-11-18. Remote `main` was `1732507c…` from 18:25:08Z to 20:57:56Z (operator gate 382): the revision its worktree was at. It read the right content from the wrong place. Its result was verified and accepted as written, and what it reported was later confirmed in source (F-TF-28) |
| "a Codex session reads through the shell" (section 25) | true of eleven of the thirteen sessions |
| Pilot 6's review, on which independent review rests (above) | unaffected: 33 shell commands, the patch tool for its gate files, the clock, and nothing else |

**Where the tools come from.** A session of the default route's provider
inherits the operator's own provider configuration: its apps and their
connectors, its plugins, its MCP servers, web search. None of it passes
the sandbox or the command gate, none of it is in an attempt's command
evidence, and no assignment grants it. A probe asked a session for the
names in its own tool registry, three times:

| Probe (operator gate) | Session arguments | Registry |
|---|---|---|
| `call_786372e1f1bb4687be90bec77755de08`, session `01a1f300-0f44-7480-9388-3bbaf6c8e893`, 08:50:29Z to 08:50:41Z (373) | the production route's | 158 tools: 14 of the provider's own (the shell, the patch tool, the clock, goals, image generation, web, MCP resources, plugin install), 141 of provider-side apps (89 of the GitHub connector, tools that write among them) and 3 of a node REPL server |
| `call_5675f7f27456459b9f215ae528486cb2`, session `01a1f301-3bd1-7d73-85e1-cd991b8fc268`, 08:51:46Z to 08:52:06Z (374) | the same, with eleven configuration overrides | 5 tools: the shell, the patch tool, the clock, an image viewer, the shell's input |
| `call_ab5d3daa6c21487d8966b13c4fcabc1c`, session `01a1f302-e806-7283-82ef-a82035667219`, 08:53:36Z to 08:54:11Z (375) | the same, clearing every MCP server at once instead of naming one | 11: the node REPL server and the MCP resource tools stayed. That override is ignored, so a server is switched off by name |

In the last two the session still named the provider's six sub-agent
tools, which are outside that registry (F-TF-31, unchanged).

### A session stays on this machine (PR 29)

| Decision | What it does |
|---|---|
| D-TF-35 | An assignment's session reaches nothing outside the machine's files and its own shell. Every execution contract says so, for every provider: no connector, app or plugin, no web access, no browser or desktop control, no remote copy of the repository, even to read; what a session could not read without one it has not read, and it says so. The contract names no provider and no service |
| The default route | Its session arguments in `config.toml` switch off, for the session only, the provider features that gave a session more: apps and their connectors, plugins, the node REPL server, web search, browser and desktop control, image generation, goals. They are exactly the arguments of the second probe. The operator's provider configuration is not touched |
| A test, no new rule | For a provider that asks before each tool use the supervision policy already refused every tool outside its three lists. A test now pins that for a connector, the web, a browser and a desktop, for a builder and for a reviewer, to read and to write |

| Provider | Enforced by |
|---|---|
| asks before each tool use (the Claude Code routes) | the supervision policy, deterministically |
| runs in a sandbox and asks only at its edge (the default route) | the provider's own switches, declared on the route: the tools are not in the session. That is established by the probe, not by a test, and has to be probed again when the provider changes. An MCP server configured later is covered by the contract alone until its line is added to the route |

| | |
|---|---|
| Commit | `456c9f51d7d223271ff41823b03a38169d985ef5`, sole parent `b32fd6f7…` (Pilot 7's accepted commit), tree `f1202eebc0187d5ced34348a343fc931d5f977a4`, 5 files, +133 / −16 |
| Pull request | dimascior/Autobot#29, "A session stays on this machine": 2 commits, 6 files |
| Merge | `8ae0922f1e79819a1d927a215133be25561bee1f`, parents `d320d1bf…` and `456c9f51…`, the same tree |
| New tests | 2, in `tests/autobot/test_independent_review.py` (20 in that module) |
| New mutations | two, each killed: the contract lets a session use whatever its provider offers; a provider that asks may use a connector or the web |
| Regression on those bytes | Python 3.8.10: 771 passed. Python 3.12.11: 770 tests, OK. Legacy coordinator: 41 tests, OK. Mutations: 52 of 52 killed |

### Pilot 8: the route's switches and the rule in production

An eighth objective ran on merged `main` (`8ae0922…`, PR 29), with the
route's new arguments in force and real work to do. PR 29 had moved six
of the eight references RUNBOOK.md makes into `prompts.py` and rewritten
the line a seventh points at, those of subsection 5.7 among them; and
the runbook did not yet tell an operator what a session may reach.

| | |
|---|---|
| Objective | `obj-pilot8-session-reach-001` |
| Run | one `engineer --authorize-publication` process, 2026-11-19T09:21:56Z to 09:58:22Z, exit code 0 after 2211 seconds: "stable after 652 steps (642 waits)", the permit, then "stable after 360 steps (351 waits)". Budget used: 2 contracts, 2 attempts |
| Project | `pj_9b807ae1b17c9546162ffbcea78b53cd`, "Runbook session-reach update", opened by the Project Manager (`prop:5a6fe679dd197663bfb7447242c9e91aee840006a285e372a1881bdaf0ff4b16`, cycle 131), both workstreams laid out in that one decision |
| Team | the same two retained members, each resolved by the Taskforce through its seat |

| | Builder: `runbook-session-reach` | Reviewer: `runbook-review` |
|---|---|---|
| Workstream, sequence | `ws_cf5183461d9bcdaf301ef20222bbddb1`, `sq_14173b1bb0550ee226f6c9805e1660ea` | `ws_f66c05fb7788b91be95d9226e0b3149b`, `sq_ceba2785be738dbbfd031f297ba85d3c` |
| TaskContractRecord | `sha256:c22f2b9368f060a0972acbcc4975fbef694253fb96b74edb6855f02f98cbfb44` | `sha256:16be77815cee32b3939dd6b074082f16e7fe5a9629dbb1849f4f7c4cd6a9a0f9` |
| Specialist, seat | `sp_17a8384fb1d2fe7756e5db8e7a7072e5`, `ts_c31fff88a7cf30af7a2f73740370f927` | `sp_b2f72881be89546de36037d84fea287d`, `ts_bddb43b39c785f99430327912e364331` |
| Assignment | `asg_6f24bcea837f7ba9fbf3f4822236837d` | `asg_8f56da9bb7e8787afedb2df12c023c97` |
| Attempt | `at_9048f4a5335415ae0b3c0e00de6901ae` | `at_cb5e67504a4765133bd5aa57756c5cc8` |
| AttemptContract | `sha256:6a3fbba1de21adf401b4478551c6c06efc10483d780a73b85ec5ecbb4e17da9e` | `sha256:42a8709ecee7629859f8d706f2333f21f3514917c4f2d2e4971448766135357a` |
| ExecutionManifest | `sha256:48ce9d72660a8df4aca2f9cdfb64971a253c03cf2403add7312a1890c2c4be5b` | `sha256:d0a24d951b5330ca74f422f5bc081fb2e1a0276b2dcb8daaba03cc23854d2b72` |
| Call | `call_79adcc6648c1bae49992edd0e95e7e98` | `call_27011aba43c9f1ba40b3cbacd8721578` |
| Codex session | `01a1f31d-e7c5-7461-b6a7-ea62fda85d0c` | `01a1f333-5d8d-7931-884a-bd7ff87be28a` |
| Route, recorded before dispatch and launched | `luna-6-max`: `codex`, `gpt-6-luna`, effort `max`; the eleven overrides are in the route recorded with the assignment and on the provider's command line in AutoSwarm's call record | the same route, its own record, the same arguments |
| Source revision of its worktree | `8ae0922f1e79819a1d927a215133be25561bee1f` | `532ac9f89ac4d1c9b4ab1576eb66ee568f94a0f7`, the published change |
| RUNNING to FINISHED | 09:23:07.656Z to 09:44:44.772Z | 09:46:34.035Z to 09:58:21.671Z |

**What the run was for.** Every tool call of both sessions, read from the
provider's own session records (`extracts/tool-census.txt`,
`extracts/pilot8-subagents.txt`):

| | Builder's session | Reviewer's session |
|---|---|---|
| Governed shell commands, each in its default shell, no access error | 36 | 16 |
| The patch tool (its gate files; the builder's change) | 36 | 16 |
| The clock | 1 | 1 |
| A connector, the web, a browser or desktop tool, any other tool | 0 | 0 |
| The provider's sub-agent tools | 0 | 0 |
| Helios decisions stored under its session | 36, as many as its commands | 16 |
| Requests to leave its sandbox | 2, both allowed: `git add` of the one file, `git commit -m` | none |

**The builder.** It read the line shifts from
`git diff --unified=0 b32fd6f7 HEAD`, moved the references, wrote
subsection 5.8 from four files and committed
(`approval_c2c9fdac75235ab48fc7aceb405a041d`, 09:39:55.471Z,
`git add docs/architecture/orchestration/RUNBOOK.md`;
`approval_d8590635fbe9510f90f2e8762b51c325`, 09:41:08.247Z,
`git commit -m "runbook: update prompts.py line refs, add 5.8 session reach"`).
Of its own checking it wrote: "These checks were my own; this session did
not perform an independent review, which belongs to a separate
assignment."

| Authority, in journal order | Identity |
|---|---|
| Candidate frozen (t-s07, 1632) | CandidateReceipt `sha256:d613496144b0f51de14575379ce25d39038f87579c34b82c1143284929115c49`, tree `b854d40e46ec975d4a42d03d2d81f1c48b3e9c4b` |
| Execution receipt (1633) | `sha256:30870129b8f50c5a68d7ea181360b9bae9d668842924f1e261c6214066555410`, COMPLETE |
| Verification (1637 to 1639), all PASS | role-transform/builder `sha256:0fc48f6f5721b8250157fd46d1cc81ef29fe8e2c40b655bceb576946e94ff652`; a1 `sha256:4be393d399eaa50076953f5c76f93645ae3320daa21885edc196d1af04343d73`; a2 `sha256:4b4ed54f4a9bf4ababf8f4744e028d0d7bd24b231399ce917dc11c1266a9d179` |
| Standing permit granted (1642, 09:45:39.608Z) | one use |
| COMMITTING (t-s15a, 1644) | reservation `rv_1aa9fb786d1667fc1421b79ba122508a`, intent `in_99ba8b3620c2bc3b18eb1faf493363a2`, expected old `8ae0922f…` |
| COMMITTED, point of no return (t-s16, 1647) | CommitReceipt `sha256:513aaf536910cf2d48890b9f5378ae35daf4426b5ec1a64c7d0ae258168ad27e`: `refs/heads/main` observed at `532ac9f89ac4d1c9b4ab1576eb66ee568f94a0f7` |
| ACCEPTED (t-s20, 1648) | AcceptanceRecord `sha256:2d3917e0c31b2fa3e8abf615788dc92ed9854b85180621dae899210a1ad263fd` |
| Dependencies released, COMPLETED (1649, 1650, 09:45:40.081Z) | release `sha256:f42ae7cdd690c9b4579f5c97c8f4e2d6e3fbc1d54ed047d02ddece934e6c19dd`, successor `sq_ceba2785…` |

The published commit `532ac9f8…` has the one parent `8ae0922f…` and
changes the one authorized file, +11 / −3. Its demonstration is
`dem:b5b46ea05665e4ebf3f8a99479d3550607f5fca9f182e60ebcf11c4c3b16b702`,
QUALIFYING.

**The reviewer was given**, checked from the files themselves while its
worktree still existed (2026-11-19T09:47:11Z,
`extracts/obj-pilot8-session-reach-001-staged-inputs.txt`): worktree
`autobot-wt-1niwi_x2` at `532ac9f8…`, the revision the assignment
recorded, clean; `.autobot/inputs/runbook-session-reach.md`, 1706 bytes,
SHA-256 `46c561c99bac1a76393a67d3046eccc1c4d909644752b78a4644ef9f733be5e1`,
the builder's accepted artifact; `.autobot/inputs/README.md`, 803 bytes,
SHA-256 `475378736f452e5e043d27a0c28fd745cb96ceed587d6423d254cd8b6b286eef`,
the recorded note; nothing else. Its sequence was BLOCKED until the
builder's release, READY at 09:46:30.881Z, and its attempt was dispatched
at 09:46:30.990Z, fifty-one seconds after the builder's sequence was
COMPLETED.

**What the reviewer did.** Sixteen commands, all through its shell, all
in its own worktree: the note; the inputs folder; the runbook and the
accepted result; every line of the runbook that cites `prompts.py`; then,
in one command, the whole of `prompts.py`, the three tool lists, `decide`
and `_decide_codex` in `supervision.py`, `config.toml` and `routing.py`.
Seven of the sixteen were admitted and ran; the other nine are the
refusals that precede a gate file. It asked for nothing outside its
sandbox, and it had no other tool to reach for.

**What it returned:**

> AUTOBOT_REVIEW: EXAMINED
>
> No defects found.
>
> I searched the entire runbook for `prompts.py:` references and found
> four, at lines 308, 316, 320, and 324. I read subsection 5.7 at lines
> 314–320 and subsection 5.8 at lines 322–328. The cited prompt content,
> supervision behavior, default route settings and comments, and `_route`
> argument handling match the runbook.

followed by the files and ranges it read: the runbook lines under review,
its two inputs, `prompts.py` 79–100, 102–130, 140–154 and 241–253,
`supervision.py` 76–90, 377–440 and 443–525, `config.toml` 63 and 75–106,
and `routing.py` 93–123, every one at a path inside its own worktree; and
an empty findings block.

| What happened to that result | Identity |
|---|---|
| Collected; execution receipt | `sha256:24b6c578cf5c15f2a5d0a000e84fc43a5518763f2304cbcdf2f8dae6ff2c4e87`, COMPLETE |
| Verdict read from the line and recorded with the assignment | `execution.review` = `{"verdict": "EXAMINED"}` on `asg_8f56da9b…` |
| Verification, all PASS | a1 `sha256:f4ac601af7e0009c58355a36c861a5110be174ac434685b0664a1307dc1e088b`; a2 `sha256:cb53248dfb3a82c9e61838d85eba669c20dc93c9ba0353cb4c4c0b88e1f8f14a`; role-transform/reviewer `sha256:836158f36a334f6c877d4b57abb377cc885e1817bf63666da6711c0d23ad877d` |
| VERIFIED, ACCEPTED (t-s14, 1689), COMPLETED (1691, 09:58:22.480Z) | AcceptanceRecord `sha256:07be66e62ef9945a6b76a696a07ddd2d2ec5f33aecb16322420823e610297d4b`, release `sha256:d991c09ac118ef0227c16452e165f3e71b1e0def9e076ef79e96356cbb903df6` |
| Demonstration | `dem:0a08bfd763d1ced255355764e12c230ff9345ac2bdc872b084f201ce09ddc888`, QUALIFYING, with `review_verdict` EXAMINED among its observations |
| Project completion | `prop:e18786417d2d9121248112e3d82ff4f42b037074d526ca4ceb78c8b1dd8aad4f` (cycle 138, 09:58:22.771Z): "The independent review examined the work against the source files and reported no defects." |

**Was the work right?** Mechanically
(`extracts/obj-pilot8-session-reach-001-facts.txt`), over the runbook's
references to Python source:

| | |
|---|---|
| References before | 149 |
| Expected to stay, and stayed | 142 |
| Expected to move, and moved to exactly the predicted lines | 6, all in `prompts.py` |
| On a line the source change itself rewrote | 1: `prompts.py:115`, now `prompts.py:129`, which is that line |
| Expected by the line shifts and absent | 0 |
| New references | 7 to Python source and 4 to `config.toml`, all in subsection 5.8 (runbook lines 324 to 328) |

And by reading: each place subsection 5.8 cites (`prompts.py:94-100`,
`118`, `129`, `241-249`; `supervision.py:76-79`, `523-525`;
`config.toml:63`, `75-82`, `83-86`, `91-106`; `routing.py:93-123`) holds
what its sentence says.

**What Pilots 7 and 8 establish.** With a project objective that asks for
an independent review in front of it, a builder on the default route did
its own task in its own session, twice, and the second time said in so
many words that its checking was not a review. With the provider's outside
tools switched off by the route, a builder and a reviewer worked with the
shell and the patch tool and nothing else, and the reviewer read the work
and the source in its own worktree. Independent review on the default
route now rests on two runs whose reviewers read everything through the
governed shell, Pilot 6 and Pilot 8. Pilot 7's review examined the work
too, and is counted as that; where it read the source is recorded above
and is the reason the route was changed.

What these runs do not establish is in section 25: the one-session rule
on this provider is still the contract's alone, an MCP server is switched
off by name, and Autobot does not itself read a provider's session record.

## 22. Final state: the Taskforce, the projects, the ledger

Read from the production stores after the last run
(`reports/closure-facts.txt`, `reports/closure-taskforce-report.json`,
`reports/closure-project-report.json`,
`reports/closure-authority-report.json` and the snapshot
`10-after-pilot8`; a consistent copy of both databases as they then stood
is `db/closure-intelligence.db`, SHA-256
`785603772f494f02ac877fb5459978a7a4d31f98c3bd1b38cd3daa73556c73d6`, and
`db/closure-autobot.db`, SHA-256
`d920a39e0966e43e5957f402402112dc5168065207813cde5f5907a8e16dfdc2`). The
reports and copies of the state after Pilot 6, which the previous
revision of this document cited, are beside them under their own names
(`reports/final-*`, `db/final-*`) and were not written over.

### The Taskforce

Four members, four seats, all earned the same way: verified work as an
outside specialist, a QUALIFYING demonstration, and an explicit retention
decision binding it.

| Member | Membership, since | Seat | Retained by, on which demonstration | Assignments through the seat since |
|---|---|---|---|---|
| `sp_aa671ebf47f9b3d37540e0a5ee07256d` (`claude-sonnet`) | `tm_3725eabebe5943956125cbd56e765f8a`, 2026-11-18T06:13:52.725Z | `ts_e5fcdb796547f1982c18496e3e66260a` research/investigator, ESTABLISHED | `dec:69fc58f0ecb3ab500d7389afbf480b0ef8dc5f4d1a2a5b8a72318e3dd96165a6`, on `dem:ad9667c697ce…` (founding run) | 5 |
| `sp_b2f72881be89546de36037d84fea287d` (`claude-haiku`) | `tm_de73f28ceaee60eae40d2b45d880a104`, 13:26:52.304Z | `ts_bddb43b39c785f99430327912e364331` verification/reviewer, ESTABLISHED | `dec:d83b32f45b531da8c7fd0e288ec174906e3d1c7f2d96efa7b1aabdb645b91950`, on `dem:b03230b8d20f…` (Pilot 1) | 9 |
| `sp_17a8384fb1d2fe7756e5db8e7a7072e5` (`claude-opus`) | `tm_1b65882e902b7a5b38279d92cf24a2c2`, 14:06:46.973Z | `ts_c31fff88a7cf30af7a2f73740370f927` documentation/builder, ESTABLISHED | `dec:b42d454675cf0d9a5c8f2e44b3a2f6e4d2c14ad23796e392ae8cfe1dac363f5d`, on `dem:e70535b3f51e…` (Pilot 2) | 7 |
| `sp_5e24b9e318c0f20c9a86748a050243ee` (`specialist-delta`) | `tm_1acdaee2aaef2fe765f131c16cd70de8`, 18:41:50.549Z | `ts_9a3c2dbe5f33568f34944158353466a8` security_review/reviewer, RETAINED | `dec:4ffbcd603269f8bbb9d62a885abc706d8901bb00b11be962ea127de14a490d66`, on `dem:5f02414e7579…` (Pilot 3) | 1 |

| | |
|---|---|
| Workforce decisions | 10: RETAIN 4, ESTABLISH 3 (a seat with repeated verified work), RELEASE_OUTSIDE 3 (a specialist let go after a task with no membership change) |
| Capability demonstrations | 30: QUALIFYING 28, NOT_QUALIFYING 2 (the two attempts the provider failed in Pilot 1) |
| Outside specialists, members of nothing | `specialist-epsilon` `sp_452860f878668e094d86dfcd1b0756c8`: one verified assignment (Pilot 3), released, no membership. `opus` `sp_2f653905288b836bca70b5f75ba329ec`: in the catalog, never assigned |
| A member is outside for work it holds no seat for | `specialist-delta` took part in Pilot 4 as an outside specialist for research and was released for it with its membership untouched |
| Assignments on record | 30: 22 through a retained seat by Taskforce resolution, 8 by the Project Manager's explicit selection of an outside specialist. Outcomes: 28 VERIFIED_ACCEPTED, 2 ATTEMPT_FAILED |

### The projects

Nine, all COMPLETED. Every team was dissolved at completion: 21
participations, all RELEASED, while all four memberships are ACTIVE.

| Project | Objective | Workstreams (attempts) | Team | Budget used |
|---|---|---|---|---|
| `pj_a882418afd31e9d34be1ca9669a432d0` | `obj-founding-driver-wait-001` | 1 (1) | 1 outside specialist | 1 contract, 1 attempt, 1 retention |
| `pj_909c7b27324114ef2719c9473c334d78` | `obj-pilot1-supervision-soundness-001` | 3 (5: two failed by the provider, then recovered) | 1 member, 1 outside specialist | 3 contracts, 5 attempts, 2 repairs, 1 retention |
| `pj_ead01e70c72ef31f187647cf702ec4ae` | `obj-pilot2-operator-runbook-001` | 8 (8) | 2 members, 1 outside specialist | 8 contracts, 8 attempts, 2 retention decisions |
| `pj_372788162c37ef8f69a229beb0d54fbb` | `obj-pilot3-route-migration-001` | 4 (4) | 1 member, 2 outside specialists | 4 contracts, 4 attempts, 2 retention decisions |
| `pj_0dea84d80f0b59c91ec0b893de5dc2c3` | `obj-pilot4-runbook-refresh-001` | 4 (4) | 3 members, 1 outside specialist | 4 contracts, 4 attempts, 1 retention decision |
| `pj_2850006f1ea8dfa1bfe3881ab3c5bc56` | `obj-pilot5-sandbox-edge-001` | 2 (2) | 2 members | 2 contracts, 2 attempts |
| `pj_22fea8d03c0c7a7edbe2ceb192f31a36` | `obj-pilot6-review-confirmation-001` | 2 (2) | 2 members | 2 contracts, 2 attempts |
| `pj_1b5046826b37a1fe6a5dec25f1d6a28f` | `obj-pilot7-one-session-001` | 2 (2) | 2 members | 2 contracts, 2 attempts |
| `pj_9b807ae1b17c9546162ffbcea78b53cd` | `obj-pilot8-session-reach-001` | 2 (2) | 2 members | 2 contracts, 2 attempts |

| | |
|---|---|
| The Engineer's proposals on record | OPEN_PROJECT 9, CREATE_TASK 15, INVESTIGATE 5, REQUEST_REVIEW 9, DECOMPOSE_TASK 9, ASSIGN_SPECIALIST 9, REPAIR_TASK 2, RETAIN_SPECIALIST 4, RELEASE_SPECIALIST 3, WAIT_FOR_AUTHORITY 9, PROJECT_COMPLETE 9 |
| Findings admitted | 94 |
| Permission requests answered by the supervision policy | 55: 51 allowed, 4 denied (a gate file a builder addressed to the wrong folder in Pilot 5, and three a reviewer addressed there in Pilot 7) |
| Reviews with a recorded verdict | 3, all EXAMINED (Pilots 6, 7 and 8). The six earlier reviews were collected before the review contract existed and carry none |

### The ledger

| | |
|---|---|
| Sequences | 29, all COMPLETED: 20 READ_ONLY, 9 SOURCE_MODIFYING |
| Attempts | 31: 29 VERIFIED, 2 FAILED (kept, with their witnesses; nothing was accepted from them) |
| Verification rows | 87, all PASS: two registered assertions and one role-transform check for each of the 29 verified attempts |
| Execution receipts | 29, all COMPLETE |
| Effect intents | 9, all HAPPENED |
| Objects on record, by kind (earlier milestones included) | 42 TaskContractRecords, 44 AttemptContracts, 2 verifier registrations, 1 transform registration, 1 delegation, 1 repository declaration |
| Journal | 1370 entries, highest sequence number 1691 |

Twenty-eight of the sequences and thirty of the attempts belong to the
runs of this document; one SOURCE_MODIFYING sequence
(`sq_95e3a014528c2bf7500691d2f49c1455`) is the 2026-11-17 proof of
section 1. Every specialist-created commit authority published, each by
one compare-and-swap of `refs/heads/main` with an admitted receipt:

| When (UTC) | Sequence | `refs/heads/main` from | to (the specialist's commit) |
|---|---|---|---|
| 2026-11-17T14:55:18 | `sq_95e3a014528c2bf7500691d2f49c1455` | `e3ad4d0f…` | `05e3d1ad91e247cfb767e3c25926f74d3fad9a35` |
| 2026-11-18T14:06:46 | `sq_c0016eef57b0fcd266f27c147534abd3` (Pilot 2) | `5c96da35…` | `e6c88ea05c225231db894a5029290169969d4368` |
| 2026-11-18T14:18:56 | `sq_00032bce9c68528d6fa7de35158869ed` (Pilot 2) | `e6c88ea0…` | `f53df432a9cb6990e5748c0d50cfa9e33a1f82d2` |
| 2026-11-18T14:27:00 | `sq_6815520800bbe5c13821a032e94b1055` (Pilot 2) | `f53df432…` | `5894d5e2e7af31016898b7916e655b3fb9c166a9` |
| 2026-11-18T19:54:12 | `sq_f42a9a651d6a8a3e4aa51fbd02ee8a63` (Pilot 4) | `1732507c…` | `606663ade00219a4b2c1baef0df0aa81dc510067` |
| 2026-11-18T21:19:18 | `sq_ff709a6a85cee21328a10c8ad812aafa` (Pilot 5) | `d01929ff…` | `4abc57c249695748a3f5e56054e3eb82577fc1cc` |
| 2026-11-19T03:57:55 | `sq_fcb4891735a4bf214f72a73e0e5ef289` (Pilot 6) | `816aab4e…` | `53667c52daa3d8ea5d60ccfd883b8aa5d18513b5` |
| 2026-11-19T08:25:54 | `sq_18f8a8a626d71baa037f13cdeb17e703` (Pilot 7) | `d320d1bf…` | `b32fd6f7c0a75567e6a523b0ca4be733305f454e` |
| 2026-11-19T09:45:39 | `sq_14173b1bb0550ee226f6c9805e1660ea` (Pilot 8) | `8ae0922f…` | `532ac9f89ac4d1c9b4ab1576eb66ee568f94a0f7` |

Each of those commits is on `main` unchanged: none was rebased, squashed
or rewritten on its way to the remote.

## 23. Separation: authority, the organization, the project

Twelve checks, each read from the final production state through
read-only connections, each reporting the facts it rests on
(`reports/closure-separation-proof.json`). All twelve hold.

| # | What holds | Resting on |
|---|---|---|
| 1 | Every assignment stands on a Registry-admitted record and a Sequencer-minted AttemptContract, whoever did the work | 30 assignments |
| 2 | Every accepted attempt has a COMPLETE execution receipt and PASS on every registered assertion, and the assertion set is the same for retained members and outside specialists | 28 accepted attempts, across three standings (OUTSIDE, RETAINED, ESTABLISHED) |
| 3 | Failed attempts stay in the ledger with their witnesses, and nothing was accepted from them | 2 FAILED attempts |
| 4 | No TaskContractRecord has a provider, model, route, specialist, seat or membership key, or a value that is one of those identities | 28 records, 15 keys and 20 values refused |
| 5 | Outside specialists hold project assignments without Taskforce membership | 7 assignments made to a specialist that was no member at the time; one specialist with verified work is a member of nothing today |
| 6 | Every membership was created by an explicit RETAIN decision binding that specialist's own QUALIFYING demonstrations | 4 memberships |
| 7 | A completed project has no open participation or assignment, and its members' Taskforce membership persists | 9 projects |
| 8 | Retained members take part in more than one project | `claude-haiku` 7, `claude-opus` 6, `claude-sonnet` 5, `specialist-delta` 2 |
| 9 | Retention then reuse: the same specialist worked first as an outside specialist and later, verified, through its retained seat by Taskforce resolution | all 4 seats |
| 10 | No membership, seat, workforce decision or demonstration identity appears in the authority journal | 48 identities searched for in 1370 entries, 0 found |
| 11 | No review was done by a specialist that did the reviewed work | 9 reviews |
| 12 | A review records whether it examined the work; one that did not earned no qualifying demonstration; no project closed on a record whose only reviews were NOT_EXAMINED | 9 reviews: 3 with a recorded verdict (EXAMINED: Pilots 6, 7 and 8), 6 collected before the review contract existed |

Check 12 is the one PR 26 made possible, and it says exactly as much as
the records do. Pilot 5's review is among the six that carry no verdict:
it predates the contract, so nothing in the stores marks it as the review
that examined nothing. Section 21 does.

A recorded verdict also does not say where a reviewer read. Pilot 7's
EXAMINED review read its source through a provider connector, and nothing
in the stores shows that either: it was read from the provider's session
record (section 21). The verdict is the reviewer's statement that it read
the work and what the work must agree with. What a session may read with
is the contract's and the route's (D-TF-35).

The six identities of the model are six separate records, and each is a
function of what it names and of nothing else (recomputed for every row,
section 24):

| Identity | A function of |
|---|---|
| TaskContractRecord | the frozen content: capability, role, objective, constraints, scope. No provider, model or specialist |
| ProjectAssignment | the sequence and the attempt ordinal |
| Specialist | its handle |
| TaskforceMembership | the specialist |
| TaskforceSeat | the specialist, the capability and the role |
| CapabilityDemonstration | references to authority records of one assignment |

## 24. Execution routing and model replacement, from the records

### Who ran on what

Each assignment's route is read three ways and they agree: the route
recorded with the assignment, the ExecutionManifest AutoSwarm persisted,
and AutoSwarm's own record of what it launched
(`reports/closure-route-proof.json`, 30 assignments).

| Who | Live identifiers | Assignments |
|---|---|---|
| Engineer, and the Engineer as Project Manager | provider `claude-code`, model `claude-opus-4-6[1m]`. Every run prints "engineer: reasoning-engineer/v1 over model claude-opus-4-6[1m]", and the provider's own usage record named that model in the probe (section 17) | every reasoning cycle of every run |
| Specialist sessions since the routing change: retained members, outside specialists, independent reviewers | route `luna-6-max`, served as the default: AutoSwarm provider `codex`, model `gpt-6-luna`, reasoning effort `max` | 15: Pilot 3 (3), Pilot 4 (4), Pilots 5, 6, 7 and 8 (2 each) |
| One assignment the Project Manager asked to escalate (`high_context`) | route `opus-4-6-1m`, served as the escalation: `claude-code`, `claude-opus-4-6[1m]` | 1: Pilot 3 `identity-review`, `asg_4baa11edb6bf98f722fe4923baff5188`, call `call_22464338956d9e4bbaf2e93273ce0460`, session `31402c3b-6946-459b-80bf-ff4b19bd54b3` |
| Specialist sessions before the routing change | `claude-code` with the model aliases `sonnet` (4), `haiku` (6), `opus` (4) | 14: the founding run, Pilots 1 and 2. Unchanged history |

An independent reviewer is not a route. It is a separate session on
whatever route its assignment resolves to: in Pilots 4 to 8 the default
one, under its own call and session id.

Since PR 26 the route is recorded with the assignment before the ledger
dispatches the attempt, session arguments included. Pilot 6 shows it: read
while the builder's attempt was still executing and had no result
(operator gate `20261119-taskforce-273`), its assignment already carried
`{"name": "luna-6-max", "provider": "codex", "model": "gpt-6-luna",
"effort": "max", "provider_args": […], "served_as": "default"}`. Since
PR 29 those recorded arguments include the eleven overrides that switch
the provider's outside tools off; both assignments of Pilot 8 carry them,
and AutoSwarm's call records show them on the provider's command line
(section 21).

### The effort travels beside the manifest

The ExecutionManifest names a provider and a model
(`provider_assignment`) and nothing else about the route. Its schema is
vendored from AutoSwarm and is byte-identical across every change of this
document:

| Revision | `src/autobot/execution_boundary/schemas/execution_manifest.schema.json` |
|---|---|
| `05e3d1ad…` (before this work) | blob `5a68e9d67cb1504f1baedfd2f4ccff9634fa4a0c` |
| `518b59f5…` (PR 23, before routing) | the same blob |
| `1732507c…` (PR 24, routing) | the same blob |
| `d01929ff…` (PR 25) | the same blob |
| `816aab4e…` (PR 26) | the same blob |
| `9d97a34d…` (PR 27), `d320d1bf…` (PR 28), `8ae0922f…` (PR 29) | the same blob |
| the commit that carries this document | the same blob |

The last commit that touched the file is `4037effa…`, of 2026-11-17,
before any of this. Of the 30 manifests AutoSwarm holds for these
assignments, none has an `effort` key anywhere in it. The effort is in the
route recorded with the assignment and in AutoSwarm's own call record,
which the Taskforce stages for the call and the transport passes through:
all 15 `codex` calls were launched with `effort: max`; the 15 Claude Code
calls with none. The route's session arguments travel the same way, and
so do the overrides of PR 29: no manifest names one.

The supervision policy is as stable. `src/intelligence/supervision.py` is
the blob `d92a5d1bd9dd435be0e646ac773d918e20994266` at PR 25 and at every
revision since; the last commit that touched it is `b50fb746…`, the
narrowed sandbox-edge rule. Neither of the two findings of section 21
changed it.

### Model replacement under unchanged identity

Three members ran on one model family for their first assignments and on
another afterwards. Nothing that identifies them changed.

| Specialist | Membership | Seat | On Claude Code | On `codex` / `gpt-6-luna` / `max` |
|---|---|---|---|---|
| `sp_aa671ebf47f9b3d37540e0a5ee07256d` | `tm_3725eabebe5943956125cbd56e765f8a`, since 2026-11-18T06:13:52.725Z | `ts_e5fcdb796547f1982c18496e3e66260a` | 4 assignments on `sonnet` (founding run, Pilot 1 twice, Pilot 2) | 2 (Pilots 3, 4) |
| `sp_b2f72881be89546de36037d84fea287d` | `tm_de73f28ceaee60eae40d2b45d880a104`, since 13:26:52.304Z | `ts_bddb43b39c785f99430327912e364331` | 6 on `haiku` (Pilot 1 three times, Pilot 2 three times) | 5 (Pilots 4 to 8) |
| `sp_17a8384fb1d2fe7756e5db8e7a7072e5` | `tm_1b65882e902b7a5b38279d92cf24a2c2`, since 14:06:46.973Z | `ts_c31fff88a7cf30af7a2f73740370f927` | 4 on `opus` (Pilot 2) | 5 (Pilots 4 to 8) |

Each membership and each seat was created before the routing change and
has the same id after it. The fourth member, `specialist-delta`, has only
ever run on the default route; the one assignment of `specialist-epsilon`
ran on the escalation route.

What is new with every attempt, whoever runs it and on whatever route: an
AttemptContract, an ExecutionManifest, a call and a session. What a route
change leaves alone, checked over every record
(`reports/closure-route-proof.json`, five checks, all hold):

| What holds | Resting on |
|---|---|
| Specialist, membership, seat and assignment identities are functions of the handle, the specialist, the capability and role, the sequence and ordinal, and none of a route | recomputed for 6 specialists, 4 memberships, 4 seats, 30 assignments |
| A specialist's execution route was replaced while its identity, membership and seats stayed what they were | the three members above |
| No TaskContractRecord has a provider, model, effort, route or specialist key, or a value that is a route identifier | 28 records |
| No demonstration, workforce decision, membership or seat carries a route word | 30 demonstrations, 10 decisions, 4 memberships, 4 seats |
| Every review ran as a different specialist and a different call than the work it reviewed | 9 reviews |

The Engineer took no part in any of this. It is not shown handles,
providers, models or routes, and the one thing it said about execution
depth in all nine projects was a word about the need: `high_context`,
once, with its reason (section 18).

## 25. Restart and recovery observations, and what production found

Sorted by kind, with the actual effect of each on this proof.

### Restart and recovery

| Observation | Evidence |
|---|---|
| A run that ended with its objective active was continued hours later by a new process, on the same objective and the same project, from the ledger alone | Pilot 1: first process ended 2026-11-18T06:19Z with exit code 2; the resume started 13:23Z and completed the project at 13:28:57Z (section 4) |
| Attempts the provider failed stayed in the history and were recovered on the Engineer's word, by the registered retry arc, on new AttemptContracts | `at_69fc5b024cdf0bcb66620da5722d240d` and `at_6f1c7cacdb066551cd7b2c753c143329` FAILED; `at_4dff02b13777890439ae2b628f482aa8` and `at_f8a92ebd4c077c1e48b1a2ccaba47872` VERIFIED on the same two sequences |
| The reasoning model failing once inside a cycle did not lose the cycle | Pilot 2, cycle 68: the first model call exited 1, the second answered; cycle 60: the first answer failed validation ("requirement.finalization_class must be one of ('READ_ONLY', 'SOURCE_MODIFYING')") and the model was asked again with the problem |
| A session that hangs is not recovered | Not exercised, and not implemented: F-TF-23 below. The recovery proved here is recovery from a failure the provider reports |

### Implementation defects (found in production, fixed, tested, merged)

Each row is one chain: a run exposed it, it was analysed against the
source, corrected, pinned by tests and by a mutation where it is a rule of
the model, published, and then met again in a later production run.

| Finding | What was wrong | Effect on the pilots | Fix | Later production run |
|---|---|---|---|---|
| F-TF-15 | The pump never asked for the registered retry arc (t-s08), so a sequence whose attempt the provider failed stayed EXECUTING | Pilot 1 could not continue after the provider failure | PR 21: the pump asks for t-s08; the next attempt needs the Engineer's word. Mutations 22, 24, 25 | Pilot 1's resume: both sequences recovered and completed |
| F-TF-16 | The Engineer was not shown why an attempt failed | A provider failure would have looked like the specialist's | PR 21: the provider's own report is recorded and shown, uninterpreted. Mutation 23 | Pilot 1's resume |
| F-TF-17 | A started objective's budget could not be changed from the command line | The budget the failed attempts spent could not be restored | PR 21: budget flags on a resume | Pilot 1's resume |
| F-TF-18 | Accepted READ_ONLY results were not delivered to dependent work | Pilot 1's independent review did not receive the trace it was to review; the reviewer said so and verified the question from source | PR 22: accepted predecessor results are placed in the dependent's isolated worktree. Mutation 26 | Pilot 2 and every dependent workstream since |
| F-TF-19 | The catalog pinned each specialist to a provider and model, and a route's effort and session arguments never reached AutoSwarm | The organization had no default route to change | PR 24: the routing policy. Mutations 27 to 29, 31, 34 | Pilots 3 to 8 |
| F-TF-20 | The READ_ONLY contract named one provider's tools and forbade the shell | A provider that reads through a shell could not have done READ_ONLY work | PR 24: one provider-neutral contract | every READ_ONLY session of Pilots 3 to 8 |
| F-TF-21 | An assignment kept the route its specialist had when it was selected, not the one it was dispatched on | None observed; a reroute between selection and dispatch followed by a restart would have relaunched on another model | PR 24: the dispatched route is recorded and repeated. Mutation 30 | every assignment of Pilots 3 to 8 records its route |
| F-TF-25 | RUNBOOK.md cited source by line and PR 24 moved those lines | The operator's document pointed at the wrong lines | Refreshed through the organization, not by a patch: section 19 | Pilot 4 itself; again in Pilots 6, 7 and 8, after PRs 26, 28 and 29 each moved cited lines |
| F-TF-26 | The sandbox-edge permission rule PR 24 added read a command by its first two words, accepted any program named `git`, granted path-capable Git commands without looking at their paths, and accepted a working directory that was only spelled inside the workspace. Found by Pilot 3's reviewers | None: the only two requests production had raised were `git add` of the one authorized file and `git commit -m` (section 19) | PR 25: the command that would run is judged; two commands only (section 20). Mutations 32, 35 to 38 | Pilots 5 to 8: each builder asked for `git add` of its one file and `git commit -m` and was allowed both; the four file changes sessions asked for outside a sandbox were refused (one in Pilot 5, three in Pilot 7) |
| F-TF-27 | `_relative_inside` accepted a path when either its written or its resolved spelling lay inside the root. Found by Pilot 1's specialists, and again by Pilot 3's | None observed: no session created a link | PR 25: a location is judged by where it resolves. Mutation 33 | not met: no session has created a link |
| F-TF-28 | Four routing defects reported by Pilot 3's specialists: the route of an attempt was recorded after dispatch; a relaunch took a route's session arguments from the live policy; staged session configuration was keyed by call id alone; a placeholder that was not lower-case letters in braces passed validation | None: no production run met any of the four conditions | PR 26, all four, each first confirmed by a test that fails without its fix. Mutations 45 to 47 | Pilot 6: each assignment's route, session arguments included, is on record while its attempt is still executing (section 21) |
| F-TF-29 | The reviewer of a SOURCE_MODIFYING change was not told that its worktree held the reviewed change, and was not given what the builder worked from | Pilot 4's review was partial, and said so | PR 26: the note, and the reviewed work's own inputs (D-TF-33). Mutations 41, 42 | Pilot 6 (section 21) |
| F-TF-30 | A review that could not read the work was accepted, credited and allowed to close its project. The reviewer had set its command tool to a shell that cannot start for it | Pilot 5's project was completed unreviewed | PR 26: the shell rule and the review verdict (D-TF-31, D-TF-32). Mutations 39, 40, 43, 44, 48 | Pilot 6 (section 21) |
| F-TF-31 | Nothing said that an assignment is one specialist's own work in one session. A builder was shown a project objective that also asked for an independent review, and its provider offers every session tools that start sub-agents | Pilot 6's builder started one, named it `independent_review` and reported its check in those words. No guarantee of the authority plane was touched, and the organization's own review was done separately | PR 28: the one-session rule in every execution contract (D-TF-34). Mutations 49, 50. On the default route it is the contract's alone: the provider's switches do not remove the tools | Pilots 7 and 8 (section 21): no session called a sub-agent tool |
| F-TF-32 | Nothing said that an assignment reaches nothing outside the machine's files and its shell, and a session of the default route's provider inherits the operator's own provider configuration: 158 tools, a GitHub connector among them | Two reviewers read source through the connector (Pilot 3 `argument-review`, Pilot 7 `runbook-review`): reads only, of the revision they were to read. Nothing was written outside a sandbox | PR 29: the default route's session arguments switch the tools off (158 to 5), and every execution contract forbids them (D-TF-35). Mutations 51, 52 | Pilot 8 (section 21) |

### Runtime and provider limitations (not defects of Autobot)

| Observation | Effect |
|---|---|
| The provider's usage window ran out at 2026-11-18T06:18Z, while Pilot 1's first two sessions were starting and during the Engineer's next reasoning call | Two attempts FAILED and one reasoning cycle produced nothing. The run ended with exit code 2 and the objective active. The work was resumed on the same objective and project seven hours later and completed |
| A reasoning model that cannot be reached answers nothing; the stimulus of that cycle is consumed | The loop goes quiet and stays quiet until it is resumed. `engineer` without `--objective` is the resume, and it is a stimulus |
| The Engineer and every Claude Code session draw on one subscription; Codex sessions draw on another | A long autonomous run and its operator compete for the same windows |
| Inside the Codex sandbox a linked worktree cannot stage or commit (F-TF-22) | The route relays requests and the supervision policy grants Git bookkeeping for the assignment's own worktree |
| On this machine every shell command of a Codex session passes the Helios gate, and a Codex session reads through the shell | Work on the default route is correct and slow: each command costs a gate handshake. Two reviewers found a faster way that passes neither the gate nor the sandbox, a provider connector; the route has switched it off since PR 29 (F-TF-32) |
| A session of the default route's provider inherits the operator's own provider configuration: 158 tools in its registry, 141 of them provider-side apps | No assignment grants them. The default route's session arguments now switch them off for the session (5 tools left). An MCP server is switched off by name; one configured later needs its own line on the route |
| The default route's provider offers every session six sub-agent tools, and neither of its two multi-agent switches removes them when given as a session argument | On that route the one-session rule is the contract's alone (F-TF-31) |
| On this machine `bash` is the WSL launcher, and WSL refuses a sandboxed process | A Codex session that selects `bash` gets "Bash/Service/CreateInstance/E_ACCESSDENIED" for every command. The contract now keeps a session in its default shell (F-TF-30). The host is as it was |
| Helios classes an interpreter run on inline code (`python -c`) as tier 3 | A specialist cannot run one; the contract says so |

### Evidence-quality observations

| Observation | Effect |
|---|---|
| Pilot 5's review examined nothing, was accepted, earned its reviewer a QUALIFYING demonstration (`dem:5c6f07f8…`) and let its project complete (section 21) | Those records stand as they were written. Pilot 5 is not counted as an independent review anywhere in this document. The reviewed text was checked directly, outside the organization, and by Pilot 6's reviewer |
| Pilot 6's builder reported a check by a sub-agent of its own session as "an independent review" (F-TF-31) | The words are the builder's and are in its accepted result as written. The independent review this document counts is the organization's, by another specialist in another session (section 21) |
| Two accepted reviews read their source through a provider connector, from the remote repository: Pilot 3's `argument-review` and Pilot 7's `runbook-review` (F-TF-32) | Both results stand as accepted. What each read was the content of the revision it was to read (sections 18 and 21), and Pilot 7's reviewer said where it had read it. Neither read is in the attempt's command evidence, which holds shell commands only. Independent review on the default route rests on Pilot 6 and Pilot 8, whose reviewers read everything through the shell |
| The command traces this document printed for earlier runs listed shell commands only | The connector reads of Pilot 3 were therefore not seen until every session was read for every tool it called (`extracts/tool-census.txt`) |
| Pilot 4's independent review was partial (F-TF-29): the reviewer verified the new sections and spot-checked the references, and said it could not compare exhaustively | The review was accepted as what it was. The exhaustive comparison was made mechanically, outside the organization, and is recorded as that (section 19) |
| The two demonstrations of the provider-failed attempts were recorded NOT_QUALIFYING before F-TF-16 was fixed and carry no provider report of their own | They are counted as non-qualifying work of those specialists. Project state reads the provider's report from the witnesses. Nobody was released, and both specialists were selected again |
| The repository has no CI status checks | PRs 20 to 29 were merged, and this change is offered, on local regression evidence cited by gate in each commit message |
| Some command gates of the operator session that built and ran this proof declare a creation time that is later than the moment their command ran: 70 of 424 | Nothing that decides anything reads that field: no time-to-live check, no ordering, no authority transition, no receipt, no verification. The impact is 70 gate files with a wrong creation time in the operator session's own audit trail. Detail under "Operator gate timestamps" below |
| Two operator commands of the last stretch produced no evidence: gate 376 ran no test because its selection file was malformed, and one command that differed from its gate by an undeclared pipe was refused by the hook before it ran | Neither is cited anywhere. The runs cited for PR 29 are the ones that followed (377 to 385) |
| The first form of the route-replacement check searched records for route words and reported one false positive: the word "codex" inside a task description that names the function `_decide_codex` | The check was rewritten to look at a record's keys and at values that are route identifiers; it holds for every record (sections 23 and 24) |
| The isolated worktree the 2026-11-17 pilot left behind (`autobot-wt-udfyfkex`) is still in the checkout | Not touched. Every worktree created since was removed when its assignment or probe settled |
| Pilot 2, cycle 74, recorded two `WAIT_FOR_AUTHORITY` proposals: the model's own and the policy's | Both closed; no effect |
| The handles `claude-sonnet`, `claude-haiku` and `claude-opus` name specialists that have run on `codex` / `gpt-6-luna` since the routing change | A handle is an identity, not a route (section 17). Each assignment records the route it ran on |

**Operator gate timestamps.** Every shell command of the operator session
that built and ran this proof passed the Helios command gate, under
correlation ids `2026111x-taskforce-NNN`. Each gate file declares a
`created_utc`. For 70 of the 424 gates that had a result record when the
final audit ran (operator gate `20261119-taskforce-427`), the session
estimated that time forward instead of reading a clock, and the declared
time is later than the moment the hook recorded the command began. The
70 are the same 70 the audit found after Pilot 6; none of the gates
written since is among them:

| Day | Gates | Declared time ahead by |
|---|---|---|
| 2026-11-17 | 13: 003 to 015 | 3 minutes to 3 hours 4 minutes |
| 2026-11-18 | 52: 027, 039 to 043, 051, 052, 054, 060 to 062, 072 to 078, 081, 093 to 098, 129, 142 to 145, 159 to 170, 190, 191, 199 to 205 | under a second to 7 minutes 49 seconds |
| 2026-11-19 | 5: 256 to 260 | 2 to 54 seconds |

What that field takes part in, from the sources:

| Question | Answer |
|---|---|
| Time-to-live validation | No. Helios validates `expires_utc` and rejects an expired gate (`gate_check.ps1:440-448`). It never reads `created_utc` from a gate |
| Ordering | No. A gate is linked to its predecessor by `previous_correlation_id` being present (`gate_check.ps1:804-811`), not by time |
| Authority transitions | No. Operator gates are never admitted to the authority ledger. For a specialist's governed command, authority recognizes the hook's own `executed_utc` from the evidence record, never a gate's declared creation time (`witness_semantics.py:45-54`) |
| Receipt identity | No. No receipt is computed over an operator gate file |
| Verification | No |

The actual impact is on the audit trail of the operator session and
nowhere else: those gate files state a creation time that is wrong, and
sorting them by it would misorder them. Beside each one, the result record
the hook wrote carries the true execution time, and Git and the ledger
carry their own. The gate files are preserved exactly as they were
written. `reports/closure-gate-timestamp-audit.txt` in the evidence bundle
lists every affected gate with both times (the audit as it stood after
Pilot 6 is beside it as `reports/gate-timestamp-audit.txt`). The largest
lead is 11,040 seconds, on gate `20261117-taskforce-014`. The clock has
been read from the gate itself since gate 206 of 2026-11-18; the five
gates of 2026-11-19 are where seconds were again added to a reading by
guess. From gate 261 of that day on, the declared time is exactly the last
value read: a bound the clock had already passed.

### Engineering findings the organization produced, still open

The specialists' findings are the organization's own verified output
about its own code. Nothing in this change set addresses the ones below.

| Finding | Class | Statement |
|---|---|---|
| Pilot 1: `fnd:033cfdf77d40e226607cc5…`, `fnd:b7fa591425e9d99af182a1…`, `fnd:e49c8d9ea394d61d11990b…` | verification_gap, security_issue | For Claude Code sessions, shell tools are allowed with no scope check wherever the contract grants a shell; a shell write is bounded by Helios (cap-20) and by the candidate scope check after the fact, not by the supervision policy |
| Pilot 1: `fnd:f12cd622525d6b158254fd…`, `fnd:d7d3af86663be171efff90…` | implementation_defect | The workspace used as the write boundary is taken from the request, not from the frozen record or the launch |
| Pilot 1: `fnd:360a670a0f3e547d60d8e5…` | verification_gap | Several write-allow branches of the policy have no test |
| Pilot 1: `fnd:9f7734c384ce8af6f2b220…` | implementation_defect | A Glob call with no path is allowed and its pattern is not checked |
| Pilot 2: `fnd:92e5bd96f0696b3390c84e…`, `fnd:141e6c9f34c511257dbe23…` (F-TF-23) | missing_requirement | `Sequencer.mark_member_stale` has no caller: a provider session that hangs is never failed, and holds every run until `--max-wall-seconds`. Confirmed in source |
| Pilot 2: `fnd:989e5dc77558159577f849…` (F-TF-24) | implementation_defect | An attempt the Sequencer refuses to start leaves an ABANDONED assignment that `allocate_ready` then skips for good. Read in source; not confirmed by a test |
| Pilot 2: `fnd:5814bdf27021abb3dfc47d…` (F-TF-24) | documentation_mismatch | A non-zero exit of `autoswarm run` escapes as an uncaught error and leaves the attempt DISPATCHED |
| Pilot 2: `fnd:043c449f34014e0c0cf0c4…` (F-TF-24) | documentation_mismatch | A negative budget on a fresh run leaves the objective ACTIVE behind a traceback |
| Pilot 2: `fnd:7b10147f119e78efe55490…`, `fnd:5a224742360bb938efb592…` (F-TF-24) | documentation_mismatch | `--authorize-publication` grants a permit to every VERIFIED sequence of an active objective, and `--max-wall-seconds` bounds waiting per round, not the run; the help text says less |
| Pilot 3: `fnd:bc96fcc6835867944d13e5…` | verification_gap | A specialist registered with no handle is given one made from its provider and model, so its identity then derives from them. Every specialist in the production catalog has a handle |

Two more were read from session records, not reported by a specialist.
Both are fixed as far as Autobot owns them (the table of defects above).
What stays open of each:

| Finding | What stays open |
|---|---|
| F-TF-31 | On the default route's provider the sub-agent tools cannot be switched off by session argument, so the one-session rule there is the contract's alone. Two production runs kept it; nothing makes a third keep it. A sub-agent's reasoning would be a second provider session file that authority does not collect |
| F-TF-32 | An MCP server is switched off by name, so one the operator configures later is covered by the contract alone until its line is added to the route. What the route's arguments switch off is the provider's behaviour, established by a probe, and has to be probed again when the provider changes |
| Both | Autobot does not itself read a provider's session record. Whether a session kept either rule is established after the fact, by reading that record, as this document did |

Three findings Pilot 1 reported about `_relative_inside`
(`fnd:2784853fa48adcb8650db6…`, `fnd:266633c603cb1e10d535a0…`,
`fnd:0f6e501232c70fa8b8fcdb…`) and the three Pilot 3 reported about the
sandbox edge are fixed (F-TF-26, F-TF-27, PR 25). The four Pilot 3
reported about routing (`fnd:50c4d3654fb8d44fd22393…`,
`fnd:d17406f9d5e714fcf7c859…`, `fnd:1b4b53755757500f087b40…`,
`fnd:451997b7ff9c1bedbd5330…`) and the one Pilot 4 reported about what a
reviewer is told (`fnd:b0f182d0e32919c7ac610f…`) are fixed (F-TF-28,
F-TF-29, PR 26).

The first row is the design as documented (section 6.3 of
ARCHITECTURE.md): the frozen scope is enforced by the Git adapter before
a candidate is frozen, and Helios governs each command. The rest are real
gaps or loose documentation. None of them lets work outside the frozen
scope become ACCEPTED state: the candidate scope check and verification
stand behind every one. The operator-facing ones are written into
RUNBOOK.md.

## 26. Final regression

Run on the source and test bytes of the commit that carries this
document: its `src` and `tests` trees are the objects these runs read.
They are those of `456c9f51…` (PR 29) and of `532ac9f8…`, the commit the
last production run published, which changes the runbook only; this
commit adds documentation only, and no test reads a documentation file it
changes. The operator gate of each run is given, and again in that
commit's message. The repository has no CI status checks, so this local
record is the regression evidence.

| Leg | Interpreter and runner | Result | Gate `20261119-taskforce-` |
|---|---|---|---|
| Whole suite, `tests/autobot/` | Python 3.8.10, pytest 8.3.5 | 771 passed, 0 failed | 378 |
| Whole suite, `tests/autobot` | Python 3.12.11, unittest discovery | 770 tests, OK | 379 |
| Legacy coordinator, `tests/test_coordinator.py` | Python 3.12.11, unittest discovery | 41 tests, OK | 383 |
| Architecture mutations | Python 3.8.10 | 52 of 52 killed | 384 |
| Architecture conformance, `test_architecture_conformance.py` | Python 3.8.10, pytest | 20 passed | 385 |
| Routing conformance, `test_model_routing.py` | Python 3.8.10, pytest | 46 passed | 385 |
| Supervision and sandbox: `CodexSupervisionTests`, `SupervisionPolicyTests`, `ReviewerAuthorityTests` | Python 3.8.10, pytest | 23 passed | 385 |
| Independent review, `test_independent_review.py` | Python 3.8.10, pytest | 20 passed | 385 |
| Transport, `test_autoswarm_transport.py` | Python 3.8.10, pytest | 34 passed | 385 |
| Project orchestration, `test_taskforce_orchestration.py` | Python 3.8.10, pytest | 42 passed | 385 |

The two whole-suite totals differ by one because pytest also collects a
helper function named `test_modules` in `test_mutation_matrix.py`, which
unittest discovery does not.

The tests that pin each defect production found and a change set
repaired, run on their own (Python 3.8.10, pytest, the same gate as the
focused groups):

| Finding | Tests | Result |
|---|---|---|
| F-TF-15, F-TF-16: a provider-failed attempt is recoverable, on the Engineer's word, with the provider's report in view | `test_taskforce_orchestration.py::ProviderFailureTests`; `test_chief_engineer_policy.py::ReasonedLoopTests::test_the_engineer_recovers_from_a_provider_failure` | 5 passed |
| F-TF-17: the budget of a started objective, and the resume signal | `test_authority_runtime.py`: `test_an_active_objectives_budget_is_yours_to_change`, `test_a_resume_is_a_stimulus_for_the_engineer` | 2 passed |
| F-TF-18: dependent work receives its predecessors' accepted results | `test_taskforce_orchestration.py::PredecessorResultTests` | 1 passed |
| F-TF-19, F-TF-21: the routing policy; the dispatched route recorded and repeated | `test_model_routing.py`: `CatalogRoutingTests`, `RouteReplacementTests`, `LunaDefaultTests` | 13 passed |
| F-TF-19: a route's effort and session arguments reach the session | `test_autoswarm_transport.py`, the four route tests | 4 passed |
| F-TF-26, F-TF-27: the sandbox edge; a location is where it resolves | `test_model_routing.py::CodexSupervisionTests`; `test_taskforce_workforce.py::SupervisionPolicyTests` | 18 passed |
| F-TF-28: the route across a stop, its arguments, session configuration, placeholders | `test_model_routing.py::RouteAcrossAStopTests`; `RoutingPolicyTests::test_a_placeholder_no_session_can_fill_is_refused_however_spelled`; `test_autoswarm_transport.py::WorkspaceIsolationTests::test_a_session_configuration_is_applied_only_to_its_provider` | 4 passed |
| F-TF-29: what a dependent session is told and given | `test_independent_review.py`: `InputsNoteTests`, `ReviewFlowTests::test_the_reviewer_is_given_the_work_what_it_was_built_from_and_a_note` | 3 passed |
| F-TF-30: a review that could not read the work | `test_independent_review.py`: `ContractTests`, `VerdictTests`, `ReviewFlowTests` | 13 passed |
| F-TF-31: one assignment, one session | `test_independent_review.py`: `ContractTests::test_every_contract_keeps_the_work_in_one_session`, `ReviewerAuthorityTests::test_no_session_is_granted_a_tool_that_starts_another_agent` | 2 passed |
| F-TF-32: a session stays on this machine | `test_independent_review.py`: `ContractTests::test_every_contract_keeps_a_session_to_this_machine`, `ReviewerAuthorityTests::test_no_session_is_granted_a_tool_that_reaches_outside` | 2 passed |

F-TF-20 (one provider-neutral READ_ONLY contract) is pinned by the prompt
tests in the whole suite and was exercised by every READ_ONLY session of
sections 17 to 21.

What the tests of the last two rows do not show, and are not meant to:
that the default route's provider keeps either rule. Its sub-agent tools
cannot be switched off, and what its other switches remove is the
provider's behaviour. Those are shown by the probes of section 21 and by
Pilots 7 and 8, where every tool call of every session was read.

The architecture mutations break one rule of the model at a time, in
memory, and count a mutation as killed only when a test that claims to
pin that rule fails. There are 52. The eight that concern the sandbox
edge and a reviewer's authority: any Git command may leave the sandbox; a
location is judged by how it is spelled; a script is split on blanks
whatever else it holds; any directory is the assignment's worktree; any
program may be the shell; `git add` may stage anything; supervision
allows every request; a reviewer's session is supervised as a builder's.
The six that concern what a review is: a review that could not read the
work is recorded as one; an unexamined review lets its project complete;
a dependent session is told nothing about its workspace; a reviewer is
not given what the reviewed work was given; the contract lets a session
choose any shell; a review need not say whether it examined the work.
The four that concern what a session is: the contract lets a session hand
its work to sub-agents; a provider that asks may start another agent; the
contract lets a session use whatever its provider offers; a provider that
asks may use a connector or the web.

What the independent-review tests pin, by name
(`tests/autobot/test_independent_review.py`):

| Requirement | Test |
|---|---|
| A reviewer can read the source and the accepted inputs, and valid reads succeed | `ReviewerAuthorityTests::test_a_reviewer_reads_what_a_review_needs_and_writes_nothing` |
| A reviewer is given the accepted work, what that work was given, and the note, by the addresses the ledger holds | `ReviewFlowTests::test_the_reviewer_is_given_the_work_what_it_was_built_from_and_a_note` |
| A reviewer is another specialist in another session, on a READ_ONLY contract | `ReviewFlowTests::test_the_reviewer_is_another_specialist_in_another_session` |
| A reviewer cannot acquire a builder's authority | `ReviewerAuthorityTests::test_nothing_a_reviewer_asks_for_leaves_the_sandbox` |
| The builder's rule is intact: only `git add` in scope and `git commit -m`; a compound command, a junction out of the worktree and a misplaced gate-file write are refused | `ReviewerAuthorityTests::test_the_builders_rule_is_what_it_was`, with `CodexSupervisionTests` in `test_model_routing.py` |
| A review that could not read the work is recorded as that, earns no qualifying demonstration and cannot close its project | `ReviewFlowTests::test_a_review_that_could_not_read_the_work_is_not_a_review` |
| A review that examined the work counts, and its verdict is what the reviewer stated | `ReviewFlowTests::test_a_review_that_examined_the_work_counts`, `VerdictTests` |
| One assignment is one session: every contract says so, and where a provider asks before each tool use a tool that starts another agent is refused | `ContractTests::test_every_contract_keeps_the_work_in_one_session`, `ReviewerAuthorityTests::test_no_session_is_granted_a_tool_that_starts_another_agent` |
| A session stays on this machine: every contract says so, and where a provider asks before each tool use a connector, the web, a browser or a desktop is refused | `ContractTests::test_every_contract_keeps_a_session_to_this_machine`, `ReviewerAuthorityTests::test_no_session_is_granted_a_tool_that_reaches_outside` |

Other versions in this record, as read for the earlier revisions of this
document: Git 2.48.1.windows.1; Claude Code 2.1.323; codex-cli
0.158.0-alpha.15.2; AutoSwarm 2.0.0.dev0; SQLite 3.35.5 under Python
3.8.10 and 3.47.1 under Python 3.12.11. The regression evidence of each
earlier publication step is in section 1.

## 27. Execution-evidence authority (PR 31)

Provider-native session records entered the authority plane. The
implementation added native Codex obj-13 admission, registered
execution-reach policies, CommandExecution-to-Helios correlation, and
SESSION_TRANSCRIPT as a production evidence requirement.

| Item | Value |
|---|---|
| Implementation commit | `42e24ed` — provider-native obj-13, registered reach policy, command correlation |
| Proof commit | `cf3040d` — production correlation, terminality, policy binding and profile activation proof |
| Merge | `1364464` (PR 31), parents `5360493` and `cf3040d` |

### Production correlation measurement

| Measurement | Value |
|---|---|
| Luna sessions with both Codex and Helios evidence | 26 |
| Total CommandExecution items measured | 592 |
| Total Helios decisions measured | 968 |
| CommandExecution ids with exact Helios match | 591 |
| CommandExecution ids without Helios match | 1 (ungoverned outlier) |
| Duplicate CommandExecution ids | 0 |
| Duplicate Helios tool_use_ids | 0 |
| Helios-only decisions | 377 (non-command governance: file reads, greps, etc.) |

**Production correlation rule**: every provider CommandExecution item in
the measured production Luna corpus has exactly one matching Helios
decision under the same `exec-*` identity. The single unmatched
CommandExecution belongs to the one ungoverned session (no Helios
evidence).

### Provider terminality

| Measurement | Value |
|---|---|
| Sessions with terminal Codex record | 26 / 26 |
| Sessions with AutoSwarm outcome=completed | 26 / 26 |

**Terminality rule**: AutoSwarm call is terminal AND the native record
belongs to the bound native_session_id AND the native record parses
through its terminal final_answer event THEREFORE the native producer
record is final for obj-13 admission.

### Registered execution-reach policy

| Policy | Authorized reach |
|---|---|
| `luna-local/v1` (default) | MACHINE_LOCAL |
| `luna-research/v1` | MACHINE_LOCAL, PROVIDER_CONNECTOR |
| `luna-resource/v1` | MACHINE_LOCAL, PROVIDER_CONNECTOR |
| `opus-engineer/v1` | MACHINE_LOCAL, PROVIDER_CONNECTOR, PROVIDER_SUBAGENT |
| `opus-orchestrator/v1` | (empty — reasoning only) |

The policy identity is frozen into the ExecutionReceipt at derivation
time. A later policy edit does not reinterpret an already completed
attempt.

### Profile activation

SESSION_TRANSCRIPT is active in `SOURCE_MODIFYING_GOVERNED`. Receipt
classification:

| Condition | Completeness |
|---|---|
| Missing obj-13 | INCOMPLETE |
| Terminal clean obj-13 within authorized reach | eligible for COMPLETE |
| Observed reach outside authorized reach | INTEGRITY_FAILED |

### Execution-evidence test totals

| Suite | Result |
|---|---|
| Focused execution-evidence (8 test files) | 89 passed |
| Full regression (excluding legacy coordinator) | 860 passed |

### Findings disposition

**F-TF-31** (sub-agent behavior): the execution contract forbids
sub-agent tools. The registered policy `luna-local/v1` does not authorize
PROVIDER_SUBAGENT reach; the default route's session arguments switch off
`features.multi_agent`. An obj-13 record showing sub-agent activity would
produce INTEGRITY_FAILED.

**F-TF-32** (connector behavior): the registered policy `luna-local/v1`
does not authorize PROVIDER_CONNECTOR reach; the default route's session
arguments switch off connectors, apps, browser, web search and image
generation. An obj-13 record showing connector activity would produce
INTEGRITY_FAILED.

The distinction the execution-evidence implementation makes explicit:

| Concept | Meaning |
|---|---|
| Provider capability availability | what the provider CAN do (many things) |
| Authorized execution reach | what the policy ALLOWS (luna-local/v1: MACHINE_LOCAL only) |
| Observed execution reach | what the obj-13 record SHOWS the session DID |

The first is a provider fact. The second is a frozen policy. The third is
observed evidence. Authority evaluates the third against the second.

## 28. Generalized organizational model (PR 32)

The five organizational dimensions that generalize Autobot from
engineering-only to multi-profile orchestration.

| Item | Value |
|---|---|
| Commit | `fda8337` — WorkProfile, ResourceScope, Roles, ExecutionProfile |
| Merge | `320053e` (PR 32), parents `1364464` and `fda8337` |
| Files | 9 files, +1006 insertions |

### WorkProfile

| Profile | Default finalization | Default ExecutionProfile | External effect |
|---|---|---|---|
| ENGINEERING | SOURCE_MODIFYING | luna-local/v1 | yes |
| RESEARCH | READ_ONLY | luna-research/v1 | no |
| GENERAL_OPERATION | SOURCE_MODIFYING | luna-resource/v1 | yes |

### ResourceScope

9 resource kinds: REPOSITORY, REPOSITORY_PATH, FILESYSTEM_READ,
ARTIFACT_WORKSPACE, PERSISTENT_DEST, WEB_SOURCE, SERVICE, DATABASE,
EFFECT_TARGET.

3 activity classes: OBSERVATION, ARTIFACT_PRODUCTION, EXTERNAL_EFFECT.

### Organizational roles

| Role | Can reason | Can execute | Default model | Default ExecutionProfile |
|---|---|---|---|---|
| ORCHESTRATOR | yes | no | Opus 4.6 1M | opus-orchestrator/v1 |
| ENGINEER | yes | yes | Opus 4.6 1M | opus-engineer/v1 |
| PROJECT_MANAGER | yes | no | Opus 4.6 1M | opus-orchestrator/v1 |
| SPECIALIST | no | yes | Luna 6 Max | luna-local/v1 |
| REVIEWER | no | yes | Luna 6 Max | luna-local/v1 |

### ExecutionProfile hierarchy

6 registered policies with contained reach:
```
orchestrator = {} (empty)
local        ⊂ research ⊂ high-context ⊂ engineer
                resource ⊂ high-context ⊂ engineer
```

### Generalization regression

| Suite | Result |
|---|---|
| Focused generalization (5 test files) | 83 passed |
| Full regression (excluding legacy coordinator) | 943 passed |

## 29. Production state at closure of PRs 31 and 32

| Item | Value |
|---|---|
| `main` | `320053e` = `origin/main` |
| autobot.db | 29 COMPLETED sequences |
| intelligence.db | 0 active objectives |
| Taskforce | 4 ACTIVE members, 4 seats |
| Content store | 1167 objects |
| Codex sessions | 127 records (31 JSONL + 96 ZST) |
| AutoSwarm calls | 72 |
| Helios evidence sessions | 235 |
| Registered ExecutionProfiles | 6 |
| codex_sessions_root | configured (`C:\Users\dimas\.codex\sessions`) |
| SESSION_TRANSCRIPT | active for SOURCE_MODIFYING |

### Publication history

| PR | Change | Commit | Merge | Regression |
|---|---|---|---|---|
| 30 | Closure proof | `728cddc` | `5360493` | 826 / 0 |
| 31 | Execution-evidence authority | `42e24ed`, `cf3040d` | `1364464` | 860 / 0 |
| 32 | Generalized organizational model | `fda8337` | `320053e` | 943 / 0 |
| 33 | Production proof sections 27-29 | `c7acaae` | `bc0a5c9` | documentation only |
| 34 | Research, effects, mixed project graph | `5a6ee7e` | `1026bea` | 1004 / 0 |

## 30. Research, effects, and mixed project orchestration (PR 34)

| Item | Value |
|---|---|
| Commit | `5a6ee7e` — research evidence, filesystem effect, mixed project graph |
| Merge | `1026bea` (PR 34), parents `bc0a5c9` and `5a6ee7e` |
| Files | 8 files, +1173 insertions |

### Research evidence model

Source evidence kinds: SOURCE_CAPTURE, SOURCE_METADATA,
CLAIM_ASSOCIATION, RESEARCH_ARTIFACT.

Verification rules: source identity (required), source capture
(required), freshness (optional), source diversity (optional),
citation coverage (required), claim-to-source linkage (required),
artifact completeness (required), independent review (required).

Research reach semantics: a connector observation authorized under
`luna-research/v1` (PROVIDER_CONNECTOR in authorized reach) is a
reach violation under `luna-local/v1` (MACHINE_LOCAL only). The
observation is the same provider-native fact; the judgment differs
by the registered ExecutionProfile.

### Effect registry

| Family | Status | Executor | Observation |
|---|---|---|---|
| GIT_CAS | PROVEN | git_sequencer | local |
| FILESYSTEM_PLACE | IMPLEMENTED | filesystem_placer | local |
| WEB_MUTATION | DEFERRED | web_executor | remote |
| MESSAGE_DELIVERY | DEFERRED | message_executor | remote |
| DATABASE_MUTATION | DEFERRED | database_executor | local |
| SERVICE_OPERATION | DEFERRED | service_executor | remote |

### Filesystem effect

Content-addressed persistent file placement with compare-and-swap
semantics. Independent post-observation verification: destination
content address must match source artifact address. Reversible.

### Mixed Project Work Graph

Cross-profile orchestration: RESEARCH, ENGINEERING, and
GENERAL_OPERATION nodes coexist in one project. Dependencies
propagate accepted artifacts. Topological ordering ensures
predecessors complete before dependents. Ready-node scheduling
respects the dependency graph.

### Regression

| Suite | Result |
|---|---|
| Focused new tests (4 files) | 61 passed |
| Full regression (excluding legacy coordinator) | 1004 passed |

## 31. Post-PR35 execution-evidence production pilots

PR 35 (`4e7860c`) closed the initial production proof. The four commits
that follow it are direct descendants on `main`, not a separate branch:

| Commit | Change |
|---|---|
| `23b191a` | Execution-evidence schema fix: `command_correlation`, `execution_reach`, contradiction fields added to obj-15 schema (both copies); WorkProfile compiler integration; manifest generalized fields; PlanningView |
| `7000a54` | F-TF-34 intermediate: filter helios-only contradictions by `exec-*` prefix (later superseded) |
| `06921de` | F-TF-34 final: helios-only decisions are measurements, not contradictions; `provider_only` remains the sole command-governance contradiction |
| `46a769e` | Specialist-authored Pilot 11 confirmation (the commit the governed builder created and published) |

### Pilot 9: `obj-eng-20261120174140`

| | |
|---|---|
| Objective | Append an execution-evidence pilot version marker to RUNBOOK.md |
| Project | `pj_9d5badf30725155e7449e9ce1a937c95` "Execution-evidence pilot version marker" |
| Builder | `sq_1462445c896f0f1fffb6ab557038f360`, specialist `sp_17a8384fb1d2fe7756e5db8e7a7072e5` (claude-opus), RETAINED_SEAT |
| State | **HALTED** — the builder's execution receipt was rejected by obj-15 schema validation (F-TF-33) |
| Reviewer | `sq_be7b1ed5806c28fef8018da1f733339e` — **BLOCKED** (never staffed; predecessor HALTED) |
| Finding | **F-TF-33**: `command_correlation`, `execution_reach`, and contradiction fields were present in the receipt but absent from the obj-15 schema. The runtime produced correct evidence; the schema had not been updated to accept it. |
| Correction | `23b191a` added the fields to both schema copies (src and docs). |

### Pilot 10: `obj-eng-20261120183443`

| | |
|---|---|
| Objective | Append a Pilot 10 version marker to RUNBOOK.md |
| Project | `pj_b4b0b60d83efceae717d1660c60675a8` "Pilot 10 version marker" |
| Builder | `sq_de62871b5d02cf4b9803b161ccbca53f`, specialist `sp_17a8384fb1d2fe7756e5db8e7a7072e5` (claude-opus), RETAINED_SEAT |
| State | **HALTED** — the builder's receipt was INTEGRITY_FAILED due to 23 `governance_missing_provider_command` contradictions for helios-only tool_use_ids (F-TF-34) |
| Reviewer | `sq_8ea133b945ad4a6e09a6dc0a86009263` — **BLOCKED** (never staffed; predecessor HALTED) |
| Finding | **F-TF-34**: Codex assigns `exec-*` IDs to ALL tool calls (apply_patch, file reads, shell commands). Helios governs every tool call, so helios-only entries always exist for non-command tools. These are expected measurements, not integrity violations. |
| Intermediate | `7000a54` filtered by `exec-*` prefix (incorrect — all Codex IDs use `exec-*`). |
| Final correction | `06921de` removed all helios-only contradiction generation. Only `provider_only` (a CommandExecution without governance) remains a contradiction. The correlation counts (matched/provider_only/helios_only) are preserved as a measurement. |

### Finding F-TF-35: HALTED → READY resume path

The `t-s13` transition that moves a HALTED sequence back to READY remains
unimplemented. Pilots 9 and 10 are durable evidence of the gap: their
builder sequences are HALTED and their reviewer sequences are BLOCKED on
them. The objectives remain ACTIVE in intelligence.db.

**Status:** OPEN / DEFERRED. The workaround (start fresh objectives) was
used for Pilot 11.

### Pilot 11: `obj-eng-20261120185528`

The execution-evidence authority chain confirmed end to end, with all
three corrections (F-TF-33, F-TF-34 schema + correlation semantics) in
place.

| | |
|---|---|
| Objective | Append Pilot 11 execution-evidence confirmation to RUNBOOK.md |
| Project | `pj_decd8bac7c435aea335e99ef55d378d1` "Pilot 11 execution-evidence confirmation", COMPLETED |
| Run date | 2026-11-20 |

**Builder (append-pilot11-marker):**

| | |
|---|---|
| Workstream | `ws_d2bf6513015738db8e275786140393e7`, documentation/builder, SOURCE_MODIFYING |
| Sequence | `sq_f89146b4d80857ab43b7be7e42cf3c47`, COMPLETED |
| Record | `sha256:cb2a4bf67d1f0ff21bdf701dd77f8a80e9e10e3263a81dd908b5013ffcbf05f2` |
| Contract | `sha256:bf8bfb1a8957e677e489a1f68848cea90e1b973e785cc7717a6cc10bce371bf8` |
| Assignment | `asg_350c44e2c6d577e94d5387c27489466a`: specialist `sp_17a8384fb1d2fe7756e5db8e7a7072e5` (claude-opus), RETAINED_SEAT, TASKFORCE, seat `ts_c31fff88a7cf30af7a2f73740370f927`, standing ESTABLISHED |
| Route | luna-6-max: codex / gpt-6-luna / max |
| Attempt | `at_e027818fbd46e928e491460eec79b29e` |
| Call | `call_ee73f10624ea668647c192cb68319757` |
| Verification | `a1` PASS `sha256:0e9029c0eb6151c6f3382afa5f14873410458831584bc2180f8048e2cbdbfd65`; `a2` PASS `sha256:5035786c8f59dbf4bf588bc4fd1686cf4e81a836bd4a33271ec1f63b86f9dc44`; `role-transform/builder` PASS `sha256:cd070a69934fdc1e234bf82aaf4954a7f73534502a6c9a11f236d35338a733f1` |
| Outcome | VERIFIED_ACCEPTED |

**Reviewer (review-pilot11-marker):**

| | |
|---|---|
| Workstream | `ws_914a3c18f03f3123283e8659c5a09136`, verification/reviewer, READ_ONLY |
| Sequence | `sq_9809a552c4264e7ce37502d3dd04afa7`, COMPLETED |
| Record | `sha256:0319cd0c4223989eec7bc0424d3b15ad822e0f24aa9252b50241add7d7bbc7ed` |
| Contract | `sha256:5c6ecca440027183f874f951b1a4fb95127e9706c8626f5f7530dd2ce905ec8d` |
| Assignment | `asg_ffb4740a547adae49d9a4159236484e3`: specialist `sp_b2f72881be89546de36037d84fea287d` (claude-haiku), RETAINED_SEAT, TASKFORCE, seat `ts_bddb43b39c785f99430327912e364331`, standing ESTABLISHED |
| Route | luna-6-max: codex / gpt-6-luna / max |
| Attempt | `at_97b46cc74912d71d2483925a609053d1` |
| Call | `call_431eb8f3bc0b984ea80ddad0be6663fb` |
| Predecessor | review-of `sha256:cb2a4bf67d1f0ff21bdf701dd77f8a80e9e10e3263a81dd908b5013ffcbf05f2` (the builder record) |
| Review verdict | EXAMINED |
| Verification | `a1` PASS `sha256:d83bf748d3f1b41d890e3a89984822f9176ab14c93d95148f885df79e8622c85`; `a2` PASS `sha256:bc207418aaeedee8c144aff435b58290bd3c4df1614df5f8a0e0107a1bef9729`; `role-transform/reviewer` PASS `sha256:9896ebe475916a218259e8be41017c02f37e3eb04c873e776588825967533952` |
| Outcome | VERIFIED_ACCEPTED |

**Reviewer independence:** The builder (`sp_17a8384fb1d2fe7756e5db8e7a7072e5`,
claude-opus) and reviewer (`sp_b2f72881be89546de36037d84fea287d`,
claude-haiku) are different specialists, different memberships, different
seats, different assignments, different attempts, different calls, and
different native provider sessions.

**Finding chain summary:**

| Finding | Discovered | Corrected | Confirmed |
|---|---|---|---|
| F-TF-33 | Pilot 9 (schema rejection) | `23b191a` | Pilot 10 (schema accepted), Pilot 11 |
| F-TF-34 | Pilot 10 (helios-only contradictions) | `06921de` | Pilot 11 |
| F-TF-35 | Pilot 9 (HALTED → READY) | OPEN / DEFERRED | — |

## 32. Final production state

| Item | Value |
|---|---|
| Item | Value |
|---|---|
| `main` | `484d6fd` = `origin/main` |
| autobot.db | 31 COMPLETED + 2 HALTED + 2 BLOCKED sequences (35 total) |
| intelligence.db | 2 ACTIVE objectives (Pilots 9/10), 11 COMPLETE; schema v5 |
| Projects | 10 COMPLETED, 2 ACTIVE (Pilots 9/10 with HALTED builders) |
| Taskforce | 4 ACTIVE members, 4 seats (3 ESTABLISHED + 1 RETAINED) |
| Content store | 1327 objects |
| Authority objects | 101 (autobot.db) |
| Transition journal | 1578 entries |
| Specialists | 6 registered |
| Total tests | 1089 passed, 0 failed |

### Publication history (complete)

| PR | Change | Merge | Tests |
|---|---|---|---|
| 30 | Closure proof | `5360493` | 826 |
| 31 | Execution-evidence authority | `1364464` | 860 |
| 32 | Generalized organizational model | `320053e` | 943 |
| 33 | Production proof sections 27-29 | `bc0a5c9` | docs |
| 34 | Research, effects, mixed project graph | `1026bea` | 1004 |
| 35 | Final proof closure and capability classification | `4e7860c` | 1004 |
| 36 | Post-PR35 reconciliation: Pilots 9/10/11 and F-TF-33/34/35 | `525d0c7` | 1023 |

### Post-PR36 commits on main

| Commit | Change | Tests |
|---|---|---|
| `df3b6f5` | OrchestratorService, OrchestratorPolicy, OrchestratorPlanningView | 1043 |
| `0f0fff0` | Orchestrator integrated into ContinuousDriver loop | 1043 |
| `1e4c89a` | intelligence.db schema v4: generalized workstream dimensions + dependency table | 1043 |
| `f89d21d` | Execution-evidence generalization: profile binding, receipt derivation, zstd | 1055 |
| `9e54344` | Live schema v4: generalized writers, dependency APIs, real Orchestrator data | 1055 |
| `b39aea7` | ResourceScope authority binding: compiler, manifest, proposal validation | 1059 |
| `623965e` | Functional Orchestrator: 13 typed decisions, validation, application, convergence | 1076 |
| `484d6fd` | Workstream-to-authority bridge, durable orchestration reasoning, schema v5 | 1089 |

## 33. Capability classification

| Capability | Status | Evidence |
|---|---|---|
| Autobot deterministic authority plane | LIVE END TO END | 35 sequences, 13 objectives |
| Engineer — Opus 4.6 1M | LIVE | 12 projects |
| Engineer Project Manager | LIVE | 12 projects |
| Taskforce persistent organization | LIVE | 4 members, 12 dissolved teams |
| Luna engineering specialists | LIVE | Pilots 3-11 |
| Independent Luna reviewers | LIVE | Pilots 6, 8, 11 |
| Per-assignment Opus escalation | LIVE | Pilot 3 |
| Provider-native obj-13 | LIVE — PRODUCTION CONFIRMED | Pilot 11 |
| SESSION_TRANSCRIPT | LIVE — PRODUCTION CONFIRMED | Pilot 11 |
| CommandExecution-to-Helios correlation | PROVEN — SEMANTICS CORRECTED | F-TF-34, Pilot 11 |
| Observed execution reach | AUTHORITY VISIBLE | Pilot 11 |
| ExecutionProfile authority binding | IMPLEMENTED | Compiler freezes from work_profile; receipt reads from record |
| Reach-policy derivation from frozen profile | IMPLEMENTED | Receipt resolves registered policy; historical fallback to luna-local/v1 |
| Execution-reach integrity | LIVE — PRODUCTION CONFIRMED | Pilot 11 |
| Engineering WorkProfile | IMPLEMENTED | Pilot 11 (implicit ENGINEERING) |
| Research WorkProfile | IMPLEMENTED | |
| General Operation WorkProfile | IMPLEMENTED | |
| ResourceScope | IMPLEMENTED | |
| Mixed Project Work Graph | IMPLEMENTED | |
| Filesystem effect | IMPLEMENTED | |
| Git CAS effect | PROVEN | |
| Research evidence model | IMPLEMENTED | |
| Objective Orchestrator typed decisions | IMPLEMENTED | 13 decision classes, validation, deterministic application |
| Orchestrator exactly-once reasoning | IMPLEMENTED | Durable orchestration_reasoning table; input-digest replay |
| Orchestrator → authority bridge | IMPLEMENTED | workstream_to_proposal converts organizational to executable contracts |
| Orchestrator production Opus policy | PENDING | Decision types and application ready; production reasoning route pending |
| Luna research specialists (luna-research/v1) | IMPLEMENTED | |
| Luna operation specialists (luna-resource/v1) | IMPLEMENTED | |
| Opus high-context escalation (opus-high-context/v1) | IMPLEMENTED | |
| Generalized workstream persistence (schema v4) | IMPLEMENTED | Writers, readers, dependency APIs live |
| JSONL.ZST native recognition | IMPLEMENTED | Session recognizer decompresses; CAS stores exact producer bytes |
| Finding F-TF-36: governed builder false claim | RECORDED | Specialist published incorrect F-TF-35 claim; verification does not check prose |
| HALTED recovery (F-TF-35) | OPEN / DEFERRED | Pilots 9/10 as evidence |
| Acceptance reuse (Chapter 5) | DEFERRED BY FROZEN ROADMAP | |
| Remote verification/publication (Chapter 6) | DEFERRED BY FROZEN ROADMAP | |
| Deployment hardening | DEFERRED BY FROZEN ROADMAP | |
| Web/message/database/service effects | DEFERRED BY FROZEN ROADMAP | |

LIVE: running in production with evidence.
PRODUCTION CONFIRMED: exercised in a live production pilot that completed the full authority chain.
PROVEN: measured against production evidence with deterministic tests.
IMPLEMENTED: code, model, tests, and regression complete; production pilots pending.
OPEN / DEFERRED: known gap with durable evidence; workaround available.
DEFERRED BY FROZEN ROADMAP: architecture designed; implementation follows the frozen chapter sequence.

## 34. Independent orchestration architecture (post-PR38, 92b57be → db2d6d8)

### Architectural correction

The primary organizational identity is now `orchestration_node_id`.
Project identity, proposal identity, TaskContract identity, sequence identity,
provider identity, and model identity are bindings or realizations of that
organizational node. A Project is an optional organizational container selected
when useful for durable coordinated work.

The Coordinator is the universal admission and elevation layer. Every ACTIVE
objective enters through `CoordinatorService.admit_objective()`, which creates
a durable `CoordinatorDecision` and `OrchestrationRun`.

### Schema v6

Additive migration (all v5 rows preserved, no table recreation):

| Table | Purpose |
|---|---|
| `coordinator_decisions` | Admission/elevation decisions (DIRECT, ENGINEER_ELEVATED, ORCHESTRATOR_ELEVATED) |
| `orchestration_runs` | One coordinated realization per objective |
| `orchestration_nodes` | Primary organizational work identity (TASK_CONTRACT, ENGINEER_REASONING, CONTROL) |
| `orchestration_edges` | Organizational DAG with authority edge binding |
| `orchestration_authority_bindings` | Node → proposal → TaskContract → sequence chain |
| `orchestration_engineering_bindings` | Node → Engineer reasoning request → decision |
| `orchestration_project_bindings` | Optional node → Project association |
| `orchestration_node_outputs` | Accepted artifacts, engineering decisions, research artifacts |

Additional: `proposals.origin_kind`, `proposals.origin_id`, sentinel cycle for
orchestration-origin proposals, `project_assignments.orchestration_node_id`.

### Coordinator entry in driver.step()

```
_coordinator_admit(now)                # for each unadmitted objective
    ↓
orchestrator.run_cycle(...)            # node-centric view
    ↓
_admit_ready_nodes(now)                # TASK_CONTRACT nodes → authority
    ↓
engineer.run_cycle(...)                # legacy Engineer path
    ↓
taskforce.advance(now)                 # staffing and execution
    ↓
_release_accepted_dependencies(now)    # legacy workstream deps
    ↓
_release_orchestration_edges(now)      # node edge projection
```

PassthroughCoordinatorPolicy skips admission entirely, preserving backward
compatibility with the existing Engineer-driven flow.

### ProductionCoordinatorPolicy

Stable admission classifier using complexity feature analysis:

| Feature | Maps to |
|---|---|
| `research_required` | +1 |
| `engineering_design` | +1, ENGINEER_ELEVATED if alone |
| `multi_domain` | +1 |
| `organizational_complexity` | +1 |
| `external_effects` | +1 |
| `deep_dependencies` | +1 |
| Score ≥ 3 | ORCHESTRATOR_ELEVATED |
| `engineering_design` alone (< 3) | ENGINEER_ELEVATED |
| Otherwise | DIRECT |

Deterministic for equivalent canonical inputs.

### Authority binding identity tiers

Complete frozen identity chain recorded per node:

```
orchestration_authority_bindings:
    proposal_id
    task_contract_id
    requirement_digest
    contract_digest
    record_hash
    sequence_id
```

Authority predecessor compilation uses the correct identity tier
(RECORD, CONTRACT, REQUIREMENT) from the source node's binding.

### Decision normalization (v1 → v2)

| v1 Class | v2 Class |
|---|---|
| `CREATE_WORKSTREAM` | `CREATE_NODE` |
| `ADD_DEPENDENCY` | `ADD_EDGE` |
| `COMPLETE_WORKSTREAM` | `COMPLETE_NODE` |
| `COMPLETE_PROJECT` | `COMPLETE_RUN` |
| `RESTRUCTURE_PROJECT` | `RESTRUCTURE_RUN` |

Both accepted. v1 decisions automatically normalized before application.

### Deterministic test coverage

| Suite | Tests | Coverage |
|---|---|---|
| `test_orchestration_architecture.py` | 51 | Schema, Coordinator, nodes, edges, bindings, outputs, decisions |
| `test_coordinator_integration.py` | 13 | Authority runtime, elevation, restart convergence, project-independent |
| `test_orchestrator.py` | 37 | Existing orchestrator tests (backward compat) |
| `test_workstream_bridge.py` | 13 | Bridge tests (backward compat) |

### Regression

```
base:    92b57be (1089 passed)
head:    db2d6d8 (1153 passed)
delta:   +64 tests
failed:  0
```

### Commits

| SHA | Description |
|---|---|
| `77b71e4` | Schema v6, Coordinator, OrchestrationRun/Node model |
| `6beb0fa` | Integration tests with authority runtime |
| `8fcd90f` | Production Coordinator entry, node-centric view, generalized proposal origin |
| `db2d6d8` | Full-chain authority tests: elevation, restart, project-independent |

### Updated capability classification

| Capability | Status | Evidence |
|---|---|---|
| Autobot Coordinator | IMPLEMENTED | ProductionCoordinatorPolicy, 3 elevation modes, deterministic tests |
| CoordinatorDecision persistence | IMPLEMENTED | Idempotent across restarts (tested) |
| OrchestrationRun | IMPLEMENTED | Full lifecycle: ACTIVE → COMPLETED (tested) |
| OrchestrationNode | IMPLEMENTED | PENDING → EXECUTING → COMPLETED (tested) |
| OrchestrationEdge / DAG | IMPLEMENTED | PENDING → RELEASED with authority provenance (tested) |
| Authority binding (all identity tiers) | IMPLEMENTED | proposal → contract → record → sequence (tested) |
| Engineering binding | IMPLEMENTED | Request → adopted decision (tested) |
| Optional Project binding | IMPLEMENTED | Zero-binding and post-creation binding (tested) |
| Node outputs | IMPLEMENTED | ACCEPTED_ARTIFACT, RESEARCH_ARTIFACT (tested) |
| Project-independent orchestration | IMPLEMENTED | Runs with zero project bindings (tested) |
| v1 → v2 decision normalization | IMPLEMENTED | All 9 mappings (tested) |
| Node-centric OrchestratorPlanningView | IMPLEMENTED | Runs, nodes, edges, bindings, outputs in view |
| Generalized proposal origin | IMPLEMENTED | ORCHESTRATION_NODE origin, sentinel cycle |
| Coordinator production entry | IMPLEMENTED | _coordinator_admit() in driver.step() |
| Production Opus Orchestrator policy | PENDING | Decision types, validation, structured output route ready |
| Production Opus Engineer reasoning | PENDING | Engineering binding schema ready |
| Production research pilot | PENDING | Architecture ready, production execution pending |
| Production Orchestrator pilot | PENDING | Architecture ready, production execution pending |
