# Chapter 0 closure: baseline, ownership and freeze

**Phase:** C (verify, freeze, close). **Date:** 2026-11-08. **Handoff:** `../../implementation/handoffs/ch00-phase-c.json`, with its SHA-256 sidecar. **Amended:** the same day by amendment 1 (CORR-55, CORR-56; section 8), whose handoff is `ch00-amendment-01.json`; `latest.json` names the newest handoff.

This is the audit surface for Chapter 0. An agent that has lost the conversation can read this folder and rebuild exactly:

- who may decide that work is done;
- which object proves each step;
- why Helios, AutoSwarm and Akashic stay unchanged.

## 1. Acceptance gate

The figures in this table are Phase C's. Amendment 1's gate is in section 8, and section 2 gives the current counts.

| Item | Verdict | Evidence |
|---|---|---|
| The registries are the canonical form, and are checked mechanically | PASS | `check_registries.py`: 36 of 36 checks |
| The checker catches known defects | PASS | `test_checker_negative.py`: 11 of 11 planted defects caught, 5 of them taken from the drafts |
| Every capability has exactly one owner | PASS | 55 capabilities; 0 with no owner, 0 with two |
| Every object type has exactly one minter of an allowed kind | PASS | 42 types; no receipt minted by an LLM or an agent |
| Each finalization path has exactly one PONR, and nothing crosses it backwards | PASS | 7 paths |
| Every ref or push effect runs under a bounded intent; canonical effects need an active delegation and a permit reservation | PASS | 6 effect types; the outcome rules treat the expected old value as unknown, never as absent |
| No unverified or uncommitted acceptance; PUBLISHED_UNVERIFIED releases nothing; stale and fenced attempts are dead ends; revocation covers every state before the PONR | PASS | Graph checks |
| Identity tiers don't leak into each other; bindings are resolved at freeze | PASS | 9 / 16 / 7 fields; 5 bindings |
| The authority graph is acyclic in both topologies, and every forbidden edge is absent | PASS | 15 authority edges, 17 forbidden |
| Every invariant has a planned test | PASS | 73 invariants: 41 proven by registry checks now, the rest by tests in Chapters 1 to 8 |
| Every correction is resolved | PASS | 54 corrections |
| Every decision is made | PASS | All 18 decided. You confirmed D-04 on 2026-11-08 (CORR-54) |
| Where receipts live is a checked rule | PASS | `objects.storage` (INV-72): the ledger and the content-addressed store hold every authoritative object; HDB is index-only; Git holds source proof only |
| Unknowns are tracked, each with a probe and what it blocks | PASS | 18 unknowns. None blocks starting Chapter 1: U-10 and U-18 are resolved inside it. U-01 blocks AUTOMATIC acceptance of contracts that require Helios integrity (D-05) |
| The prose agrees with the registries | PASS | `docs.ids_exist`, `docs.counts_match`, `docs.registers_current` |
| No machine paths anywhere in this folder or the handoff | PASS | `docs.no_machine_paths` |
| Upstream commits and worktree states are pinned | PASS | Section 3 |
| Handoff, sidecar and `latest.json` exist | PASS | Committed only when you ask |

## 2. Registry counts

The checker compares this table with the registries.

<!-- registry-counts -->
| Count | Value |
|---|---|
| principals | 26 |
| capabilities | 55 |
| capabilities owned by you | 12 |
| capabilities owned by Autobot seats | 31 |
| capabilities owned by external owners | 12 |
| object types | 42 |
| state machines | 9 |
| transitions | 88 |
| wildcards | 5 |
| expanded wildcard edges | 200 |
| finalization paths | 7 |
| effect types | 6 |
| intent classes | 3 |
| invariants | 76 |
| invariants with a registry proof | 44 |
| enforcement points | 32 |
| corrections | 56 |
| decisions | 18 |
| unknowns | 18 |
| checks | 38 |

## 3. Upstream pins (2026-11-08 00:41 UTC)

Read-only git, with no fetch. Remote names come from each checkout's own configuration.

