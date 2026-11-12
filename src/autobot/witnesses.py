"""The registered witness kinds, pinned to Chapter 0 (D-21's discriminator).

Mirrors every ``class: "witness"`` entry of
``docs/architecture/chapter0/registries/objects.json`` — the object id is
the ``witness_kind`` discriminator a D-21 structured admission entry
carries, and the registered minter is the only principal that may be named
as the entry's ``producer``. A test cross-checks this table against the
registry file (the ``models.py``/``effects.py`` pinning discipline).
"""

# witness_kind (object id) -> {"name": registered name, "producer": minter}
WITNESS_KINDS = {
    "obj-11": {"name": "WitnessRecord: execution lifecycle",
               "producer": "autoswarm"},
    "obj-12": {"name": "WitnessRecord: command governance",
               "producer": "helios"},
    "obj-13": {"name": "WitnessRecord: session transcript",
               "producer": "agent_clis"},
    "obj-14": {"name": "WitnessRecord: remote check",
               "producer": "ci_system"},
}
