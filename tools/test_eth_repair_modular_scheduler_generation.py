from __future__ import annotations

from eth_repair_modular.contracts import GenerationContext, SchedulerContext
from eth_repair_modular.generation import SingleResponsibilityGenerationPolicy, PersistentDebtChildEpochGenerationPolicy
from eth_repair_modular.scheduler import EventCompletedOnlySchedulerPolicy, ContinuousResponsibilitySchedulerPolicy


def main() -> int:
    serial = EventCompletedOnlySchedulerPolicy()
    continuous = ContinuousResponsibilitySchedulerPolicy()
    old_generation = SingleResponsibilityGenerationPolicy()
    child_epoch = PersistentDebtChildEpochGenerationPolicy()

    # Scheduler: current event-completion clock waits off-event; continuous
    # responsibility scheduler reevaluates, but still does not authorize action.
    off_event = SchedulerContext(
        t=1000,
        seconds_left=250.0,
        responsibility_live=True,
        receipt_advanced=True,
        after_kind=None,
    )
    a = serial.evaluate(off_event)
    b = continuous.evaluate(off_event)
    assert not a.reevaluate_management
    assert b.reevaluate_management and not b.event_clock_authority

    # Generation: frozen V70G remains locked with partial debt. Candidate opens
    # only a new child epoch after confirmed Repair progress and no equivalent
    # Expand child remains physically owned. Debt itself remains positive.
    partial = GenerationContext(
        generation_authorized=True,
        debt_increment=10.0,
        paid_increment=3.0,
        responsibility_count=1,
        physical_expand_live=False,
        payment_progress_observed=True,
        equivalent_expand_owned=False,
    )
    old = old_generation.evaluate(partial)
    new = child_epoch.evaluate(partial)
    assert not old.unlocked and not old.allow_new_responsibility
    assert new.unlocked and new.allow_new_responsibility

    # Never stack an equivalent live Expand child.
    occupied = GenerationContext(
        generation_authorized=True,
        debt_increment=10.0,
        paid_increment=3.0,
        responsibility_count=1,
        physical_expand_live=True,
        payment_progress_observed=True,
        equivalent_expand_owned=True,
    )
    occ = child_epoch.evaluate(occupied)
    assert not occ.unlocked and not occ.allow_new_responsibility

    # No confirmed Repair progress -> stay locked.
    no_progress = GenerationContext(
        generation_authorized=True,
        debt_increment=10.0,
        paid_increment=0.0,
        responsibility_count=1,
        physical_expand_live=False,
        payment_progress_observed=False,
        equivalent_expand_owned=False,
    )
    np = child_epoch.evaluate(no_progress)
    assert not np.unlocked and not np.allow_new_responsibility

    print({
        'ok': True,
        'schedulerOffEvent': {'serial': a.reason, 'continuous': b.reason},
        'partialDebtGeneration': {'frozen': old.reason, 'candidate': new.reason},
        'occupiedGuard': occ.reason,
        'noProgressGuard': np.reason,
    })
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
