"""Compatibility import path.

The reasoning intelligence is the Engineer (``intelligence.engineer``);
before the organizational model was aligned it was called the Chief
Engineer and lived in this module. Everything is re-exported here under
both its current and its former name, so existing imports keep working.
"""

from .engineer import (  # noqa: F401
    BUDGET_NOTE,
    STANDING_RULE_ID,
    SUBMITTING_PRINCIPAL,
    ChiefEngineerPolicy,
    ChiefEngineerService,
    EngineerPolicy,
    EngineerService,
    PlanningBudget,
    PlanningView,
    standing_staffing_rule,
)

__all__ = [
    "BUDGET_NOTE", "STANDING_RULE_ID", "SUBMITTING_PRINCIPAL",
    "ChiefEngineerPolicy", "ChiefEngineerService", "EngineerPolicy",
    "EngineerService", "PlanningBudget", "PlanningView",
    "standing_staffing_rule",
]
