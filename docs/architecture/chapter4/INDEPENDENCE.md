# Test Independence (TI-1): D-58-grade oracles for the runtime suite

Chapter 1 set the standard for a proof that can be believed (D-58): a
second implementation that imports nothing from the first (`jcs2.py`),
both compared over RFC examples and seeded random values, goldens
recomputed in a fresh interpreter, and a checker tested by planting
specific defects after a clean control passes. The runtime ledger suite
(chapters 2–4, `tests/autobot/`) was green and deterministic, but green
under ONE implementation: its expected values came from the production
functions and its state reads went through the Sequencer's own
connection. TI-1 brings that suite to the chapter-1 standard.

Every statement here is implemented and proven by the suite. The
objective, verbatim from the directive: a defect in canonicalization,
preimage layout, hashing, journal chaining, ledger pairing, receipt
binding, the calibrated transform, or retention reachability is caught by
code that does not share the defective implementation — and every refusal
the registers promise is tied to a planted defect that provably trips it.

## 1. The oracle inventory

Each oracle is a module under `tests/autobot/` that imports NOTHING from
`autobot` or `contract_library`. That is enforced three ways, the same
for every oracle: an AST scan of the module's imports against an explicit
standard-library allowlist; a ban on the dynamic-import tokens
(`__import__`, `importlib`, `exec(`, `eval(`, `runpy`, `pkgutil`); and a
run in a fresh `-I` interpreter whose loaded-module list must name no
production package.

