from __future__ import annotations


class ContinuousResponsibilitySchedulerAdapterMixin:
    """Adapter from a modular ResponsibilityScheduler to the legacy V83 seam.

    The scheduler never authorizes a physical action. When it requests a
    reevaluation, this adapter invokes the existing `_score_state` admission
    path. Every downstream authority (ownership, responsibility transition,
    recoverability, generation/dedup, occupancy, qty/price and execution) stays
    unchanged.

    The legacy V83 API is event-bound and expects after_kind='REPAIR'. Until the
    admission seam is separately refactored, scheduler-triggered reevaluation
    uses that token only as an API adapter and records the clock source.
    """
    scheduler_adapter_name = 'continuous_responsibility_scheduler_adapter_v1'

    def _init_scheduler_adapter(self) -> None:
        self.schedulerAdapterEvents = []
        self.schedulerAdapterReevaluations = 0
        self.schedulerAdapterSkippedSameReceipt = 0
        self.schedulerAdapterAdmissionChecks = 0
        self.schedulerAdapterAdmissionAllows = 0

    def _scheduler_after_process(self, t: int):
        if not hasattr(self, 'schedulerAdapterEvents'):
            self._init_scheduler_adapter()
        t = int(t)
        dec = self._scheduler_decision(t, after_kind=None, receipt_advanced=True)
        if dec is None or not bool(dec.reevaluate_management):
            return None

        rows = getattr(self, 'v83Admissions', [])
        if any(int(r.get('t') or -1) == t for r in rows):
            self.schedulerAdapterSkippedSameReceipt += 1
            self.schedulerAdapterEvents.append({
                't': t,
                'event': 'SCHEDULER_SKIP_EXISTING_EVENT_CLOCK',
                'schedulerReason': dec.reason,
            })
            return None

        before_checks = int(getattr(self, 'v83AdmissionChecks', 0) or 0)
        before_allows = int(getattr(self, 'v83AdmissionAllows', 0) or 0)
        self.schedulerAdapterReevaluations += 1
        self._schedulerClockSource = 'CONTINUOUS_RESPONSIBILITY_RECEIPT'
        try:
            out = self._score_state(t, 'REPAIR')
        finally:
            self._schedulerClockSource = None

        after_checks = int(getattr(self, 'v83AdmissionChecks', 0) or 0)
        after_allows = int(getattr(self, 'v83AdmissionAllows', 0) or 0)
        dc = max(0, after_checks - before_checks)
        da = max(0, after_allows - before_allows)
        self.schedulerAdapterAdmissionChecks += dc
        self.schedulerAdapterAdmissionAllows += da
        self.schedulerAdapterEvents.append({
            't': t,
            'event': 'SCHEDULER_MANAGEMENT_REEVALUATION',
            'schedulerReason': dec.reason,
            'admissionChecksDelta': dc,
            'admissionAllowsDelta': da,
            'actionReturned': bool(out),
        })
        return out

    def scheduler_adapter_metrics(self):
        return {
            'module': self.scheduler_adapter_name,
            'reevaluations': int(getattr(self, 'schedulerAdapterReevaluations', 0) or 0),
            'skippedSameReceipt': int(getattr(self, 'schedulerAdapterSkippedSameReceipt', 0) or 0),
            'admissionChecksFromScheduler': int(getattr(self, 'schedulerAdapterAdmissionChecks', 0) or 0),
            'admissionAllowsFromScheduler': int(getattr(self, 'schedulerAdapterAdmissionAllows', 0) or 0),
            'events': list(getattr(self, 'schedulerAdapterEvents', []))[:400],
        }
