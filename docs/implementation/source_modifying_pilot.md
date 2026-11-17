# SOURCE_MODIFYING Pilot Record

## Definitive production proof

A specialist agent, running in an isolated Git worktree at a frozen source
revision, created the candidate commit itself. The coordinator did not author
the change.

| Item | Value |
| --- | --- |
| Frozen target ref | `refs/heads/main` |
| Frozen source revision | `e3ad4d0ff4066db8dc7132274cd702d2b4034ea7` |
| Authorized path | `docs/implementation/source_modifying_pilot.md` |
| Candidate commit | specialist-created; its parent is the frozen source revision |

The candidate SHA is reported in the specialist's `SourceModificationResult`
(`target_ref`, `expected_old`, `new_value`). A commit cannot contain its own
hash, so it is not recorded here.

## What the run demonstrated

- Task scope: the specialist could change only the authorized path.
- Source revision: the worktree was frozen at the recorded revision, and the
  candidate's parent is that revision.
- Worktree isolation: the canonical branch was not modified; the candidate
  exists only as a local commit in the isolated worktree.
- Specialist ownership: the specialist staged and committed the change under
  the prompt contract. The coordinator verifies parentage and does not create
  the candidate.
- Clean finish: `git diff --name-only <source> HEAD` listed only the authorized
  path, and `git status --porcelain` was empty.

## Related changes

- PR #18: SOURCE_MODIFYING task scope, source revision, worktree isolation.
- PR #19: specialist-owned candidate, prompt contract, parentage verification.
