# Chapter 0: Evidence Sequencing (frozen 2026-11-08)

This is the next architecture for Autobot: who may decide that work is done, and how that decision is proven. It is designed and frozen, not implemented. `../../ARCHITECTURE.md` still describes the running system.

## Read in this order

1. `CHAPTER0_ARCHITECTURE.md`: the design, explained.
2. `CHAPTER0_CLOSURE.md`: the acceptance gate, registry counts, upstream pins and Chapter 1 prerequisites.
3. `CHAPTER0_REGISTERS.md`: every registry entry as tables. This file is generated; don't edit it by hand.
4. `registries/*.json`: the canonical form. Where any prose disagrees with these files, the files win.

The phase handoff is `../../implementation/handoffs/ch00-phase-c.json`, with its SHA-256 sidecar and `latest.json`.

## Check it

Python 3.11 or later, standard library only. From this folder:

```text
python check_registries.py            # all checks; exit 1 on any failure
python check_registries.py --render   # regenerate CHAPTER0_REGISTERS.md after changing a registry
python test_checker_negative.py       # plants eleven known defects; each must be caught
```

The checker covers:

- ownership, minting and transitions;
- one point of no return per path, with nothing crossing it backwards;
- effect intents and permits;
- the state-graph properties;
- identity tiers, bindings and the authority graph;
- where receipts live (D-04);
- that every correction, decision, unknown and invariant is complete;
- that these documents cite real IDs, quote the real counts and contain no machine paths.

## Changing Chapter 0

Changing Chapter 0 follows HEA's rule: append a correction, never rewrite one.

1. Add a `CORR-` entry to `registries/corrections.json`.
2. Edit the registries.
3. Run `--render`.
4. Run both checks.
5. Write a new phase handoff.