| Oracle | Re-derives, with its own code | Written from | Proven by |
|---|---|---|---|
| `preimage2.py` | canonical bytes (an iterative JCS writer unlike the library's and unlike `jcs2`); content addresses; the domained object hash; the three identity tiers; the closure artifact root; the journal entry hash | the FROZEN chapter-1 specification, decisions D-24/D-29..D-33/D-37, SEM-17, `identity.json` and the golden vectors — never the library source | `test_independent_identity.py` |
| `auditor2.py` | one runtime directory's durable authority facts: the journal chain (its own hasher over the raw rows); the D-68 row↔journal pairing, replayed entry by entry; every content reference in every ledger column, re-hashed by its own file walk; every indexed object's identity; the obj-16/18/19/20/37 and obj-15 bindings re-parsed from raw store bytes; retention reachability by its own walker | the rules restated in its docstring, each beside the source it restates | `test_independent_state.py` |
| `transform2.py` | the calibrated A1 ingestion rule (`autoswarm-input-v1`) twice: through the interpreter's incremental text layer, and as a byte automaton — neither shares a mechanism with the registered script's decode-and-replace chain | the calibrated rule of `VERIFICATION.md` section 13 | `test_independent_transform.py` |
| `real_bytes.py` | nothing — it READS the provider records retained on the operator's machine, in place, for the local-only arm of section 4 | — | `test_independent_transform.py` |

What each proof consists of:

- **Identity** (`test_independent_identity.py`). The oracle ALONE
  reproduces every frozen golden digest (so it is anchored to the frozen
  vectors, not to the library it is compared with); then equality with
  production over every chapter-1 fixture object, the live registrations
  and records the suite binds, every closure kind and the runtime's own
  payload shapes; three-way agreement on canonical bytes with the library
  and with chapter 1's `jcs2`; seeded random payloads, manifests, records
  and journal chains (SEED printed, fixed); the documented refusals; and
  a fresh-interpreter recompute of the goldens.
- **State** (`test_independent_state.py`). `auditor2` sweeps real
  runtimes the production plane built — the verified flow, a FAIL →
  repair → halt flow, an attested flow, the transform mismatch and the
  calibrated pass, the whole SL loop through completion, a READ_ONLY
  completion, an effect-intent retry history, every closure form, and a
  hard-killed child's directory — and finds nothing; its retention map
  equals `Sequencer.retention_roots()` leaf for leaf; production
  `recover()` agrees. Then the checker is tested like a checker: a clean
  COPY passes, and each of 37 planted defects (a forged journal payload,
  a broken link, a row whose state disagrees with its last entry, a
  retargeted binding column, a flipped store byte, a deleted admission
  row …) makes exactly the named check fail. A fresh `-I` process audits
  a preserved directory and must emit the identical report.
- **Path confusion.** An oracle that never read the ledger must not be
  able to say "clean". `auditor2` refuses a missing directory, an empty
  one, a foreign SQLite file and a ledger without its store
  (`AuditUnavailable`, exit status 2 — never 0), never creates the
  database it is pointed at, and every clean report carries a WITNESS:
  the resolved directory, the journal entry count and the head entry
  hash. Each sweep compares that witness with the same facts read through
  the production connection of that runtime; another runtime's witness
  differs.
- **Read-only.** A quiescent ledger is opened `mode=ro&immutable=1` (no
  lock, no sidecar: the directory is byte-for-byte as it was found); a
  ledger with a live writer is read `mode=ro` THROUGH the WAL, proven to
  see the writer's latest committed row.
- **Transform** (`test_independent_transform.py`). The registered
  closure, resolved, materialized and executed in its own process,
  against both references over 25 byte families (BOMs, CR/CRLF/lone-CR,
  UTF-16, overlong and truncated sequences; six families are refused);
  then the exact registered script bytes, executed in-process — a bridge
  test proves that equals the materialized closure — over 800 seeded
  random byte strings, valid and invalid UTF-8, with non-vacuity counts,
  idempotence and one-byte sensitivity. Zero divergences.

Running an oracle by hand:

    python -I tests/autobot/auditor2.py <runtime data dir>
    python -I tests/autobot/preimage2.py docs/architecture/chapter1/vectors/fixtures

`auditor2` prints its report as JSON; exit status 0 is clean, 1 is
findings, 2 is "could not audit".

## 2. The mutation matrix

`tests/autobot/mutation_matrix.json` is the registry that ties every
refusal the suite proves to one row:

    invariant(s) → seam → planted defect → the named refusal
    (error class, pinned marker) → the test that plants it → the control
    that passes without the plant

It was populated by reorganizing the EXISTING refusal assertions — 219 in
the six classes the audit counted (`IntegrityHalt`, `ConflictError`,
`StaleFenceError`, `AuthorityLost`, `AdmissionError`, `JournalBroken`),
plus the `LeaseUnavailable`, `PermitExhaustedError` and `SchemaTooNew`
sites — with no test deleted and no existing test changed. Enumerating
them against the target list showed which registered refusals were proven
by error CLASS only; `test_mutation_holes.py` fills those holes in the
chapter-1 negative shape (control first, one planted defect, the marker
pinned). At TI-1 the registry holds 293 rows over 252 refusal sites; 112
rows pin a marker, 181 pin the error class alone, and 11 of the rows
record a refusal a seam RETURNS rather than raises. Each row also carries `site` —
`<module>.<function>#<ordinal of the refusal assertion in that function>`
— an addition to the directive's row shape that makes the census below
checkable.

`test_mutation_matrix.py` is the registry's checker:

- **the census, both ways**: an AST walk finds every refusal assertion in
  `tests/autobot/`; each must be named by a row, and each row must name a
  real site carrying exactly the error class it claims — a new refusal
  assertion without a row, or a row drifting from its test, fails; and
  since the census names a site by ONE class, a refusal asserted as a
  tuple of classes or as the `LedgerError` base — which no row could
  carry — is itself a failure;
- every `test` and `control` resolves through the unittest loader to
  exactly one test, and the test really reaches its site;
- **a marker is a pin, not prose**: it must be a string constant asserted
  against the caught exception's text AFTER the refusal and BEFORE the
  variable is rebound (or an `assertRaisesRegex` pattern, or the constant
  a test hands to a helper that performs that assertion). Comments and
  docstrings are not in the AST; the checker is itself tested over a
  specimen source where the marker exists only in a comment, only in a
  docstring, against another binding, or before the refusal — and finds
  none of them;
- every target invariant has at least one row with a pinned marker:
  INV-03, INV-06, INV-07, INV-13, INV-17, INV-18, INV-44, INV-49,
  INV-53, INV-58, INV-66, INV-68, INV-72, SEM-15, SEM-16, D-52, D-53,
  D-56, D-C3-2, F-C4-1, F-C4-2, and every registered chapter 2–4 runtime
  proof whose statement names a refusal (the list is checked against
  `invariants.json`: refusal proofs and the five positive-property proofs
  together must be exactly the registered set);
- every id a row cites exists in a chapter registry (findings: in a
  chapter document).

These are PLANTED-DEFECT proofs — corrupt an input, a row or a byte, and
the seam must refuse by name. Classical code-mutation testing (mutating
the implementation and watching tests fail) is out of scope for TI-1.

## 3. Findings

Each production change in this milestone fixes a real defect an oracle
exposed, and names the oracle that failed.

- **F-TI-1 — fixed.** `contract_library.object_hash("TaskContractRecord",
  …)` is the flat full-payload hash; the frozen D-29/D-33 define obj-04's
  object hash as the `record_hash` TIER. The divergence was reachable:
  `Registry.admit_object` accepted a record payload, indexed it under the
  flat hash, and the next `recover()` HALTed permanently ("does not
  recompute", INV-11). Failing oracle:
  `test_independent_identity.ProductionEqualityTests`. Fix, at the seam
  and not in the frozen library: `Registry.admit_object` refuses a
  TaskContractRecord — a record enters only through the admission
  machine. The library-level divergence stays pinned by
  `test_f_ti_1_flat_object_hash_is_not_obj04s_identity`.
- **F-TI-2 — recorded, not fixed (a chapter-1 correction is the
  operator's).** The library restricts the tier preimages at TOP level
  only, where `identity.json` lists nine and sixteen DOTTED fields; an
  unlisted nested member would enter the library's preimage and not the
  oracle's. Unreachable for a frozen record — the closed schema refuses
  unknown nested members before any hash is taken — and pinned by
  `test_f_ti_2_unlisted_nested_members_enter_the_library_preimage`.
- **F-TI-3 — documentation gap.** The library enforces an object-kind
  grammar that no frozen chapter-1 registry or specification states; an
  oracle written from the documents therefore does not refuse what the
  library refuses. Pinned by
  `test_f_ti_3_kind_grammar_is_enforced_but_undocumented`.
- **F-TI-4 — fixed.** `Sequencer.reclaim` protected less than `recover()`
  requires: it deleted journaled finalization receipts and the execution
  evidence admitted results bind, after which recovery HALTed (D-66).
  Failing oracle: `test_independent_state.RetentionOracleTests` — reclaim
  everything the independent auditor says the ledger requires, and
  nothing may be removed. See `VERIFICATION.md` section 18.
- **F-TI-5 — OPEN, recorded and pinned, deliberately not fixed.** The
  obj-12 recognizer does not read what the governance producer writes;
  section 4.2. The correction decides which evidence classifies
  INTEGRITY_FAILED, so it is an authority-semantics decision.
- **F-TI-6 — fixed.** A packaged manifest's entry paths, and a
  registration's entrypoint, could name a file on Windows other than the
  one the strings say; section 4.3 and `VERIFICATION.md` section 17.
  Failing oracle: `test_real_boundary.ClosureFilesystemTests` (seven
  planted-defect tests failed against the unfixed module).

## 4. The real boundaries

### 4.1 Real provider bytes — the local-only arm

The suite drives a fake transport. The A1 chain was calibrated against
real provider records that exist only on the operator's machine, and
`real_bytes.py` reads them there, in place: the retained
`calls/<call_id>/task.json` records under `AUTOSWARM_HOME` and the prompt
files under `AUTOBOT_PROMPT_EVIDENCE_DIR`. Over those bytes the arm
proves: the census (19 records, each still the bytes the manifest
identifies); for the four Luna records whose input file is retained, that
the REGISTERED closure executed over the exact file bytes outputs exactly
the prompt the provider recorded, that both references agree, and that
the production observed-side derivation over the exact `task.json` bytes
yields that identity; for the six PowerShell 5.1-composed monitor
prompts, the calibrated signature (BOM kept, no CR, the closure a fixed
point, the CRLF form mapping back) and — for the five whose body file is
retained — the exact composition; and that a real record, which names its
own call, is refused at the witness seam for any other attempt.

**The approval boundary (directive section 7c).** The repository is
public. No provider or prompt byte is committed; committing any real
provider or Helios bytes needs the operator's explicit approval per file.
What IS committed is `real_bytes_manifest.json`: sha256 identities and
class tokens only — no call id, no file name, no size, no text. The
operator regenerates it from the live records with
`python tests/autobot/real_bytes.py tests/autobot/real_bytes_manifest.json`.

**The arm cannot skip silently where the evidence lives.** With
`AUTOBOT_REAL_BYTES` unset the arm runs when the evidence is present and
skips, visibly, when it is absent. With `AUTOBOT_REAL_BYTES=require` —
set by the operator on the host that holds the evidence, and set for
every TI-1 suite leg — absence is a FAILURE.

### 4.2 Command governance (obj-12) — and F-TI-5

The shared drive path admitted prose as its governance witness, so no
driven test ever exercised the recognizer. It now admits the decision
record and the result record of one governed command in the RECOGNIZED
fact family (the spellings `witness_semantics` recognizes), and
`test_real_boundary.py` asserts what is extracted: the identity facts of
both records, both JOINED to the attempt's bound session, the execution
decided by its decision record, a COMPLETE classification with no
contradiction.

The recognized family is not what the producer writes. Reading the Helios
command gate's own records (shape only; the constructed records in
`support.py` reproduce key sets, key order and byte framing with
synthetic values) shows two differences, each proven on this machine's
real records before it was written down:

1. **Framing.** Both record kinds are written by Windows PowerShell 5.1 as
   UTF-8 WITH a byte order mark (and CRLF line ends). The recognizer
   decodes strict UTF-8 and its JSON parser refuses a leading mark, so
   the exact producer bytes yield NO fact: the record admits UNJOINED and
   the admission identity gate cannot see its session.
2. **Spelling.** The decision record spells its decision `verdict`
   (ALLOW, DENY, DENY_FATAL, PASSTHROUGH, UNKNOWN, INTEGRITY_FAILURE);
   the recognizer's only spelling is `decision`. Even from the document
   without the mark, no decision is recognized.

The consequence, pinned end to end by
`GovernanceRecognizerDivergenceTests`: a DENY followed by the execution
of the same command — the INV-68 `deny_then_ran` contradiction, which
classifies INTEGRITY_FAILED in the constructed spelling — classifies
COMPLETE with no contradiction in the producer's real framing. And the
correction is not one line: read past the mark alone and every result
record's execution facts appear while no decision does, so every ALLOWED
command becomes `execution_without_decision` and a DENIED one is named an
orphan rather than `deny_then_ran`. Framing, spelling and the verdict
vocabulary (is DENY_FATAL a DENY? is PASSTHROUGH a decision? what does
UNKNOWN decide?) have to be ruled on together, under D-C3-5/6/8. Until
then the pins hold the present behavior, so the correction is a conscious
act in the same change that rewrites them.

### 4.3 The filesystem under identity — and F-TI-6

Probes were added only where identity is load-bearing: the content store
and closure materialization.

**The content store.** An object lives at `objects/<two hex>/<64 hex>`,
lowercase. Those names are pure ASCII, so neither case folding nor Unicode
normalization can make two addresses one file, and an address in any other
spelling (uppercase hex) is refused as not an address at all (D-30).

**NFC/NFD.** Unicode filename normalization does not reach identity, and
no test here pretends to exercise it. Identity is the SHA-256 of bytes
and of canonical JSON in which a path is a string compared code point by
code point; the store's own names are ASCII; and a materialized entry is
written and then executed by the SAME string, so a filesystem that
preserves names (NTFS) and one that normalizes them (APFS) both hand back
the file that was written. The one place a filesystem could FOLD two
distinct strings — two manifest entries spelled NFC and NFD — is closed
by the grammar, not by a probe: a closure path must be NFC (the frozen
D-26 rule), so the answer never depends on the filesystem.

**F-TI-6.** Case folding and Win32 name resolution, unlike normalization,
did reach what executes. On this machine's filesystem a manifest whose
entries differ only by case, by a trailing dot or space, or by a
`::$DATA` stream suffix was accepted, and after materialization the
entrypoint file held THE OTHER entry's bytes; a drive-letter segment
resolved outside the run directory; a device name (`nul .txt`) reached
the device and left no file; and an entry named like a file the runner
writes afterwards (`input.bin`) would be overwritten by the transform's
input and executed. The closure-path grammar of `VERIFICATION.md` section
17 now refuses all of these identically on every platform, at resolution,
before anything is written. It also refuses the 8.3 short-alias form
(`NAME~1.EXT`): this machine's volume generates no short names, so that
fold could not be observed here, and it is refused on the known rule
rather than on an observation.

### 4.4 The D-56 boundary: the executable hash is captured, not constrained

obj-18 records the interpreter's `executable_sha256` and each named
tool's — the binary's identity, never its path. The frozen D-56
constraint vocabulary (interpreter implementations and versions; platform
os and architectures; tool versions; exact environment values) has no
rule that names it: a constraint key spelled `executable_sha256` fails
closed as unsupported, even when it carries the true hash, so no contract
can believe it pinned a binary. A contract pins an interpreter by
implementation and version; which binary ran is recorded evidence. This is
documented, not changed — widening the vocabulary is a chapter-1 decision.
(Closure EXECUTION is a different rule: a python closure pins the
interpreter identity string and HALTs on any other, INV-66.)

## 5. The corrected audit record

Verified against the tree on 2026-11-13; TI-1 builds on it. Each item
carries its TI-1 disposition.

Confirmed gaps:

- (a) **Identity-oracle circularity.** `support.py` computed expected
  values with the production `object_hash`, `content_address` and
  `artifact_root`; `jcs2` protects canonicalization only; the preimage
  layout, content addresses, the artifact root and the retention walk had
  no second implementation. — *Closed: `preimage2`, and `auditor2`'s
  retention walker.*
- (b) **No systematic independent state reader.** The 153 direct SQL
  reads (17 files) all used the connection the Sequencer writes through.
  — *Closed: `auditor2`, its own read-only connection, in process and in
  a fresh process.*
- (c) **No formal mutation matrix.** 219 refusal assertions (16 files)
  existed, but nothing tied invariant → planted defect → named refusal →
  test id in an enforced registry. — *Closed: section 2.*
- (d) **Real-boundary gaps.** AutoSwarm is `fake_transport`; the
  drive-path obj-12 governance bytes were hardcoded prose. — *Narrowed:
  real provider RECORDS are proven locally (4.1) and the drive path
  carries recognized governance facts (4.2); the provider LAUNCH is still
  faked in the suite (`live_g2.py` stays the opt-in live arm), and the
  real governance shape is F-TI-5, open.*

Claims that were false as stated — the capability already existed:

- (e) `test_adversarial.py` opens a literal second `sqlite3.connect` and
  proves the stale-guard lives in the SQL WHERE clause.
- (f) The crash matrix (`test_recovery.py`) opens `Database(...)` fresh in
  the parent over a database the crashed CHILD process wrote — a genuine
  process-boundary reader.
- (g) `journal.validate` re-hashes every entry from stored canonical
  payload bytes and verifies every link; a planted forged payload gets
  `JournalBroken`. Residue: the re-hasher was the production journal
  module. — *That residue is closed: `auditor2` hashes the chain with its
  own code, three ways.*
- (h) `test_provenance.py` states and proves the byte-identical-artifacts-
  from-two-attempts property (INV-03), and holds a runtime mtime-flip
  metamorphic.
- (i) Most negatives pin the refusal MARKER text, which identifies the
  rule; the missing part was the control-first, registry-enumerated
  chapter-1 shape. — *Supplied by the matrix.*

Also recorded: C4-M2's `register_transform_check` re-executes the bound
transform closure at admission (derive, not trust), and several M2 byte
vectors assert hand-written literal expected bytes — those were never
circular. The circularity lived in the binding and receipt layer.

## 6. Honest limits

- **Shared primitives.** The oracles are independent of this
  repository's code, not of CPython: both sides call the standard
  library's SHA-256, JSON parser, SQLite and Unicode tables.
- **The journal hasher precedent is closed**, as (g) records — and only
  for what `auditor2` re-derives. It re-parses receipt BINDINGS; it does
  not re-derive the obj-15 classification from witness bytes, because
  there is no second witness recognizer. F-TI-5 was found by reading the
  producer, not by an oracle disagreeing.
- **The matrix proves refusals of planted defects.** It is not code-
  mutation testing. 181 of its 293 rows pin the error class alone. For a
  refusal a seam returns, the checker verifies that the marker is
  asserted in the test, not which value it is asserted against — that
  part is by review.
- **The real-bytes arm proves what is retained.** Nine of the nineteen
  records were given inline, with no input file to recompute from; for
  those only the census is checked.
- **The filesystem probes describe this machine**: NTFS, short-name
  generation off. The grammar is portable; the observation is local.
- **F-TI-2, F-TI-3 and F-TI-5 are open** by decision, each pinned by a
  test that fails the moment the behavior changes.
- **No caller boundary.** In the interim in-process topology (D-01) an
  oracle is a reader with the same file access as the code it audits; it
  detects defects, it is not a security boundary (Chapter 8).
