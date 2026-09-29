"""Opt-in, research-only execution control for the authorized OpenFunding student.



Ordinary precommit refusals advance to a NEW observation, never an invented HOLD.

Transport uncertainty stops the run with a truthful journal; it is NOT automatic

resumption, original order discovery, a native matcher or a policy/goal producer.

Actual native use requires the separately verified strict-return-code actuator.

"""

from collections import Counter

from copy import deepcopy

from dataclasses import asdict, is_dataclass

import math



from tools.minimal_student_system_plan_v1 import PlanRejected

from tools.minimal_student_training_world_v2 import validate_envelope, full_input

from tools.pair_core_authorized_outcome_student_bridge_v1 import make_authorized_training_class



EPS = 1e-8





class ExecutionStopped(RuntimeError):

    """A committed plan cannot safely continue without external reconciliation."""

    def __init__(self, report):

        super().__init__(report['reason'])

        self.report = report





def owner_census(student):

    """Partition committed owners; never force pending to terminal at data end.



    Exact zero is distinct from a positive subminimum fill. Native snapshot absence

    remains unknown, not proof of no fill. Economic obligation outlives a carrier.

    """

    rows = []

    mismatches = []

    for key, c in sorted(student.gateway.ledger.carriers.items()):

        transport = student.gateway.transport.get(key)

        local = student.orders.get(key)

        try:

            snap = student.snap(local) if local is not None else None

        except Exception as exc:

            snap = None

            mismatches.append(dict(key=key, reason='NATIVE_SNAPSHOT_UNAVAILABLE', detail=str(exc)))

        native_qty = snap.get('cumExecQty') if snap else None

        if native_qty is not None:

            if (isinstance(native_qty, bool) or not isinstance(native_qty, (float, int))

                    or not math.isfinite(native_qty) or native_qty < 0):

                mismatches.append(dict(key=key, reason='INVALID_NATIVE_CUMULATIVE'))

            elif abs(native_qty-c.filled) > EPS:

                mismatches.append(dict(key=key, reason='NATIVE_GATEWAY_CUMULATIVE_DIFFER',

                                       native=native_qty, gateway=c.filled))

        if transport == 'NOT_SENT_TERMINAL':

            label = 'DEFINITE_NOT_SENT'

            if c.state != 'TERMINAL' or c.filled != 0:

                mismatches.append(dict(key=key, reason='NOT_SENT_ACCOUNTING_CONTRADICTION'))

        else:

            fill = ('ZERO' if c.filled == 0 else

                    'FULL' if c.qty-c.filled <= EPS else 'PARTIAL')

            state = ('TERMINAL' if c.state == 'TERMINAL' else

                     'UNKNOWN_SEND' if transport == 'UNKNOWN' else

                     'RESERVED_UNSENT' if transport == 'RESERVED_NOT_SENT' else 'OPEN')

            label = state+'_'+fill

        rows.append(dict(key=key, parent=c.parent_id, route=c.route, carrier_state=c.state,

                         transport=transport, quantity=c.qty, confirmed_qty=c.filled,

                         confirmed_cost=c.payment+c.fees, reserved_qty=c.reserved_qty,

                         reserved_cash=c.reserved_cash, classification=label,

                         native_id_known=local is not None,

                         native_status=snap.get('status') if snap else None,

                         native_cumulative=native_qty,

                         cancel_transport=student.cancel_transport.get(key)))

    counts = Counter(r['classification'] for r in rows)

    own = student.gateway.own_state()

    for side in ('UP', 'DOWN'):

        if abs(own['inventory'][side]-student.inv[side]) > EPS:

            mismatches.append(dict(side=side, reason='CONFIRMED_INVENTORY_DIVERGENCE'))

    if abs(own['cost']-student.cost) > EPS:

        mismatches.append(dict(reason='CONFIRMED_COST_DIVERGENCE'))

    return dict(committed_owners=len(rows), partitions=dict(counts), owners=rows,

                unresolved_owners=sum(r['carrier_state'] != 'TERMINAL' for r in rows),

                legacy_all_owner_zero=sum(r['confirmed_qty'] == 0 for r in rows),

                terminal_zero=counts['TERMINAL_ZERO'],

                definite_not_sent=counts['DEFINITE_NOT_SENT'],

                mismatches=mismatches,

                accounts={str(p): student.gateway.ledger.account(p)

                          for p in student.gateway.ledger.grants},

                meaning='OWNER_PARTITION_NOT_POLICY_PERFORMANCE_OR_HISTORICAL_AUDIT_REPAIR')





