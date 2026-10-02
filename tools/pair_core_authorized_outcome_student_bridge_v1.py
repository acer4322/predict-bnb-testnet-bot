"""Opt-in research bridge to the nonbinding student's canonical receipt gateway.

The caller supplies goals, finite whole-plan quantities and endpoint authority.
No policy, funding cap, native Active implementation or simulated receipt source.
Use in a serialized event loop; local reservations are not venue-atomic orders.
"""
from copy import deepcopy
from dataclasses import asdict

from tools.minimal_student_open_funding_v1 import OpenFundingLedger
from tools.minimal_student_system_plan_v1 import PlanRejected, SystemPlan
from tools.minimal_student_training_world_v2 import (
    TrainingPlanGateway, make_training_class, digest,
)
from tools.pair_core_authorized_outcome_preview_v1 import (
    InitialPortfolio, validate_context, payoff_projection, EPS,
)


class AuthorizedOutcomeStudentGateway(TrainingPlanGateway):
    """Same receipt/owner kernel as the student, with explicit outcome admission.

OpenFundingLedger grows finite commitments from each explicit NEW intent. This
bridge does not impose the old ledger's cash/quantity limits or Passive4/Active1.
An endpoint bound is caller authority for this experiment, never a default rule
for the independent learning student. Maintenance remains possible after a bound
is tightened below current risk; receipts must always be reconciled truthfully.
"""

    def __init__(self, ledger, *, policy_id, initial, bounds):
        if not isinstance(ledger, OpenFundingLedger):
            raise ValueError('EXPLICIT_NONBINDING_STUDENT_PROFILE_REQUIRED')
        ledger.profile.validate()
        validate_context(initial, bounds)
        self.bounds = bounds
        super().__init__(
            ledger, asset=ledger.profile.asset, policy_id=policy_id,
            capabilities=('PASSIVE',),
            initial_inventory={'UP': initial.up, 'DOWN': initial.down},
            initial_cost=initial.cost, tick=ledger.profile.tick,
            quantity_step=ledger.profile.quantity_step,
        )

    def initial_portfolio(self):
        return InitialPortfolio(self.initial_inventory['UP'],
                                self.initial_inventory['DOWN'], self.initial_cost)

    def outcome_feedback(self):
        projection = payoff_projection(self.ledger, self.initial_portfolio())
        limits = {'UP': self.bounds.min_up_payoff, 'DOWN': self.bounds.min_down_payoff}
        violations = {s: {'worst': projection['coordinate_worst'][s], 'required': limits[s]}
                      for s in limits if projection['coordinate_worst'][s] < limits[s] - EPS}
        return dict(authority=asdict(self.bounds), projection=projection,
                    violations=violations, within_authority=not violations,
                    funding=self.ledger.funding_demand(),
                    native_supported_routes=['PASSIVE'], native_active_supported=False)

    def own_state(self):
        state = super().own_state()
        state['authorized_outcome'] = self.outcome_feedback()
        return state

    def snapshot_id(self):
        return digest(dict(base=super().snapshot_id(), bounds=asdict(self.bounds),
                           tick=self.tick, quantity_step=self.step))

    def replace_endpoint_authority(self, bounds):
        validate_context(self.initial_portfolio(), bounds)
        before = self.snapshot_id()
        self.bounds = bounds
        self.events.append(dict(event='EXTERNAL_ENDPOINT_AUTHORITY_REPLACED',
                                before=before, after=self.snapshot_id(),
                                outcome=self.outcome_feedback()))

    def _stage(self, plan, *, now_ms, market_end_ms):
        if not isinstance(plan, SystemPlan):
            raise PlanRejected('WHOLE_PLAN_AND_CONTINUATION_REQUIRED')
        self.ledger.profile.validate()
        if self.ledger.capital is not None or self.capabilities != frozenset({'PASSIVE'}):
            raise PlanRejected('STUDENT_PROFILE_OR_NATIVE_CAPABILITY_CHANGED')
        if (self.asset, self.tick, self.step) != (self.ledger.profile.asset,
                self.ledger.profile.tick, self.ledger.profile.quantity_step):
            raise PlanRejected('STUDENT_NUMERICAL_PROFILE_MISMATCH')
        live = {k for k, c in self.ledger.carriers.items() if c.state != 'TERMINAL'}
        if live != {a.key for a in plan.actions if a.kind in ('KEEP', 'CANCEL')}:
            raise PlanRejected('WHOLE_LIVE_OWNER_SET_MUST_BE_EXPLICIT')
        if any(a.kind == 'NEW' and a.route == 'ACTIVE' for a in plan.actions):
            raise PlanRejected('NATIVE_ACTIVE_UNSUPPORTED_NOT_HOLD')
        trial = deepcopy(self)
        event = TrainingPlanGateway.commit(trial, plan, now_ms=now_ms,
                                           market_end_ms=market_end_ms)
        feedback = trial.outcome_feedback()
        new = any(a.kind == 'NEW' for a in plan.actions)
        accepted = feedback['within_authority'] or not new
        status = ('DECLARED_ENDPOINT_AUTHORITY_EXCEEDED' if not accepted else
                  'MAINTENANCE_ONLY_OUTSIDE_AUTHORITY' if not feedback['within_authority'] else
                  'ADMISSIBLE_RESERVATION_NOT_EXECUTION')
        event['authorized_outcome'] = feedback
        event['outcome_admission_status'] = status
        return trial, dict(accepted=accepted, status=status, outcome=feedback,
                           snapshot=self.snapshot_id(), native_submitted=False)

    def preview_plan(self, plan, *, now_ms, market_end_ms):
        try:
            _, report = self._stage(plan, now_ms=now_ms, market_end_ms=market_end_ms)
            return report
        except PlanRejected as exc:
            return dict(accepted=False, status=str(exc), snapshot=self.snapshot_id(),
                        outcome=None, native_submitted=False)

    def commit(self, plan, *, now_ms, market_end_ms):
        trial, report = self._stage(plan, now_ms=now_ms, market_end_ms=market_end_ms)
        if not report['accepted']:
            raise PlanRejected(report['status'])
        # Publish only after both the existing complete-plan checks and endpoint
        # admission pass. Rejection cannot grow funding, claim an id or cancel.
        self.ledger = trial.ledger
        self.transport = trial.transport
        self.active_continuation = trial.active_continuation
        self.committed_ids = trial.committed_ids
        self.events = trial.events
        return self.events[-1]


