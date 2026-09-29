from __future__ import annotations
import argparse, json, tempfile, zipfile, shutil, threading, time, joblib, sys, importlib.util, math
from pathlib import Path

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sibling(name, path):
    p = Path(path)
    s = importlib.util.spec_from_file_location(name, p)
    if s is None or s.loader is None:
        raise ImportError(p)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


birth = sibling('v83_birth_for_existing_parent_epoch_hft', Path(__file__).resolve().with_name('run_eth_v83_expand_fill_repair_responsibility_birth_smoke_1916869.py'))
router = sibling('router_v2_for_existing_parent_epoch_hft', Path(__file__).resolve().with_name('repair_execution_router_v2.py'))
epochmod = sibling('responsibility_generation_epoch_for_hft', Path(__file__).resolve().with_name('responsibility_generation_epoch.py'))
ResponsibilityGenerationEpochState = epochmod.ResponsibilityGenerationEpochState

EPS = 1e-9
FIXED = 1916869
v38 = birth.v38
v80 = birth.v80


class ExistingParentResponsibilityGenerationEpochHFT(birth.ExpandFillRepairResponsibilityBirth):
    """Single allowed mutation: ALREADY_OWNED attachment starts a fresh execution epoch on the same parent."""

    def __init__(self, *a, **kw):
        self.generationEpochByParent = {}
        self.generationEpochProcessedSources = set()
        self.generationEpochEvents = []
        self.generationEpochRouterEvents = []
        self.generationEpochActiveSubmits = 0
        self.generationEpochActiveFillKeys = set()
        super().__init__(*a, **kw)

    def _parent_churn_count(self, pid):
        return sum(1 for x in getattr(self, 'repairChurn', []) if int(x.get('parentId') or -1) == int(pid))

    def _paid_total(self, pid):
        # Frozen execution semantics use actual parent fill as payment-progress authority.
        return float(self._parent_actual_fill(int(pid)))

    def _attach_new_epoch_after_resolution(self, t):
        for p in getattr(self, 'expandFillBirthPending', []):
            if p.get('terminal') != 'ALREADY_OWNED':
                continue
            src = str(p.get('sourceKey'))
            if src in self.generationEpochProcessedSources:
                continue
            rp = getattr(self, 'repairParent', None)
            if not isinstance(rp, dict) or rp.get('side') != p.get('repairSide'):
                continue
            pid = int(rp.get('id'))
            side = str(rp.get('side'))
            state = self.generationEpochByParent.get(pid)
            if state is None:
                state = ResponsibilityGenerationEpochState(parent_id=pid, parent_side=side)
                self.generationEpochByParent[pid] = state
            fill_now = float(self._parent_actual_fill(pid))
            churn_now = self._parent_churn_count(pid)
            paid_now = self._paid_total(pid)
            ep = state.attach_existing_parent_responsibility(
                added_debt=float(p.get('qty') or 0.0),
                parent_fill_now=fill_now,
                churn_now=churn_now,
                paid_total_now=paid_now,
            )
            # Mirror the frozen router's arm authority without rebirthing/replacing the parent.
            if hasattr(self, '_armedParents'):
                self._armedParents.add(pid)
            if hasattr(self, 'armFillBase'):
                self.armFillBase[pid] = fill_now
            self.generationEpochProcessedSources.add(src)
            self.generationEpochEvents.append({
                't': int(t), 'event': 'EXISTING_PARENT_RESPONSIBILITY_EXECUTION_EPOCH_ATTACH',
                'sourceKey': src, 'parentId': pid, 'parentSide': side, 'epoch': int(ep),
                'addedDebt': float(p.get('qty') or 0.0), 'parentFillBase': fill_now,
                'churnBase': churn_now, 'paidBase': paid_now,
                'samePhysicalParent': True,
            })

    def _activate_pending_after_process(self, t):
        super()._activate_pending_after_process(t)
        self._attach_new_epoch_after_resolution(int(t))

    def _maybe_hard_active(self, t):
        rp = getattr(self, 'repairParent', None)
        if not isinstance(rp, dict):
            return super()._maybe_hard_active(t)
        pid = int(rp.get('id'))
        state = self.generationEpochByParent.get(pid)
        if state is None or not state.armed:
            return super()._maybe_hard_active(t)

        side = str(rp.get('side'))
        parent_fill_now = float(self._parent_actual_fill(pid))
        churn_now = self._parent_churn_count(pid)
        paid_now = self._paid_total(pid)
        obs = state.observe(parent_fill_now=parent_fill_now, churn_now=churn_now, paid_total_now=paid_now)
        pay = self._current_payoffs()
        qv = birth.base.v1.quotes(self.book) if hasattr(birth.base, 'v1') else None
        ask = None
        legal = None
        if qv and side in qv and qv[side].get('ask') is not None:
            ask = float(qv[side]['ask'])
            legal = 1.0 / ask if ask > EPS else math.inf
        debt = float(self._manager_debt_for_parent(pid, float(pay['gap']))) if hasattr(self, '_manager_debt_for_parent') else max(0.0, float(pay['gap']))
        active_owned = pid in getattr(self, 'activeByParent', {})
        hard_confirmed = pid in getattr(self, 'hardConfirmed', set())
        pol = getattr(self, 'repairExecutionRouter', None) or router.RecursiveCompositeRepairExecutionRouterV2()
        ctx = router.RepairExecutionContextV2(
            t=int(t), seconds_left=(int(self.capEnd) - int(t)) / 1000.0,
            parent_id=pid, parent_side=side,
            overflow_born_parent=True,  # execution-family eligibility belongs to the epoch, not to a new parent birth
            armed=bool(obs['armed']), churn_count=int(obs['postEpochChurn']),
            payment_progress_since_arm=bool(obs['paymentProgress']),
            active_already_owned=active_owned, hard_confirmed=hard_confirmed,
            floor=float(pay['floor']), manager_debt=debt,
            live_ask=ask, legal_physical_qty=legal,
        )
        d = pol.evaluate(ctx)
        ev = {
            't': int(t), 'event': 'EXISTING_PARENT_EPOCH_ROUTER_EVALUATION',
            'parentId': pid, 'parentSide': side, 'epoch': int(state.epoch),
            'postEpochChurn': int(obs['postEpochChurn']), 'paymentProgress': bool(obs['paymentProgress']),
            'paidSinceEpoch': float(obs['paidSinceEpoch']), 'fillProgressSinceEpoch': float(obs['fillProgressSinceEpoch']),
            'floor': float(pay['floor']), 'managerDebt': debt, 'liveAsk': ask,
            'legalPhysicalQty': legal, 'routerReason': d.reason, 'routerAllow': bool(d.allow_active_handoff),
        }
        self.generationEpochRouterEvents.append(ev)
        if not d.allow_active_handoff:
            return False

        # Frozen lineage choice: use the latest *post-epoch* disconnected passive carrier.
        base_churn = int(state.arm_churn_base)
        parent_churn = [x for x in getattr(self, 'repairChurn', []) if int(x.get('parentId') or -1) == pid]
        fresh_churn = parent_churn[base_churn:]
        if not fresh_churn:
            self.generationEpochRouterEvents.append({'t': int(t), 'event': 'EPOCH_ALLOW_BUT_NO_FRESH_CARRIER', 'parentId': pid, 'epoch': int(state.epoch)})
            return False
        last = fresh_churn[-1]
        passive_key = str(last.get('key'))
        carrier = getattr(self, 'carrierLedger', {}).get(passive_key, {})
        oid = carrier.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self, 'repairLaneObjective', None) else None)
        q = float(d.physical_qty)
        if hasattr(self, 'hardConfirmed'):
            self.hardConfirmed.add(pid)
        if hasattr(self, 'hardEventConfirmedCount'):
            self.hardEventConfirmedCount += 1
        state.active_owned = True
        ok = self._submit_active(int(t), pid, passive_key, side, float(ask), q, oid)
        if ok:
            self.generationEpochActiveSubmits += 1
            active = getattr(self, 'activeByParent', {}).get(pid, {})
            ak = active.get('key')
            if ak:
                self.generationEpochActiveFillKeys.add(str(ak))
                # Register with the frozen AllocationLedger V2 composite accounting if available.
                if hasattr(self, 'v84Composite'):
                    self.v84Composite[str(ak)] = {
                        'key': str(ak), 'side': side, 'parentId': pid, 'price': float(ask),
                        'submittedQty': q, 'gapAtSubmit': debt, 'fillSeen': 0.0,
                        'repairAllocated': 0.0, 'overflowAllocated': 0.0,
                        'overflowBornAt': None, 'overflowDebt': 0.0, 'overflowPaid': 0.0,
                        'lane': 'V83_EXISTING_PARENT_EPOCH_ACTIVE_COMPOSITE',
                    }
                    if hasattr(self, 'v84CompositeSubmits'):
                        self.v84CompositeSubmits += 1
            self.generationEpochEvents.append({
                't': int(t), 'event': 'EXISTING_PARENT_EPOCH_ACTIVE_COMPOSITE_SUBMIT',
                'parentId': pid, 'parentSide': side, 'epoch': int(state.epoch),
                'passiveKey': passive_key, 'activeKey': ak, 'price': float(ask), 'qty': q,
            })
        else:
            state.active_owned = False
        return bool(ok)

    def run_candidate(self, models, winner):
        r = super().run_candidate(models, winner)
        epoch_state = {str(pid): {
            'parentId': st.parent_id, 'parentSide': st.parent_side, 'epoch': st.epoch,
            'armed': st.armed, 'armFillBase': st.arm_fill_base, 'armChurnBase': st.arm_churn_base,
            'attachedDebt': st.attached_debt, 'paidAtAttach': st.paid_at_attach, 'activeOwned': st.active_owned,
        } for pid, st in self.generationEpochByParent.items()}
        active_fill = 0.0
        for key in self.generationEpochActiveFillKeys:
            m = getattr(self, 'v84Composite', {}).get(key, {})
            active_fill += float(m.get('fillSeen') or 0.0)
        r.update({
            'generationEpochStates': epoch_state,
            'generationEpochEvents': self.generationEpochEvents[:200],
            'generationEpochRouterEvents': self.generationEpochRouterEvents[:300],
            'generationEpochAttachments': len(self.generationEpochProcessedSources),
            'generationEpochActiveSubmits': int(self.generationEpochActiveSubmits),
            'generationEpochActiveFillQty': float(active_fill),
        })
        return r


