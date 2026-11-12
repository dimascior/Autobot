"""The first persistent loop driver.

    read ProjectState -> identify planning events -> invoke the Chief
    Engineer -> validate typed proposals -> compile -> Registry
    admission -> allocate READY work -> PromptProjection -> transport
    dispatch -> collect outcomes -> registered authority arcs -> wait
    for authority-state change -> repeat.

Restart safety is layered, never clever: the authority ledger is the
only truth (D-69 restart discipline); planning events are keyed by
journal seq; proposals converge on deterministic identity and Registry
DUPLICATE; dispatch idempotency rides the Sequencer-minted call_id and
the Transport contract; witness and verification admissions are
idempotent on content. Killing this driver anywhere and starting a new
one continues the same loop without duplicating proposals, contracts or
specialist launches — the autonomous-pilot suite does exactly that.

One driver reasons at a time (the plan store's driver lease). The
authority side needs no such guard: the Sequencer's instance lease and
single-writer transactions already fence it.
"""

from autobot import project_state
from autobot.models import instant
from datetime import timedelta

from .chief import ChiefEngineerService
from .planstore import readonly_view
from .taskforce import Taskforce


def _any_activity(planning, sweep):
    if planning["cycle_id"] is not None:
        return True
    if sweep["allocated"] or sweep["adopted"] or sweep["collected"] \
            or sweep["verified"]:
        return True
    arcs = sweep["arcs"]
    return any(arcs[key] for key in arcs)


class ContinuousDriver:
    def __init__(self, plan, registry, sequencer, store, environment,
                 clock, policy, transport, runner, agent_factory,
                 authority_db_path, repo_path=None,
                 repository_context=None, holder="chief-engineer",
                 lease_seconds=3600, max_proposals_per_cycle=16):
        self.plan = plan
        self.clock = clock
        self.holder = holder
        self.lease_seconds = lease_seconds
        self.ro = readonly_view(authority_db_path)
        self.chief = ChiefEngineerService(
            plan, registry, environment, clock, policy,
            max_proposals_per_cycle=max_proposals_per_cycle)
        self.taskforce = Taskforce(
            plan, sequencer, store, transport, runner, agent_factory,
            clock, self.ro, repo_path=repo_path,
            repository_context=repository_context)
        self._acquire()

    def _acquire(self):
        now_dt = self.clock.now()
        self.plan.acquire_driver_lease(
            self.holder, instant(now_dt),
            instant(now_dt + timedelta(seconds=self.lease_seconds)))

    def close(self):
        self.plan.release_driver_lease(self.holder)
        self.ro.close()

    # -------------------------------------------------------------- steps

    def step(self):
        """One loop iteration: a planning cycle (when something new woke
        the planner) and one Taskforce sweep. Returns the step report;
        ``quiet`` means the step changed nothing and proposed nothing."""
        self._acquire()  # renew; refuses if another driver took over
        now = instant(self.clock.now())
        planning = self.chief.run_cycle(self.ro, project_state, now)
        sweep = self.taskforce.advance(now)
        return {"planning": planning, "sweep": sweep,
                "quiet": not _any_activity(planning, sweep)}

    def run_until_stable(self, max_steps=64, quiet_steps=2):
        """Repeat ``step`` until the loop is stable: ``quiet_steps``
        consecutive steps with no planning cycle and no Taskforce
        activity. Returns the closing report with what remains: active
        objectives and proposals waiting on YOUR registered authority."""
        steps = []
        quiet = 0
        while len(steps) < max_steps:
            report = self.step()
            steps.append(report)
            quiet = quiet + 1 if report["quiet"] else 0
            if quiet >= quiet_steps:
                break
        else:
            raise RuntimeError(
                "no stable state within %d steps (runaway loop guard)"
                % max_steps)
        return {
            "steps": len(steps),
            "reports": steps,
            "active_objectives": self.plan.active_objectives(),
            "waiting": self.plan.proposals_by_status(
                "WAITING", "QUEUED_FOR_YOU"),
        }