def make_authorized_training_class(frozen_minimal_class, exact_class):
    """Opt-in to existing native submit/process/terminal paths, all unchanged.

No worker runner uses this factory by default. Native runs must start flat with
an empty owner ledger; arbitrary synthetic initial portfolios belong to gateway
tests only. The producer receives current external authority with own feedback.
"""
    Base = make_training_class(frozen_minimal_class, exact_class)

    class AuthorizedOutcomeTrainingStudent(Base):
        def __init__(self, *args, endpoint_authority, **kwargs):
            ledger = kwargs.get('ledger')
            if not isinstance(ledger, OpenFundingLedger) or ledger.carriers:
                raise ValueError('EMPTY_NONBINDING_NATIVE_LEDGER_REQUIRED')
            validate_context(InitialPortfolio(0., 0., 0.), endpoint_authority)
            super().__init__(*args, **kwargs)
            if self.inv != {'UP': 0., 'DOWN': 0.} or self.cost != 0.:
                raise ValueError('NATIVE_INITIAL_ACCOUNTING_NOT_FLAT')
            self.gateway = AuthorizedOutcomeStudentGateway(
                self.gateway.ledger, policy_id=self.producer.policy_id,
                initial=InitialPortfolio(0., 0., 0.), bounds=endpoint_authority,
            )

        def current_frame(self, *args, **kwargs):
            frame = super().current_frame(*args, **kwargs)
            frame['authorized_outcome'] = self.gateway.outcome_feedback()
            # The unchanged full_input exporter serializes own_view. Preserve
            # exact input authority there as well as in post-plan own_state.
            frame['own_view']['authorized_outcome'] = deepcopy(frame['authorized_outcome'])
            return frame

    return AuthorizedOutcomeTrainingStudent