| Repository | Role in Chapter 0 | Branch | Commit | State |
|---|---|---|---|---|
| github.com/dimascior/Autobot | Primary target | main | `2d8427c80d676e4ed67f8026b9edf9e334058463` | git refuses this checkout for the shell user (dubious ownership), so HEAD was read from the git files; worktree state unreadable |
| github.com/dimascior/Helios- | Read-only: the live command gate | main | `e9ad8881e0d7aaca49574af7ff8b3c642fe44be8` | The 2026-11-04 Reset manifest commit; no upstream configured; 2 tracked changes |
| github.com/dimascior/Akashic | Read-only | main | `51209af948348ed25fb5709bf54b017155cc2ff1` | 1 ahead of origin/main; 4 tracked changes |
| github.com/dimascior/HeliosDataBase (HDB) | Read-only index | main | `7100932b25a2b9c256d11bfe077f8dc149296d9f` | The configured catalog checkout; 5 ahead of origin/main; 35 tracked changes |
| github.com/dimascior/codex-handoff | Read-only | main | `24d823c0810447fa562d9a55e1925ecfe6741759` | In sync with origin/main; clean |
| github.com/dimascior/Codex_claude_devtools | Read-only witness reader | claude/serene-brahmagupta-ljj3xj | `a091ee116d2b52d079d4f800b7f0fa36bb5db229` | 2 ahead of its upstream; clean |
| github.com/dimascior/CODEAPI | Read-only | main | `8678f46a18f85646d98511b73080266d5103bf84` | The guard commit; no upstream configured; clean |
| github.com/dimascior/CODEAPI (second checkout) | Read-only | release/terminal-runtime-cleanup | `a78a8cb362d4cf83a4187818376a0be78fe6860d` | 63 ahead and 9 behind its upstream; clean |
| github.com/dimascior/HeliosEntitlementAuthority (HEA) | Method corpus | main | `79628711bc78c8862c8f097d2d4de79f69192c9c` | 138 behind origin/main, which is at `f439f5244313e6e0d1737be556c56b68905d4b56`: the 418-commit history Chapter 0 cites. 111 tracked changes |
| AutoSwarm (fork github.com/dimascior/AutoSwarm) | Reference: the execution engine | n/a | Not pinned | No checkout in the scanned folders. The installed tool was built from the fork's main (`addc189`, recorded 2026-11-05, not re-read) |

## 4. Prior phases

Phases A and B are drafts 1 to 4. They stay outside this repository because they contain machine-local paths. Their hashes:

| Draft | Bytes | SHA-256 |
|---|---|---|
| autobot-chapter0-who-what-how.md | 108012 | `7a1f140b915e104c910a518b8f95df86bcbc764a5a8854dbd069ca888851bb5c` |
| autobot-chapter0-who-what-how-draft2.md | 107101 | `ac45b4ac0aef9a88eb5b2129a22e74c0dee930181b6713b5f4a843669c2ba44b` |
| autobot-chapter0-who-what-how-draft3.md | 62138 | `a41fa1ea2b1dea984cf4869b7d4d3c57563f2298830b3185107af69c63e53af4` |
| autobot-chapter0-who-what-how-draft4.md | 31997 | `a9e87db49bf7231549525102edc4c47e20bdffaeb389715967b1153f095546d3` |

## 5. What writing the registries changed

Each of these is fixed in the registries:

- **HALTED is split.** HALTED (before verification) and HALTED_AFTER_VERIFICATION are separate states, so a resume can never skip verification.
- **UNKNOWN_OUTCOME can always resolve:** by observation, by an idempotent retry under fresh authority, or by your decision (INV-70).
- **An integrity-failed ExecutionReceipt now halts the sequence** (t-s45).
- **Staging-ref deletion has a receipt** (obj-41).
- **The post-publication CI check has its own state**, POST_PUBLICATION_OBSERVING.

## 6. Chapter 1 prerequisites

1. Commit this folder, the handoff, its sidecar and `latest.json` together.
2. `latest.json` names `ch00-phase-c.json`, and the sidecar hash verifies.
3. `check_registries.py` passes on the committed tree.

**Chapter 1 scope:** contracts, identifiers and canonical serialization. That means:

- golden hashes for all three identity tiers;
- the registration and transform schemas;
- U-10 and U-18.

## 7. Amendment before the acceptance commit

You confirmed D-04 on 2026-11-08, recorded as CORR-54. It settles where receipts live:

