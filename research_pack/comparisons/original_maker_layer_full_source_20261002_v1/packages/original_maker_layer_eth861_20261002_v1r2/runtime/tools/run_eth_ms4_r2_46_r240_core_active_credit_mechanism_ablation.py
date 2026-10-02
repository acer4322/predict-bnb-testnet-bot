from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_40_handoff_repair_credit_quarantine as r240

r239=r240.r239
r28=r240.r28
v2=r28.v2
r1=r28.r1
EPS=1e-9

class R240OneCoreActiveMechanismSim(r240.HandoffRepairCreditQuarantineSim):
    """R2.46 research-only mechanism ablation on the exact R2.40 branch.

    Existing R2.40 behavior executes first. Genuine terminal zero-fill ECONOMIC_CORE
    evidence is held in a separate diagnostic service queue so merely observing it
    cannot block or reorder the frozen SATELLITE_REPAIR failure-evidence queue.
    After frozen R2.40 slot refresh/drain runs, one bounded Core-derived Active
    Repair may use the existing R2.2 actuator if the same strict-past conditions hold.

    mode ACTIVE_CREDIT_QUARANTINE removes only same-generation continuation credit
    attributable to confirmed Repair allocation on that one Core-derived Active key.
    """
    def __init__(self,tape,mode='ACTIVE',fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r246Mode=str(mode).upper()
        self.coreSeen=set()
        self.pendingCoreEvidence=None
        self.coreActiveMaterialized=False
        self.coreActiveKey=None
        self.coreActiveIntervention=None
        self.r246=Counter();self.r246Events=[]
        self.quarantinedCoreActiveCredit=0.0
        self.coreActiveRepairAllocated=0.0
        self.coreActiveCreditObserved=0.0
        self.coreActiveFillEvents=0

    def _discover_core_terminal(self,t:int):
        if self.coreActiveMaterialized or self.pendingCoreEvidence is not None:return
        for sid,key in list(self.slot_key.items()):
            if key in self.coreSeen:continue
            if self.key_role.get(key)!='ECONOMIC_CORE':continue
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            if status not in v2.TERMINAL_STATUSES:continue
            self.coreSeen.add(key)
            if cum>EPS or bool(o.get('cancelRequested')):
                self.r246['CORE_TERMINAL_NOT_GENUINE']+=1
                continue
            ev={'t':int(t),'event':'R246_CORE_GENUINE_ZERO_FILL_EVIDENCE','sourceKey':key,
                'sourceRole':'ECONOMIC_CORE','generation':int(self.key_scope_gen.get(key,-1)),
                'repairProgressClock':int(self.scopeRepairProgressClocks),'side':str(o['side']),
                'sourcePrice':float(o['price']),'terminalStatus':status}
            self.pendingCoreEvidence=ev;self.r246['CORE_EVIDENCE_DISCOVERED']+=1
            self.r246Events.append(ev);self.slot_history.append(ev)
            break

    def _strict_past_snapshot(self,t:int,ev,ap=None,aq=None):
        qv=r1.v2.base.quotes(self.book)
        upmid=None;imb=None
        if qv:
            try:upmid=(float(qv['UP']['bid'])+float(qv['UP']['ask']))/2.0
            except Exception:pass
            try:imb=float(qv.get('imb') or 0.0)
            except Exception:pass
        ob=self._active_obligation()
        repair_side=self._repair_side()
        return {
            'decisionT':int(t),'sourceKey':ev.get('sourceKey'),'sourceRole':ev.get('sourceRole'),
            'sourcePrice':float(ev.get('sourcePrice') or 0.0),'sourceGeneration':int(ev.get('generation',-1)),
            'sourceRepairProgressClock':int(ev.get('repairProgressClock',-1)),
            'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,
            'repairProgressClock':int(self.scopeRepairProgressClocks),'repairSide':repair_side,
            'scopeDebtQty':float(self._scope_debt_qty()),
            'reservedRepairQuota':float(self._reserved_repair_quota(repair_side)) if repair_side else 0.0,
            'activeReservedRepair':float(self._active_reserved_repair()),
            'physicalFloor':float(self._physical_floor()),
            'physicalBest':float(max(float(self.inv['UP']),float(self.inv['DOWN']))-float(self.cost)),
            'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),'cost':float(self.cost),
            'riskCreditTotal':float(self.scopeRiskCreditTotal),'riskCreditConsumed':float(self.scopeRiskCreditConsumed),
            'riskCreditReserved':float(self._reserved_current_expand_risk()),
            'riskCreditAvailable':float(self._available_expand_risk_credit()),
            'activeAsk':None if ap is None else float(ap),'activeVenueMinQty':None if aq is None else float(aq),
            'bookImbalance':imb,'upMid':upmid,
            'liveRoles':[{'key':k,'role':role,'side':str(o['side']),'price':float(o['price']),
                          'remaining':float(self._remaining(k)),'generation':self.key_scope_gen.get(k)}
                         for _,k,o,role in self._live_role_rows()],
            'activeKeys':[str(k) for k in sorted(self.activeKeys)],
            'handoffObligation':None if ob is None else {'id':int(ob['id']),'side':ob['side'],'generation':int(ob['generation']),
                                                         'outstanding':float(ob['outstanding'])},
        }

    def _try_core_active(self,t:int):
        if self.coreActiveMaterialized or self.pendingCoreEvidence is None:return False
        ev=self.pendingCoreEvidence;gen=int(ev['generation']);side=str(ev['side']);epoch=(gen,int(ev['repairProgressClock']))
        if self.scopeSide is None or int(self.scopeGeneration)!=gen or side!=self._repair_side():
            self.r246['CORE_EVIDENCE_STALE_DROP']+=1;self.pendingCoreEvidence=None;return False
        if epoch in self.usedEpochs:
            self.r246['CORE_EPOCH_ALREADY_DRAINED']+=1;self.pendingCoreEvidence=None;return False
        if self._has_live_active():
            self.r246['CORE_ACTIVE_ALREADY_LIVE_WAIT']+=1;return False
        qv=r1.v2.base.quotes(self.book)
        if not qv or qv.get(side,{}).get('ask') is None:
            self.r246['CORE_NO_ACTIVE_ASK_WAIT']+=1;return False
        ap=float(qv[side]['ask']);aq=1.0/ap if ap>EPS else math.inf
        if not math.isfinite(aq) or aq<=EPS or aq>12.0+EPS:
            self.r246['CORE_BAD_ACTIVE_MIN_QTY']+=1;self.pendingCoreEvidence=None;return False
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.0,debt-reserved)
        if avail+EPS<aq:
            self.r246['CORE_RESIDUAL_DEBT_BELOW_ACTIVE_MIN_WAIT']+=1;return False
        before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,ap,aq))
        if after<=before+EPS:
            self.r246['CORE_ACTIVE_NOT_FLOOR_IMPROVING']+=1;self.pendingCoreEvidence=None;return False
        pre=self._strict_past_snapshot(t,ev,ap,aq)
        before_exec=len(self.executionDecisions)
        if not self._submit_active(t,side,'SATELLITE_REPAIR',aq,0.0,{'rank':None,'depth':None}):
            self.r246['CORE_ACTIVE_SUBMIT_BLOCKED']+=1;return False
        active_submit=next((x for x in reversed(self.executionDecisions[before_exec:]) if x.get('event')=='MS4_R2_ACTIVE_REPAIR_SUBMIT'),None)
        if active_submit is None or not active_submit.get('key'):
            raise RuntimeError('Core Active submit materialized without authoritative MS4_R2_ACTIVE_REPAIR_SUBMIT key')
        self.usedEpochs.add(epoch);self.coreActiveMaterialized=True;self.coreActiveKey=str(active_submit['key']);self.pendingCoreEvidence=None
        self.r246['CORE_ACTIVE_MATERIALIZED']+=1
        rec={**pre,'event':'R246_CORE_ACTIVE_MATERIALIZED','activeSubmitT':int(t),'activeKey':self.coreActiveKey,
             'activePrice':float(ap),'activeQty':float(aq),'candidateFloor':float(after),
             'submitRc':active_submit.get('submitRc')}
        self.coreActiveIntervention=rec;self.r246Events.append(rec);self.slot_history.append(rec)
        return True

    def _refresh_slots(self,t:int):
        self._discover_core_terminal(t)
        # Frozen R2.40/SATELLITE evidence gets first service priority.
        super()._refresh_slots(t)
        self._try_core_active(t)

    def process(self,t):
        old_scope=self.scopeSide;old_gen=int(self.scopeGeneration);before_n=len(self.splitEvents)
        super().process(t)
        qcredit=0.0;details=[]
        for ev in self.splitEvents[before_n:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            if self.coreActiveKey is None or str(ev.get('key'))!=self.coreActiveKey:continue
            rq=float(ev.get('repairAllocated') or 0.0)
            if rq<=EPS:continue
            price=float(ev.get('price') or 0.0);credit=rq*(1.0-price)
            self.coreActiveRepairAllocated+=rq;self.coreActiveCreditObserved+=credit;self.coreActiveFillEvents+=1;qcredit+=credit
            details.append({'key':self.coreActiveKey,'repairQty':rq,'price':price,'credit':credit,'fillInc':float(ev.get('fillInc') or 0.0)})
        if qcredit<=EPS:return
        same_scope=(self.scopeSide==old_scope and int(self.scopeGeneration)==old_gen)
        obs={'t':int(t),'event':'R246_CORE_ACTIVE_REPAIR_FILL_CREDIT_OBSERVED','mode':self.r246Mode,
             'sameScopeGeneration':bool(same_scope),'creditObserved':qcredit,'details':details,
             'riskCreditTotalAfterBaseProcess':float(self.scopeRiskCreditTotal),'riskCreditConsumed':float(self.scopeRiskCreditConsumed),
             'riskCreditReserved':float(self._reserved_current_expand_risk())}
        self.r246Events.append(obs);self.slot_history.append(obs)
        if self.r246Mode!='ACTIVE_CREDIT_QUARANTINE' or not same_scope:return
        before=float(self.scopeRiskCreditTotal)
        protected=max(0.0,float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk()))
        removable=max(0.0,before-protected);removed=min(qcredit,removable)
        self.scopeRiskCreditTotal=max(protected,before-removed)
        self.quarantinedCoreActiveCredit+=removed
        if removed+EPS<qcredit:self.r246['CORE_ACTIVE_CREDIT_QUARANTINE_SHORTFALL']+=1
        self.r246['CORE_ACTIVE_CREDIT_QUARANTINED']+=1
        qe={'t':int(t),'event':'R246_CORE_ACTIVE_CREDIT_QUARANTINED','creditObserved':qcredit,'creditRemoved':removed,
            'creditNotRemoved':max(0.0,qcredit-removed),'protectedConsumedPlusReserved':protected,
            'riskCreditTotalBefore':before,'riskCreditTotalAfter':float(self.scopeRiskCreditTotal)}
        self.r246Events.append(qe);self.slot_history.append(qe)

    def run_r246(self,winner):
        r=super().run_r240(winner)
        r.update({'r246Mode':self.r246Mode,'r246Stats':dict(self.r246),'r246Events':self.r246Events[:1600],
                  'r246CoreActiveMaterialized':bool(self.coreActiveMaterialized),'r246CoreActiveKey':self.coreActiveKey,
                  'r246CoreActiveIntervention':self.coreActiveIntervention,
                  'r246CoreActiveFillEvents':int(self.coreActiveFillEvents),
                  'r246CoreActiveRepairAllocated':float(self.coreActiveRepairAllocated),
                  'r246CoreActiveCreditObserved':float(self.coreActiveCreditObserved),
                  'r246QuarantinedCoreActiveCredit':float(self.quarantinedCoreActiveCredit)})
        return r

