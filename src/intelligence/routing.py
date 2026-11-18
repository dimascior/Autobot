"""Execution routing: which provider, model and session configuration a
specialist's session runs on.

Routing is execution state. It is resolved downstream of every
engineering and organizational decision:

    a work requirement says      capability, role, objective, constraints,
                                 expected output
    staffing decides             the specialist (a retained member the
                                 Taskforce resolves, or an outside specialist
                                 the Project Manager selects)
    routing decides, last        provider, model, reasoning effort, native
                                 session arguments

Nothing here is part of a TaskContractRecord, a requirement, a project, a
workstream, a capability, a seat, a membership or a demonstration. A
specialist is an organizational identity; the route it executes on can be
replaced between any two attempts and none of those identities change.

The policy is declared in configuration::

    [routing]
    default    = "luna-6-max"      # every specialist session
    escalation = "opus-4-6-1m"     # work the organization escalates
    fallback   = ["sonnet"]        # only while the default is disabled
    utility    = "haiku"           # lightweight utility work
    escalate   = ["architecture"]  # capabilities (or "capability:role")
                                   # whose work runs on the escalation route

    [routing.routes.luna-6-max]
    agent  = "codex"               # the provider AutoSwarm launches
    model  = "gpt-6-luna"
    effort = "max"
    provider_args = ["--sandbox", "workspace-write"]

Route names are the organization's own words. The identifiers inside a
route (agent, model, effort) are the provider's, passed through exactly.

The Engineer never sees any of it. Work runs on the escalation route in
two ways, and neither names a model:

    the organization's rule    ``escalate`` lists capabilities (or
                               "capability:role" pairs) whose work always
                               runs escalated
    the Project Manager's ask  a staffing decision for ONE assignment may
                               carry ``"escalation": "<class>"`` (one of
                               ESCALATION_CLASSES) with its rationale; the
                               request is recorded with the assignment and
                               routing resolves it
"""

import re

ROUTE_CLASSES = ("default", "escalation", "fallback", "utility")

# What a Project Manager may ask for when one assignment's engineering
# problem needs more than the organization's standard execution depth.
# These are the Engineer's words: they describe the need, never a
# provider or a model. Routing resolves every one of them to the
# declared escalation route.
#
#   deep_reasoning   the problem needs deeper reasoning than standard
#                    work (a subtle design, a hard defect, a repair after
#                    standard-depth work failed verification)
#   high_context     the problem spans more of the system than standard
#                    work holds in view (architecture across many modules,
#                    a review of a large change)
ESCALATION_CLASSES = ("deep_reasoning", "high_context")

# Placeholders a route's provider_args may carry; the transport fills them
# for each session from the workspace it actually launches in
# (AutoSwarmTransport.SESSION_PLACEHOLDERS is the same list).
PLACEHOLDERS = ("{repo_git}", "{gate_pending}", "{gate_pending_real}",
                "{workspace}")
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


class RoutingError(Exception):
    """The routing policy is missing something or contradicts itself."""


def _text(value, what):
    if not (isinstance(value, str) and value.strip()):
        raise RoutingError("%s must be a non-empty string" % what)
    return value.strip()


def _route(name, spec):
    if not isinstance(spec, dict):
        raise RoutingError("[routing.routes.%s] must be a table" % name)
    provider = spec.get("agent") or spec.get("provider")
    provider = _text(provider, "[routing.routes.%s] agent" % name)
    model = spec.get("model")
    if model is not None:
        model = _text(model, "[routing.routes.%s] model" % name)
    effort = spec.get("effort")
    if effort is not None:
        effort = _text(effort, "[routing.routes.%s] effort" % name)
    args = spec.get("provider_args", [])
    if not (isinstance(args, list)
            and all(isinstance(a, str) and a for a in args)):
        raise RoutingError(
            "[routing.routes.%s] provider_args must be a list of strings"
            % name)
    for arg in args:
        for token in _PLACEHOLDER.findall(arg):
            if token not in PLACEHOLDERS:
                raise RoutingError(
                    "[routing.routes.%s] provider_args names %s; the"
                    " placeholders a session can fill are %s"
                    % (name, token, ", ".join(PLACEHOLDERS)))
    enabled = spec.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RoutingError(
            "[routing.routes.%s] enabled must be true or false" % name)
    return {"name": name, "provider": provider, "model": model,
            "effort": effort, "provider_args": list(args),
            "enabled": enabled}