def make_execution_student(frozen_minimal_class, exact_class):

    """New opt-in factory; existing runners/factories are unchanged.



    execution_sink must synchronously persist compact events. The inherited trace

    still owns native action/receipt and full input evidence. A failed sink is fatal,

    not a permission to continue sending unlogged orders. No automatic resume.

    """

    Base = make_authorized_training_class(frozen_minimal_class, exact_class)



    class AuthorizedExecutionStudent(Base):

        def __init__(self, *a, execution_sink, execution_evidence, **kw):

            if not callable(execution_sink):

                raise ValueError('SYNCHRONOUS_EXECUTION_JOURNAL_REQUIRED')

            if execution_evidence not in ('DETERMINISTIC_BOUNDARY_DOUBLE',

                                           'PINNED_NATIVE_STRICT_RC_ADAPTER'):

                raise ValueError('EXPLICIT_EXECUTION_EVIDENCE_REQUIRED')

            super().__init__(*a, **kw)

            self.execution_sink = execution_sink

            self.execution_evidence = execution_evidence

            self.execution_counts = Counter()

            self.last_execution_feedback = None

            self.cancel_transport = {}

            self.execution_started = False

            self.execution_stop = None



        def _emit(self, kind, **data):

            self.execution_sink(dict(data, kind=kind, evidence=self.execution_evidence))



        def current_frame(self, *a, **kw):

            frame = super().current_frame(*a, **kw)

            frame['execution_feedback'] = deepcopy(self.last_execution_feedback)

            frame['own_view']['execution_feedback'] = deepcopy(self.last_execution_feedback)

            return frame



        def _burn_committed_ids(self, operations):

            allocated = [int(op['key'].rsplit('_', 1)[1]) for op in operations

                         if op['kind'] == 'NEW']

            if allocated:

                self.n = max(self.n, max(allocated)+1)



        def consume(self, frame, env):

            before = self.gateway.snapshot_id()

            before_ids = set(self.gateway.committed_ids)

            before_event_count = len(self.gateway.events)

            self.execution_counts['proposals'] += 1

            try:

                if before != frame['gateway_state_id']:

                    raise PlanRejected('STALE_NATIVE_OWN_STATE')

                validate_envelope(frame, env, self.producer.policy_id,

                                  self.producer.continuation_id)

                captured = full_input(frame)

                committed = self.gateway.commit(env.plan, now_ms=frame['t'],

                                                market_end_ms=frame['end'])

            except PlanRejected as exc:

                if (self.gateway.snapshot_id() != before or self.gateway.committed_ids != before_ids

                        or len(self.gateway.events) != before_event_count):

                    raise RuntimeError('REJECTED_PLAN_MUTATED_GATEWAY') from exc

                reason = str(exc)

                status = ('RESOURCE_CENSORED_STOP' if 'RESOURCE_CENSOR' in reason else

                          'UNSUPPORTED_CAPABILITY_STOP' if 'ACTIVE_UNSUPPORTED' in reason or

                          'UNSUPPORTED_COMPLETE_PLAN' in reason else 'REJECTED_REPLAN_ON_NEXT_OBSERVATION')

                self.execution_counts['rejected'] += 1

                report = dict(status=status, reason=reason, t=frame['t'],

                              index=frame['index'], state_id=before,

                              decision_id=getattr(getattr(env, 'plan', None), 'decision_id', None),

                              generated_replacement_plan=False, target_hold_label=None)

                self.last_execution_feedback = report

                self._emit('PROPOSAL_REJECTED', **report, input_frame=full_input(frame),
                           proposed_plan=asdict(env.plan) if is_dataclass(getattr(env, 'plan', None)) else None,
                           operations=deepcopy(getattr(env, 'operations', None)))

                if status != 'REJECTED_REPLAN_ON_NEXT_OBSERVATION':

                    self.execution_counts['resource_censor' if status.startswith('RESOURCE')

                                          else 'unsupported'] += 1

                    self.execution_stop = report

                return report



            # Local commitment has happened. From here, no wholesale rollback.

            self.execution_counts['committed'] += 1

            self.frame_count += 1

            self._executed_policy_ids.add(env.plan.policy_id)

            counts = Counter(a.kind for a in env.plan.actions)

            self.plan_kinds.update(counts)

            self.multi_new_plans += counts['NEW'] > 1

            self.keep_count += counts['KEEP']

            if self.receipt_frame:

                self.post_receipt_plans += 1

            self.receipt_frame = False

            ops = env.operations

            attempted = set()

            finished = set()

            failed_op = None

            try:

                self._emit('PLAN_COMMITTED', t=frame['t'], index=frame['index'],

                           decision_id=env.plan.decision_id, actions=asdict(env.plan)['actions'],

                           admission=committed.get('outcome_admission_status'),

                           state_id=self.gateway.snapshot_id(), venue_atomic=False)

                self._refresh_slots(frame['t'])

                self.book = deepcopy(frame['book'])

                for i, op in enumerate(ops):

                    failed_op = i

                    key = op['key']

                    if op['kind'] == 'NEW':

                        free = next((s for s in range(1, self.max_slots+1)

                                     if s not in self.slot_key), None)

                        if free is None:

                            raise RuntimeError('RESOURCE_CENSOR_UNEXPECTED_ACTUATOR_CAPACITY')

                        if key != f"{op['side']}_{self.n}":

                            raise RuntimeError('NATIVE_KEY_SEQUENCE_BEFORE_CALL')

                    else:

                        if key not in self.orders:

                            raise RuntimeError('CANCEL_NATIVE_ID_UNKNOWN')

                    # Journal failure here proves the physical call was not invoked.

                    self._emit('PHYSICAL_CALL_BEGIN', t=frame['t'], index=i,

                               decision_id=env.plan.decision_id, operation=deepcopy(op))

                    attempted.add(i)

                    if op['kind'] == 'CANCEL':

                        self.cancel_transport[key] = 'UNKNOWN'

                        rc = self.bt.cancel(0, self.orders[key]['n'], False)

                        if int(rc) != 0:

                            raise RuntimeError('CANCEL_TRANSPORT_UNCERTAIN:'+str(rc))

                        self.orders[key]['cancelRequested'] = True

                        self.cancel_count += 1

                        self.cancel_transport[key] = 'REQUEST_ACCEPTED_NOT_TERMINAL'

                    else:

                        self.send_guard = True

                        try:

                            self.submit(frame['t'], op['side'], op['price'], op['qty'])

                            owner = self.orders.get(key)

                            if (owner is None or owner['side'] != op['side'] or

                                    abs(owner['qty']-op['qty']) > EPS or

                                    abs(owner['price']-op['price']) > EPS or

                                    self.n != int(key.rsplit('_', 1)[1])+1):

                                raise RuntimeError('LOCAL_IDENTITY_DIVERGENCE_AFTER_ATTEMPT')

                        except Exception:

                            if self.gateway.transport.get(key) == 'RESERVED_NOT_SENT':

                                self.gateway.record_send(key, 'UNKNOWN',

                                    evidence='PHYSICAL_NEW_ATTEMPT_NO_CONFIRMED_COMPLETION')

                            self.execution_counts['unknown_send'] += 1

                            raise

                        finally:

                            self.send_guard = False

                        self.slot_key[free] = key

                        self.key_role[key] = op['role']

                        self.role_submits[op['role']] += 1

                        self.gateway.record_send(key, 'SENT',

                            evidence='STRICT_NATIVE_ADAPTER_RETURN_WITH_LOCAL_ID')

                        self.execution_counts['sent'] += 1

                        if frame['end']-frame['t'] <= 180000:

                            self.late_new_orders += 1  # metric, never a gate

                    finished.add(i)

                    self._emit('PHYSICAL_CALL_RETURN', t=frame['t'], index=i, key=key,

                               operation_kind=op['kind'], transport=self.gateway.transport.get(key),

                               cancel_transport=self.cancel_transport.get(key))

                self._burn_committed_ids(ops)

                self.trace.plan(dict(t=frame['t'], input_frame=captured, plan=asdict(env.plan),

                    operations=ops, own_after_plan=self.gateway.own_state(),

                    policy_provenance=env.provenance, target_expert_policy_label=None,

                    policy_supervision_mask=False, event_type='COMPLETE_EXECUTION_ATTEMPT'))

                self.complete_input_frames += 1

            except Exception as exc:

                # Only uninvoked NEW operations are known not to have reached venue.

                for i, op in enumerate(ops):

                    if op['kind'] == 'NEW' and i not in attempted:

                        if self.gateway.transport.get(op['key']) != 'RESERVED_NOT_SENT':

                            raise RuntimeError('UNATTEMPTED_NEW_ALREADY_HAS_TRANSPORT') from exc

                        self.gateway.record_send(op['key'], 'NOT_SENT',

                            evidence='SERIAL_EXECUTOR_PROVES_PHYSICAL_CALL_NOT_INVOKED')

                        self.execution_counts['definite_not_sent'] += 1

                    elif op['kind'] == 'CANCEL' and i not in attempted:

                        self.cancel_transport[op['key']] = 'NOT_ATTEMPTED_CANCEL_INTENT_RETAINED'

                self._burn_committed_ids(ops)

                report = dict(status='TRANSPORT_OR_EXECUTION_FAULT_STOP', reason=str(exc),

                              t=frame['t'], index=frame['index'], failed_operation=failed_op,

                              attempted=sorted(attempted), finished=sorted(finished),

                              decision_id=env.plan.decision_id,

                              transport=dict(self.gateway.transport),

                              cancel_transport=dict(self.cancel_transport),

                              snapshot_id=self.gateway.snapshot_id(),

                              needs_external_reconciliation=True, auto_resume=False)

                self.execution_counts['execution_faults'] += 1

                self.execution_stop = report

                self.last_execution_feedback = report

                try:

                    self._emit('EXECUTION_STOP', **report)

                except Exception as log_exc:

                    report['journal_failure'] = str(log_exc)

                raise ExecutionStopped(report) from exc

            self.gateway.events.clear()

            report = dict(status='COMMITTED_AND_PHYSICAL_CALLS_COMPLETED', t=frame['t'],

                          index=frame['index'], decision_id=env.plan.decision_id,

                          admission=committed.get('outcome_admission_status'),

                          native_fills_implied=False)

            self.last_execution_feedback = report

            return report



        def run_whole(self, base):

            if self.execution_started:

                raise RuntimeError('RESUME_REQUIRES_SEPARATE_VERIFIED_RECONCILIATION')

            self.execution_started = True

            updates = sorted(self.payload['updates'], key=lambda u:(int(u[1]), int(u[0])))

            completed_source = False

            end = int(self.payload['market']['window_end_ms'])

            try:

                base.ex.advance_to(self.bt, int(self.meta['firstReceivedMs']))

                for i, u in enumerate(updates):

                    t = int(u[1])

                    base.ex.advance_to(self.bt, t)

                    self.process(t)  # canonical original receipt path, never replaced

                    book = deepcopy(self.book)

                    base.apply(book, u)

                    quotes = base.quotes(book)

                    frame = self.current_frame(t, end, book, quotes, i)

                    self.execution_counts['observations'] += 1

                    try:

                        env = self.producer.produce(deepcopy(frame))

                        self.consume(frame, env)

                    finally:

                        # Rejection must NOT drop a real market update.

                        self.book = book

                    if quotes:

                        self._sample_occupancy()

                    if self.execution_stop:

                        break

                else:

                    end2 = int(self.meta['lastReceivedMs'])

                    if updates and end2 < int(updates[-1][1]):

                        raise RuntimeError('SOURCE_FRONTIER_REGRESSION')

                    base.ex.advance_to(self.bt, end2)

                    self.process(end2)

                    self._refresh_slots(end2)

                    self._sample_occupancy()

                    completed_source = True

            except ExecutionStopped:

                pass  # preserve committed claims; no extra advance/process/retry

            except Exception as exc:

                self.execution_stop = dict(status='CONTROL_OR_RECEIPT_FAULT_STOP',

                                           reason=str(exc), auto_resume=False)

                self.execution_counts['control_faults'] += 1

            census = owner_census(self)

            self.gateway.ledger.invariants()

            status = (self.execution_stop['status'] if self.execution_stop else

                      'ACCOUNTING_MISMATCH' if census['mismatches'] else

                      'SOURCE_ENDED_WITH_UNRESOLVED_OWNERS' if census['unresolved_owners'] else

                      'SOURCE_ENDED_NO_PHYSICAL_ACTIVITY' if self.execution_counts['sent'] == 0 else

                      'SOURCE_ENDED_RECONCILED')

            result = dict(status=status, completed_source=completed_source,

                          evidence=self.execution_evidence, counts=dict(self.execution_counts),

                          owner_census=census, stop=self.execution_stop,

                          source_updates=len(updates), auto_resume=False,

                          policy_updated=False, active_native_supported=False,

                          capital_cap=self.gateway.ledger.capital)

            self._emit('RUN_END', **result)

            return result



    AuthorizedExecutionStudent.__name__ = 'AuthorizedExecutionStudentV1'

    return AuthorizedExecutionStudent

