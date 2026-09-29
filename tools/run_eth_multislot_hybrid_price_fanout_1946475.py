from __future__ import annotations
import argparse,json,math,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))

import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as lock
import tools.run_eth_reanchor_handoff_lease_1946475 as lease
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base

EPS=1e-9;MID=1946475;pe=base.pe
ECON={'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'rollingFillQty':0.0,'activeRepairFillQty':1.639344262295082}
FULL={'pnlDiagnosticOnly':-0.9282608695652179,'floor':-0.9282608695652179,'rollingFillQty':2.1739130434782608,'activeRepairFillQty':0.0}

class HybridPriceFanoutHFT(lock.EconomicHandoffLeaseLockHFT):
    def __init__(self,*a,**kw):
        self.fanoutEvents=[];self.fanoutOpportunities=0;self.fanoutCancelRequests=0;self.fanoutSubmits=0;self.fanoutKeys=set()
        super().__init__(*a,**kw)

    def _request_cancel(self,t,row,reason):
        if str(reason)!='FRONTIER_REANCHOR':
            return super()._request_cancel(int(t),row,reason)
        side=str(row.get('side') or '').upper();target,bid,ask,pdec=self._priority_target(side)
        if target is None:return False
        pid=int(row['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));rem=float(row.get('remaining') or 0.0);hyp=avail+rem;full_qty=min(rem,hyp)
        full=self._econ(int(t),side,full_qty,float(target))
        # Preserve the already-preregistered economic lease path whenever the full replacement is admissible.
        if bool(full.get('allow')):
            return super()._request_cancel(int(t),row,reason)
        # Fanout is only a second execution option: require a genuine inside-spread priority opportunity.
        if not bool(getattr(pdec,'improved',False)):
            return False
        rows=self._managed_live(int(t));others=[r for r in rows if r['key']!=row['key'] and int(r.get('parentId') or -1)==pid and str(r.get('side') or '').upper()==side]
        if not others:
            self.fanoutEvents.append({'t':int(t),'event':'HYBRID_FANOUT_BLOCK','parentId':pid,'oldKey':row['key'],'reason':'NO_OTHER_LIVE_ECONOMIC_CORE','targetPrice':float(target)})
            return False
        legal=1.0/float(target) if float(target)>EPS else math.inf
        q=min(float(legal),rem,hyp)
        self.fanoutOpportunities+=1
        min_econ=self._econ(int(t),side,q,float(target)) if math.isfinite(legal) and q>EPS else {'allow':False,'reason':'INVALID_MIN_TRANCHE'}
        before=float(self._current_payoffs().get('floor') or 0.0);plans=[(float(r['price']),float(r['remaining'])) for r in others]+[(float(target),float(q))]
        after,floors=self._floor_after_buys(side,plans)
        joint_safe=after is not None and after>=before-1e-7 and all(float(x)>=before-1e-7 for x in floors)
        ev={'t':int(t),'event':'HYBRID_FANOUT_PREFLIGHT','parentId':pid,'oldKey':row['key'],'oldPrice':float(row['price']),'coreLive':[{'key':r['key'],'price':float(r['price']),'remaining':float(r['remaining'])} for r in others],
            'bestBid':bid,'bestAsk':ask,'priorityTarget':float(target),'priorityReason':getattr(pdec,'reason',None),'authoritativeDebt':debt,'availableNow':avail,'availableAfterOwnTerminalHyp':hyp,'oldRemaining':rem,'venueMinQty':legal,'fanoutQty':q,
            'fullReplacementEconomic':full,'minTrancheEconomicAuditOnly':min_econ,'floorBefore':before,'jointFloorAfter':after,'jointIncrementalFloors':floors,'jointFloorSafe':bool(joint_safe)}
        if not math.isfinite(legal) or q+EPS<legal or q<=EPS:
            ev['decision']='BLOCK_NO_LEGAL_MIN_TRANCHE';self.fanoutEvents.append(ev);return False
        if q>rem+EPS:
            ev['decision']='BLOCK_QUANTITY_UPLIFT';self.fanoutEvents.append(ev);return False
        if not joint_safe:
            ev['decision']='BLOCK_JOINT_WORST_CASE_FLOOR';self.fanoutEvents.append(ev);return False
        # Bypass only the full-quantity V16 reprice veto. All cancellation, cancel-pending reservation,
        # handoff lease, and parent occupancy semantics stay inherited.
        ok=lease.ReanchorHandoffLeaseHFT._request_cancel(self,int(t),row,reason)
        ev['cancelRequested']=bool(ok)
        if ok and isinstance(getattr(self,'pendingRollingCancel',None),dict):
            self.fanoutCancelRequests+=1
            self.pendingRollingCancel.update({'hybridFanoutApprovedPrice':float(target),'hybridFanoutApprovedQty':float(q),'hybridFanoutApprovedAt':int(t),'hybridFanoutCoreKeys':[str(r['key']) for r in others]})
            ev['decision']='CANCEL_WITH_MIN_TRANCHE_FANOUT_LEASE'
        else:ev['decision']='CANCEL_REQUEST_FAILED'
        self.fanoutEvents.append(ev);return bool(ok)

    def _submit_replacement(self,t,meta,rows,best):
        if not isinstance(meta,dict) or meta.get('hybridFanoutApprovedPrice') is None:
            return super()._submit_replacement(int(t),meta,rows,best)
        olde=getattr(self,'carrierLedger',{}).get(str(meta['key']),{})
        if not bool(olde.get('terminalConfirmed')):return False
        pid=int(meta['parentId']);side=str(meta['side']).upper();oid=meta.get('objectiveId');target=float(meta['hybridFanoutApprovedPrice']);approved_qty=float(meta['hybridFanoutApprovedQty'])
        if oid is None:return False
        debt,legal,structural=self._structural_slot_capacity(pid,side,target)
        if structural<self.slotCeiling:
            old=self.slotCeiling;self.slotCeiling=structural;self.contractionEvents+=1
            self.repeatedRollingEvents.append({'t':int(t),'event':'SLOT_CAPACITY_CONTRACTION','parentId':pid,'side':side,'oldCeiling':old,'newCeiling':self.slotCeiling,'authoritativeDebt':debt,'legalQtyAtBest':legal,'reason':'DEBT_NO_LONGER_SUPPORTS_PRIOR_VENUE_MIN_SLOT_COUNT'})
        live=[r for r in rows if r['key']!=meta['key']]
        if self.slotCeiling<=0 or len(live)>=self.slotCeiling:
            self.fanoutEvents.append({'t':int(t),'event':'HYBRID_FANOUT_REPLACEMENT_BLOCK','parentId':pid,'oldKey':meta['key'],'reason':'NO_FREE_STRUCTURAL_SLOT','slotCeiling':self.slotCeiling,'liveCount':len(live)});self.pendingRollingCancel=None;return False
        self._sync_parent_occupancy();available=float(self.parentExecutionOccupancy.available(pid,debt));qty=min(approved_qty,available)
        row={'t':int(t),'event':'HYBRID_FANOUT_REPLACEMENT_EVALUATION','oldKey':meta['key'],'side':side,'parentId':pid,'objectiveId':oid,'targetPrice':target,'approvedQty':approved_qty,'replacementQty':qty,'authoritativeDebt':debt,'availableQty':available,'legalQty':legal,'slotCeiling':self.slotCeiling,'otherLive':[{'key':r['key'],'price':r['price'],'remaining':r['remaining']} for r in live]}
        if qty+EPS<legal or qty<=EPS:
            row['decision']='BLOCK_NO_LEGAL_REPLACEMENT_QTY';self.fanoutEvents.append(row);return False
        before=float(self._current_payoffs().get('floor') or 0.0);plans=[(r['price'],r['remaining']) for r in live]+[(target,qty)];after,floors=self._floor_after_buys(side,plans);row.update({'floorBefore':before,'floorAfterAllLiveOptionsFill':after,'incrementalFloors':floors})
        if after is None or after<before-1e-7 or any(float(x)<before-1e-7 for x in floors):
            row['decision']='BLOCK_JOINT_WORST_CASE_FLOOR_DAMAGE';self.fanoutEvents.append(row);return False
        n0=int(self.n);self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='HYBRID_PRICE_FANOUT_PRIORITY_MIN_TRANCHE'
        prevflag=bool(getattr(self,'_allowSameSideParallelReservation',False));self._allowSameSideParallelReservation=True
        try:ok=base.InitialRepairQuotaPartitionHFT.submit(self,int(t),side,target,qty)
        finally:self._allowSameSideParallelReservation=prevflag
        row['submit']=bool(ok)
        if not ok:
            row['decision']='SUBMIT_FAILED';self.fanoutEvents.append(row);return False
        key=f'{side}_{n0}';self.partitionKeys.append(key);self.fanoutKeys.add(key);self.fanoutSubmits+=1;self.repeatedRollingSubmits+=1;self.reanchorCycles+=1;self.pendingRollingCancel=None
        row.update({'decision':'HYBRID_MIN_TRANCHE_FANOUT_SUBMITTED','replacementKey':key});self.fanoutEvents.append(row);return True

    def run_hybrid(self,models,winner):
        r=self.run_locked(models,winner);self._refresh_carrier_ledger(int(self.capEnd));fills=[];fillqty=0.0
        for key in sorted(self.fanoutKeys):
            e=getattr(self,'carrierLedger',{}).get(str(key),{});q=float(e.get('actualFilled') or 0.0);fillqty+=q;fills.append({'key':str(key),'submittedQty':float(e.get('submittedQty') or 0.0),'actualFilled':q,'terminalConfirmed':bool(e.get('terminalConfirmed')),'parentId':e.get('parentId'),'role':e.get('objectiveRole'),'lane':e.get('lane')})
        r.update({'hybridFanoutEvents':self.fanoutEvents[:500],'hybridFanoutOpportunities':self.fanoutOpportunities,'hybridFanoutCancelRequests':self.fanoutCancelRequests,'hybridFanoutSubmits':self.fanoutSubmits,'hybridFanoutFillQty':fillqty,'hybridFanoutFills':fills});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='hybrid_price_fanout_1946475_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'HYBRID_PRICE_FANOUT_1946475','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'HYBRID_PRICE_FANOUT_1946475_START','marketId':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(HybridPriceFanoutHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_hybrid(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(x or 0)<=EPS for x in ss.values());occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=base.slim(r);fillq=float(r.get('hybridFanoutFillQty') or 0.0)
        gates={'fanoutOpportunityExercised':int(r.get('hybridFanoutOpportunities') or 0)>0,'fanoutCancelRequested':int(r.get('hybridFanoutCancelRequests') or 0)>0,'fanoutReplacementSubmitted':int(r.get('hybridFanoutSubmits') or 0)>0,'fanoutPhysicalFill':fillq>EPS,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occok,'noNewRepairParent':m['repairParentBirths']==1,'pnlBetterThanFullPriority':m['pnlDiagnosticOnly']>FULL['pnlDiagnosticOnly']+EPS,'floorBetterThanFullPriority':m['floor']>FULL['floor']+EPS,'pnlNotWorseThanEconomicLease':m['pnlDiagnosticOnly']>=ECON['pnlDiagnosticOnly']-1e-7,'floorNotWorseThanEconomicLease':m['floor']>=ECON['floor']-1e-7}
        if not(safe and cons and bound and occok and gates['noNewRepairParent']):decision='HYBRID_PRICE_FANOUT_ACCOUNTING_FAIL'
        elif not(gates['fanoutOpportunityExercised'] and gates['fanoutReplacementSubmitted']):decision='HYBRID_PRICE_FANOUT_NOT_MATERIALIZED'
        elif not gates['fanoutPhysicalFill']:decision='HYBRID_PRICE_FANOUT_NO_PHYSICAL_FILL'
        elif gates['pnlBetterThanFullPriority'] and gates['floorBetterThanFullPriority'] and gates['pnlNotWorseThanEconomicLease'] and gates['floorNotWorseThanEconomicLease']:decision='HYBRID_PRICE_FANOUT_ONE_MARKET_STRONG_PASS'
        elif gates['pnlBetterThanFullPriority'] and gates['floorBetterThanFullPriority']:decision='HYBRID_PRICE_FANOUT_TRADEOFF_PASS_NO_PROMOTION'
        else:decision='HYBRID_PRICE_FANOUT_ECONOMIC_FAIL'
        out={'version':'ETH_MULTISLOT_HYBRID_PRICE_FANOUT_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'controls':{'economicLease':ECON,'fullSpreadPriority':FULL},'deltaVsEconomicLease':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-ECON['pnlDiagnosticOnly'],'floor':m['floor']-ECON['floor'],'activeRepairFillQty':m['activeRepairFillQty']-ECON['activeRepairFillQty']},'deltaVsFullPriority':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-FULL['pnlDiagnosticOnly'],'floor':m['floor']-FULL['floor'],'activeRepairFillQty':m['activeRepairFillQty']-FULL['activeRepairFillQty']},'gates':gates,'hybridFanoutOpportunities':r.get('hybridFanoutOpportunities'),'hybridFanoutCancelRequests':r.get('hybridFanoutCancelRequests'),'hybridFanoutSubmits':r.get('hybridFanoutSubmits'),'hybridFanoutFillQty':fillq,'hybridFanoutFills':r.get('hybridFanoutFills'),'hybridFanoutEvents':r.get('hybridFanoutEvents') or [],'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'reanchorCycles':r.get('reanchorCycles'),'slotCeilingFinal':r.get('slotCeilingFinal'),'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['single market 1946475','two physical slots maximum','one live economic-core sibling required before min-tranche priority fanout','priority target uses strict-past current spread geometry only','fanout qty is venue-min and cannot exceed replaced carrier remaining qty or parent available quota','joint all-live-sibling exact floor nonworse gate retained','cancel-pending/handoff lease/shared occupancy retained','full V16-admissible path unchanged','no fixed Target price ladder','no Target runtime oracle','no PnL threshold tuning','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'dEcon':out['deltaVsEconomicLease'],'dFull':out['deltaVsFullPriority'],'gates':gates,'fanoutFillQty':fillq,'fanoutFills':out['hybridFanoutFills'],'fanoutEvents':out['hybridFanoutEvents'][:18],'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