class RoutingPolicy:
    """The declared routes and which of them each class of work uses."""

    def __init__(self, routes, default, escalation=None, fallback=(),
                 utility=None, escalate=()):
        self._routes = {}
        for name, spec in routes.items():
            self._routes[_text(name, "a route name")] = _route(name, spec)
        self.default = self._known(default, "default")
        self.escalation = self._known(escalation, "escalation") \
            if escalation is not None else None
        self.utility = self._known(utility, "utility") \
            if utility is not None else None
        if isinstance(fallback, str):
            fallback = [fallback]
        self.fallback = [self._known(name, "fallback")
                         for name in (fallback or [])]
        self.escalate = []
        for rule in escalate or []:
            rule = _text(rule, "an escalate rule")
            capability, _sep, role = rule.partition(":")
            self.escalate.append((capability, role or None))
        if self.escalate and self.escalation is None:
            raise RoutingError(
                "[routing] escalate names work to escalate but no"
                " escalation route is declared")

    def _known(self, name, what):
        name = _text(name, "[routing] %s" % what)
        if name not in self._routes:
            raise RoutingError(
                "[routing] %s names the route %r, which is not declared"
                " under [routing.routes]" % (what, name))
        return name

    @classmethod
    def from_config(cls, section):
        """The policy a ``[routing]`` table declares, or None when the
        configuration has none (every specialist then carries its own
        route, as before)."""
        if section is None:
            return None
        if not isinstance(section, dict):
            raise RoutingError("[routing] must be a table")
        routes = section.get("routes")
        if not (isinstance(routes, dict) and routes):
            raise RoutingError(
                "[routing] needs at least one route under [routing.routes]")
        if "default" not in section:
            raise RoutingError("[routing] needs a default route")
        return cls(routes, section["default"],
                   escalation=section.get("escalation"),
                   fallback=section.get("fallback", ()),
                   utility=section.get("utility"),
                   escalate=section.get("escalate", ()))

    # ------------------------------------------------------------ reads

    def names(self):
        return list(self._routes)

    def route(self, name):
        """One declared route, by name (a copy)."""
        if name not in self._routes:
            raise RoutingError("no route is declared as %r" % (name,))
        route = dict(self._routes[name])
        route["provider_args"] = list(route["provider_args"])
        return route

    def class_of(self, name):
        """The classes a route serves, in ROUTE_CLASSES order."""
        classes = []
        if name == self.default:
            classes.append("default")
        if name == self.escalation:
            classes.append("escalation")
        if name in self.fallback:
            classes.append("fallback")
        if name == self.utility:
            classes.append("utility")
        return classes

    def class_for(self, capability, role=None):
        """The route class one piece of work runs on: ``escalation`` when
        the declared rule names its capability (or capability and role),
        otherwise ``default``. A function of the work's capability and
        role alone."""
        for rule_capability, rule_role in self.escalate:
            if rule_capability == capability and \
                    rule_role in (None, role):
                return "escalation"
        return "default"

    def resolve(self, route_class="default"):
        """The route a class of work executes on now.

        ``default`` is the default route, or while that is disabled the
        first enabled fallback, in the declared order. ``escalation`` is
        the escalation route, or while that is disabled whatever
        ``default`` resolves to. ``utility`` is the utility route.
        A class with no enabled route is an error, never a silent
        substitution."""
        if route_class not in ROUTE_CLASSES:
            raise RoutingError("unknown route class %r (one of %r)"
                               % (route_class, ROUTE_CLASSES))
        if route_class == "escalation" and self.escalation is not None \
                and self._routes[self.escalation]["enabled"]:
            return self._resolved(self.escalation, "escalation")
        if route_class == "utility":
            if self.utility is None or \
                    not self._routes[self.utility]["enabled"]:
                raise RoutingError("no enabled utility route is declared")
            return self._resolved(self.utility, "utility")
        if route_class == "fallback":
            candidates = list(self.fallback)
        else:
            candidates = [self.default] + list(self.fallback)
        for position, name in enumerate(candidates):
            if self._routes[name]["enabled"]:
                served = route_class if route_class == "fallback" else (
                    "default" if position == 0 else "fallback")
                return self._resolved(name, served)
        raise RoutingError(
            "no enabled route for %s work: the default route %r is"
            " disabled and no fallback is enabled"
            % (route_class, self.default))

    def _resolved(self, name, served_as):
        route = self.route(name)
        route["served_as"] = served_as
        return route

    def describe(self):
        """The whole policy as plain data (for reports and preflight)."""
        return {
            "default": self.default,
            "escalation": self.escalation,
            "fallback": list(self.fallback),
            "utility": self.utility,
            "escalate": ["%s:%s" % (c, r) if r else c
                         for c, r in self.escalate],
            "routes": [dict(self.route(name),
                            classes=self.class_of(name))
                       for name in self._routes],
        }


def route_identity(route):
    """The words that identify a route in a record: its name, and the
    provider's own identifiers exactly as they were passed."""
    if not route:
        return None
    return {key: route.get(key)
            for key in ("name", "provider", "model", "effort", "served_as")
            if route.get(key) is not None}
