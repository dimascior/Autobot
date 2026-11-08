# Chapter 1 Phase A: the contract surface (frozen 2026-11-08)

Phase A maps what Chapter 1 has to specify: the protocol objects, the identity fields and their tiers, golden vectors, and the questions and decision candidates that are still open. It is discovery, frozen. Nothing is decided here; Phase B decides. It rests on Chapter 0, in `../../chapter0/`.

## Read in this order

1. `CHAPTER1_PHASE_A.md`: what Phase A found, explained.
2. `CHAPTER1_PHASE_A_REGISTERS.md`: every registry entry as tables. This file is generated; don't edit it by hand.
3. `registries/*.json`: the canonical form. Where any prose disagrees with these files, the files win.

The phase handoff is `../../../implementation/handoffs/ch01-phase-a.json`, with its SHA-256 sidecar and `latest.json`.

## Check it

Python 3.11 or later, standard library only. From this folder:

```text
python check_phase_a.py --ch0 ../../chapter0 --handoffs ../../../implementation/handoffs --hea <HEA extract>
python test_phase_a_negative.py --ch0 ../../chapter0 --handoffs ../../../implementation/handoffs --hea <HEA extract>
```

The HEA extract is a folder holding the HEA files that `registries/hea_techniques.json` cites. These are the files of `github.com/dimascior/HeliosEntitlementAuthority` at `f439f5244313e6e0d1737be556c56b68905d4b56`, read with `git cat-file` and kept at their repository paths. The checker confirms each file's recorded SHA-256. Add `--evidence <gate evidence folder>` to confirm that each cited gate ran; without it, gate citations are accepted unchecked. `--render` regenerates the register after a registry changes.

The checker reads Chapter 0's committed files and its handoff, and fails if any byte differs. It checks the inventory, bindings, identity fields, vectors, claims, unknowns, decision candidates and HEA citations against Chapter 0 and the HEA corpus. The negative tests plant known defects, some in a temporary copy of Chapter 0, and each must be caught for the stated reason.

## Changing Phase A

Phase A is frozen. A change is a new finding, never a rewrite:

1. Append an entry to `registries/review.json`.
2. Edit the registries.
3. Run `--render`.
4. Run both checks.
5. Write a new phase handoff.

Chapter 0 changes only by an appended Chapter 0 correction. U-44 and U-47 are candidates for one, and only you can decide them.