- **The ledger** holds authoritative metadata, identities, hashes, bindings, state transitions and receipt references.
- **The content-addressed store** holds the immutable receipt payloads and the retained evidence and artifact bytes, addressed by hash.
- **HDB** is a derived, queryable index. Its freshness or availability never gates completeness, acceptance or authoritative state, unless a TaskContract explicitly declares an HDB-derived assertion.
- **Git** holds source-state proof and path-free references only.

The decision is folded in before the acceptance commit, so Phase C stays one phase. The registries, checker, register, this document and the handoff were all updated together. The decision is now a checked rule (`objects.storage`, INV-72), and one more planted defect proves that rule fails when it should.

## 8. Amendment 1: intent closure (CORR-55, CORR-56)

Chapter 1 Phase A found two gaps in the effect lifecycle and registered them as Chapter 0 correction candidates:

- no transition created the intent of the two standalone effects;
- the retry (t-i06) minted a fresh intent without the checks an initiation rule makes.

You decided both on 2026-11-08, specified the corrections, and corrected the first draft in review. They are appended after the Phase C freeze, with their own handoff, `../../implementation/handoffs/ch00-amendment-01.json`. That handoff names two relationships:

- **predecessor:** `ch01-phase-a.json`, the latest handoff when it was written, so the audit chain stays linear: Chapter 0 Phase C, then Chapter 1 Phase A, which found the defects, then this amendment, then Chapter 1 Phase B;
- **amends:** `ch00-phase-c.json`, the Chapter 0 handoff whose architecture it corrects.

Neither the Phase C handoff nor Chapter 1 Phase A is rewritten, and Phase A stays checked against the baseline it reviewed.

What changed:

- **CORR-55, standalone intents.** Each standalone effect has its own Sequencer lifecycle, with an initiation rule and effect-specific checks: staging_ref (t-g01 to t-g03) and workspace_sync (t-w01 to t-w04). Each leaves its in-flight state only on HAPPENED or ABSENT under its frozen outcome rule, and holds while the intent is UNKNOWN_OUTCOME; a staging ref observed absent is a delete that HAPPENED. A failed workspace safety predicate ends in SKIPPED, with no intent and no ref moved (t-w03). The cleanup authority comes from an immutable, previously established source. No object and no EffectIntent state changed.
- **CORR-56, retries.** t-i06 proves the identical effect and holds the attempt-scoped checks. It then moves the old intent to SUPERSEDED_BY_RETRY and mints a fresh intent that binds it. It retries exactly the attempt-scoped effects; the standalone effects are named as not retryable.
- **Invariants.** INV-47 keeps its identifier and is revised in place: every intent is committed with its effect class's checks. INV-73 covers retries, INV-74 says standalone effects use their own checks, and INV-75 says every effect that requires an intent closes.
- **The checker.**
  - Each effect names an intent class, and the classes and their checks are in `registries/transitions.json`.
  - Two new checks:
    - `effects.intent_creation`: every mint of obj-25 names its effect and holds its class's checks; standalone classes require no attempt-scoped check; the retry retries exactly the retryable effects and proves the identical effect; a skip mints no intent.
    - `effects.intent_closure`: every effect that requires an intent has a creation rule, its outcome lifecycle and a receipt on its HAPPENED path, which a SKIPPED receipt never counts as; standalone lifecycles leave their in-flight state only as their outcome rule says.
  - `effects.require_intent` now covers every machine, and `effects.definitions` no longer exempts standalone effects.
  - The universal check list of Phase C (delegation, reservation, fence) now applies only to the attempt-scoped class.
- **Planted defects.** Thirteen new ones cover both corrections and your review, and each must now be caught for its stated reason. One existing case, a canonical push intent without a delegation check, now fails under `effects.intent_creation`, which owns that rule.

| Item | Verdict | Evidence |
|---|---|---|
| The registries are checked mechanically | PASS | `check_registries.py`: 38 of 38 checks |
| The checker catches known defects, each for its stated reason | PASS | `test_checker_negative.py`: 24 of 24 planted defects caught |
| Every effect that requires an intent closes | PASS | `effects.intent_closure`: 6 of 6 effects |
| Every intent is created with its class's checks | PASS | `effects.intent_creation`: 3 intent classes, retries included |