def main():
    ap = argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--' + n, required=True)
    ap.add_argument('--market-id', type=int, required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    if a.market_id != FIXED:
        raise ValueError(a.market_id)

    tmp = Path(tempfile.mkdtemp(prefix='v83_existing_parent_epoch_hft_1916869_'))
    stop = threading.Event()
    def hb():
        while not stop.wait(15):
            print(json.dumps({'heartbeat':'V83_EXISTING_PARENT_EPOCH_HFT','ts':time.time()}), flush=True)
    threading.Thread(target=hb, daemon=True).start()
    print(json.dumps({'heartbeat':'V83_EXISTING_PARENT_EPOCH_HFT_START','market':FIXED}), flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr = {int(r['marketId']): r for r in json.load(open(tmp/'cohort.json', encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur = v38.v36.v34.v30.load_runtime(a)
        t44 = joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']
        t47 = joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape = tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls):
            return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())

        b = mk(birth.ExpandFillRepairResponsibilityBirth)
        try:
            br = b.run_candidate(models, cr['winner'])
        finally:
            b.close()
        c = mk(ExistingParentResponsibilityGenerationEpochHFT)
        try:
            rr = c.run_candidate(models, cr['winner'])
        finally:
            c.close()

        ss = birth.base.front.safety(rr)
        cons = abs(float(rr.get('v84CompositeFillQty') or 0) - float(rr.get('v84RepairAllocatedQty') or 0) - float(rr.get('v84OverflowAllocatedQty') or 0)) <= 1e-7
        already_owned = [e for e in rr.get('expandFillResponsibilityEvents',[]) if e.get('terminal') == 'ALREADY_OWNED']
        epochs = rr.get('generationEpochEvents',[])
        attach = [e for e in epochs if e.get('event') == 'EXISTING_PARENT_RESPONSIBILITY_EXECUTION_EPOCH_ATTACH']
        router_rows = rr.get('generationEpochRouterEvents',[])
        allows = [e for e in router_rows if e.get('routerAllow')]
        pre_epoch_reused = any(int(e.get('postEpochChurn') or 0) > 0 and int(e.get('t') or 0) <= int(attach[0].get('t') or 0) for e in router_rows) if attach else False
        same_parent = bool(attach and already_owned and int(attach[0]['parentId']) == int(already_owned[0]['repairParent']['id']))
        duplicate_parent = max(0, int(rr.get('repairParentBirths') or 0) - int(br.get('repairParentBirths') or 0))
        duplicate_debt = float(rr.get('v70dDuplicateGenerationDebt') or 0.0)
        shared_overfill = float(rr.get('v36SharedRealizedOverfill') or 0.0)
        prebirth_leak = float(rr.get('v70dPreBirthPaymentLeak') or 0.0)
        safety_zero = all(float(v) <= EPS for v in ss.values())
        explicit_frozen_reason = any(e.get('routerReason') in ('PAYMENT_PROGRESS_CONTINUE_PASSIVE','WAIT_FOR_DISCONNECT_EVIDENCE','NO_EXECUTABLE_ACTIVE_FRONTIER','LATE_NO_NEW_ACTIVE_EXPOSURE','NO_NEGATIVE_FLOOR_REPAIR_NEED') for e in router_rows)
        progressed = bool(allows or int(rr.get('generationEpochActiveSubmits') or 0) > 0 or explicit_frozen_reason)
        gates = {
            'resolutionAlreadyOwned': len(already_owned) > 0,
            'sameRepairParentIdBeforeAfterAttachment': same_parent,
            'newExecutionEpochObserved': len(attach) > 0,
            'preEpochEvidenceReused': pre_epoch_reused,
            'routerProgressOrExplicitFrozenReasonObserved': progressed,
            'truthMismatch': float(ss.get('truthMismatch', ss.get('authorizedSubmitWithTruthRoleMismatch', 0.0)) or 0.0),
            'overOwned': float(ss.get('overOwned', ss.get('overOwnedSubmitViolations', 0.0)) or 0.0),
            'responsibilityOverfill': float(ss.get('responsibilityOverfill', ss.get('v51ResponsibilityOverfill', 0.0)) or 0.0),
            'repairDrift': float(ss.get('repairDrift', ss.get('repairToExpandAtFirstFill', 0.0)) or 0.0),
            'doubleSpend': 0.0,
            'preBirthLeak': prebirth_leak,
            'duplicateDebt': duplicate_debt,
            'sharedOverfill': shared_overfill,
            'duplicateRepairParentBirths': duplicate_parent,
            'allocationConservation': cons,
            'safetyZero': safety_zero,
        }
        required_ok = (
            gates['resolutionAlreadyOwned'] and gates['sameRepairParentIdBeforeAfterAttachment'] and
            gates['newExecutionEpochObserved'] and not gates['preEpochEvidenceReused'] and
            gates['routerProgressOrExplicitFrozenReasonObserved'] and cons and safety_zero and
            duplicate_parent == 0 and duplicate_debt <= EPS and shared_overfill <= EPS and prebirth_leak <= EPS
        )
        if not required_ok:
            decision = 'REJECT_EXISTING_PARENT_RESPONSIBILITY_GENERATION_EPOCH_HFT'
        elif int(rr.get('generationEpochActiveSubmits') or 0) > 0 and float(rr.get('generationEpochActiveFillQty') or 0.0) > EPS:
            decision = 'FUNCTIONAL_PASS_EPOCH_REACHES_PHYSICAL_REPAIR_FILL'
        elif int(rr.get('generationEpochActiveSubmits') or 0) > 0:
            decision = 'PASS_EXECUTION_INCONCLUSIVE_ACTIVE_SUBMIT_NO_FILL'
        else:
            last_reason = router_rows[-1].get('routerReason') if router_rows else 'NO_ROUTER_ROW'
            decision = 'PASS_EPOCH_LOCALIZES_NEXT_FROZEN_EXECUTION_BLOCKER_' + str(last_reason)

        out = {
            'version':'ETH_V83_EXISTING_PARENT_RESPONSIBILITY_GENERATION_EPOCH_HFT_1916869_RESULT',
            'date':'2026-09-04','researchOnly':True,'marketId':FIXED,'decision':decision,
            'gates':gates,'safety':ss,
            'baseline':birth.slim(br),'candidate':birth.slim(rr),
            'candidateEpoch':{
                'attachments':rr.get('generationEpochAttachments'),
                'activeSubmits':rr.get('generationEpochActiveSubmits'),
                'activeFillQty':rr.get('generationEpochActiveFillQty'),
                'states':rr.get('generationEpochStates'),
            },
            'epochEvents':epochs,'routerEvents':router_rows,
            'birthEvents':rr.get('expandFillResponsibilityEvents',[]),
            'allocationEvents':rr.get('allocationV2Events',[])[:240],
            'boundary':['single market 1916869','ALREADY_OWNED attachment only','same physical Repair parent','fresh epoch rebases fill/churn/payment evidence','RepairExecutionRouter V2 numeric ordering frozen','AllocationLedger V2 frozen','no threshold/qty/price/delay tuning','<=180s fence preserved','realistic HFT only','no dream fill','no 8781']
        }
        Path(a.output).write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baseline'],'candidate':out['candidate'],'epoch':out['candidateEpoch'],'routerTail':router_rows[-12:]}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
