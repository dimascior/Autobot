"""The engineering capability vocabulary.

``CAPABILITIES`` is the vocabulary the Engineer and the Taskforce
share: the Engineer says WHAT capability a piece of engineering
work needs, the Taskforce resolves WHO holds (or can be tried for) it.
A specialist may demonstrate several.

It is intelligence-plane vocabulary — NOT a Chapter 1 enum and not the
protocol's capability registry: the protocol-side
``capability_requirements`` field of a frozen TaskContractRecord carries
registered cap-NN ids and stays whatever the frozen record says. The two
never mix.
"""

CAPABILITIES = (
    "architecture",
    "database_design",
    "debugging",
    "documentation",
    "domain_engineering",
    "git_repository_operations",
    "research",
    "security_review",
    "software_implementation",
    "verification",
)