def _clean_row(mid,cell,winner,r):return {'marketId':int(mid),'cell':cell,'winnerPostHocOnly':winner,**r}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r246_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];comparison=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';w=cr['winner']
            s0=r240.HandoffRepairCreditQuarantineSim(tape,1,4)
            try:b=s0.run_r240(w)
            finally:s0.close()
            s1=R240OneCoreActiveMechanismSim(tape,'ACTIVE',1,4)
            try:c1=s1.run_r246(w)
            finally:s1.close()
            s2=R240OneCoreActiveMechanismSim(tape,'ACTIVE_CREDIT_QUARANTINE',1,4)
            try:c2=s2.run_r246(w)
            finally:s2.close()
            rows += [_clean_row(mid,'R240_CONTROL',w,b),_clean_row(mid,'R246_R240_ONE_CORE_ACTIVE',w,c1),
                     _clean_row(mid,'R246_R240_ONE_CORE_ACTIVE_CREDIT_QUARANTINE',w,c2)]
            d={'marketId':mid,
               'activeMaterialized':bool(c1.get('r246CoreActiveMaterialized')),
               'quarantineMaterialized':bool(c2.get('r246CoreActiveMaterialized')),
               'activeKey':c1.get('r246CoreActiveKey'),'quarantineActiveKey':c2.get('r246CoreActiveKey'),
               'activeVsR240Pnl':float(c1['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'activeVsR240Floor':float(c1['floor'])-float(b['floor']),
               'activeVsR240Best':float(c1['best'])-float(b['best']),
               'quarantineVsR240Pnl':float(c2['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'quarantineVsR240Floor':float(c2['floor'])-float(b['floor']),
               'quarantineVsR240Best':float(c2['best'])-float(b['best']),
               'quarantineVsActivePnl':float(c2['pnlDiagnosticOnly'])-float(c1['pnlDiagnosticOnly']),
               'quarantineVsActiveFloor':float(c2['floor'])-float(c1['floor']),
               'activeFillEvents':int(c1.get('r246CoreActiveFillEvents',0)),
               'activeRepairAllocated':float(c1.get('r246CoreActiveRepairAllocated',0.0)),
               'activeCreditObserved':float(c1.get('r246CoreActiveCreditObserved',0.0)),
               'creditQuarantined':float(c2.get('r246QuarantinedCoreActiveCredit',0.0)),
               'r240Fills':int(b['fillEvents']),'activeFills':int(c1['fillEvents']),'quarantineFills':int(c2['fillEvents']),
               'r240Submits':int(b['submits']),'activeSubmits':int(c1['submits']),'quarantineSubmits':int(c2['submits']),
               'activeUnauthorizedOverflowQty':float(c1.get('unauthorizedOverflowQty',0.0)),
               'quarantineUnauthorizedOverflowQty':float(c2.get('unauthorizedOverflowQty',0.0)),
               'activeRepairQuotaExcessMax':float(c1.get('repairQuotaExcessMax',0.0)),
               'quarantineRepairQuotaExcessMax':float(c2.get('repairQuotaExcessMax',0.0))}
            comparison.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        cand=[r for r in rows if r['cell']!='R240_CONTROL']
        gates={'allThreeCellsComplete':len(rows)==3*len(mids),
               'atMostOneCoreActivePerCandidateMarket':all(int(r.get('r246Stats',{}).get('CORE_ACTIVE_MATERIALIZED',0))<=1 for r in cand),
               'correctnessPass':all(float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS for r in cand),
               'coreInterventionExercised':any(bool(r.get('r246CoreActiveMaterialized')) for r in cand),
               'noForcedIntervention':True}
        out={'version':'MS4_R2_46_R240_CORE_ACTIVE_CREDIT_MECHANISM_ABLATION_V1','researchOnly':True,'runtimeAuthority':False,
             'markets':mids,'rows':rows,'comparison':comparison,'gates':gates,
             'boundary':['R2.40 is exact control/base path','existing R2.40/SATELLITE failure-evidence service executes before separate Core service evidence','at most one Core-derived Active per candidate market','Core evidence itself cannot block/reorder frozen pendingFailure queue','Active is existing pure-Repair current-ask venue-min actuator','credit ablation removes only same-generation credit attributable to confirmed Core-derived Active Repair fill','no market-specific policy','winner post-hoc only','realistic HFT','no dream fill','<=180s unchanged','no 8781','mechanism evidence only; no promotion']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
        op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'comparison':comparison},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
