from __future__ import annotations
import argparse,json,math,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))

import tools.run_eth_reanchor_handoff_lease_1946475 as lease
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.spread_aware_maker_priority import SpreadAwareMakerPriorityContext,SpreadAwareMakerPriorityPolicyV1

EPS=1e-9; MID=1946475; pe=base.pe
FROZEN={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'repairParentBirths':1,'repairParentCompletions':0,'passiveRepairFills':1,'activeRepairFillQty':1.639344262295082}
SPREAD={'pnlDiagnosticOnly':-0.9282608695652179,'floor':-0.9282608695652179,'rollingFillQty':2.1739130434782608,'activeRepairFillQty':0.0}

class EconomicHandoffLeaseLockHFT(lease.ReanchorHandoffLeaseHFT):
    def __init__(self,*a,**kw):
        self.priorityPolicy=SpreadAwareMakerPriorityPolicyV1();self.economicLeaseEvents=[];self.economicCancelAllows=0;self.economicCancelBlocks=0;self.leasedReplacementSubmits=0
        super().__init__(*a,**kw)

    def _priority_target(self,side):
        qv=v1.quotes(self.book)
        if not qv or side not in qv:return None,None,None,None
        bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);d=self.priorityPolicy.evaluate(SpreadAwareMakerPriorityContext(bid,ask,0.01,None))
        return float(d.price),bid,ask,d

    def _econ(self,t,side,qty,target):
        row={'t':int(t),'side':str(side),'targetPrice':float(target),'qty':float(qty)}
        if qty<=EPS:
            row.update({'allow':False,'reason':'NO_REAL_REPLACEMENT_QTY'});return row
        try:
            x,v=self._feature(int(t),str(side),float(qty),float(target));phase='baseAcquisitionRepair' if float(v['net_pair_reserve_ratio'])<=0 else 'reserveRepair';env=float(self.priceEnvelope.q80(phase,v));pair=float(v['candidate_pair_sum']);price_ok=pair<=env+EPS
            row.update({'phase':phase,'candidatePairSum':pair,'q80Envelope':env,'priceEnvelopeAllow':price_ok,'pairEdge':float(v['pair_edge']),'matchFraction':float(v['match_fraction']),'netPairReserveRatio':float(v['net_pair_reserve_ratio'])})
            recovery_ok=True
            if phase=='reserveRepair' and not(float(v['match_fraction'])>0 and float(v['pair_edge'])>=-EPS):
                sc,th=self.economic.score('expensive_repair_recovers_30s',x);recovery_ok=float(sc)>=float(th);row.update({'expensiveRecoveryScore':float(sc),'expensiveRecoveryThreshold':float(th),'expensiveRecoveryAllow':recovery_ok})
            else:row['expensiveRecoveryAllow']=True
            row['allow']=bool(price_ok and recovery_ok);row['reason']='V16_REPAIR_ECONOMIC_ALLOW' if row['allow'] else ('V16_PRICE_ENVELOPE_BLOCK' if not price_ok else 'V16_EXPENSIVE_RECOVERY_BLOCK')
        except Exception as e:
            row.update({'allow':False,'reason':'V16_ECONOMIC_EVAL_ERROR','error':repr(e)})
        return row

    def _request_cancel(self,t,row,reason):
        # Capacity contraction is structural and must not be suppressed by an economic reprice gate.
        if str(reason)!='FRONTIER_REANCHOR':return super()._request_cancel(int(t),row,reason)
        side=str(row.get('side') or '').upper();target,bid,ask,pdec=self._priority_target(side)
        if target is None:return False
        pid=int(row['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt))
        # The canceled carrier's unresolved reservation remains occupied until terminal, but this is the qty
        # that can be re-used by the same handoff after terminal confirmation.
        hyp_avail=avail+float(row.get('remaining') or 0.0);qty=min(float(row.get('remaining') or 0.0),hyp_avail)
        ev=self._econ(int(t),side,qty,float(target));ev.update({'event':'ECONOMIC_HANDOFF_CANCEL_PREFLIGHT','parentId':pid,'oldKey':str(row['key']),'oldPrice':float(row['price']),'bestBid':bid,'bestAsk':ask,'priorityReason':getattr(pdec,'reason',None),'triggerReason':reason})
        if not bool(ev.get('allow')):
            self.economicCancelBlocks+=1;ev['decision']='KEEP_QUEUE_ECONOMIC_REANCHOR_BLOCK';self.economicLeaseEvents.append(ev);return False
        ok=super()._request_cancel(int(t),row,reason)
        ev['cancelRequested']=bool(ok)
        if ok and isinstance(getattr(self,'pendingRollingCancel',None),dict):
            self.economicCancelAllows+=1
            self.pendingRollingCancel.update({'economicLeaseApprovedPrice':float(target),'economicLeaseApprovedQty':float(qty),'economicLeaseApprovedAt':int(t),'economicLeaseSnapshot':{k:ev.get(k) for k in ['phase','candidatePairSum','q80Envelope','pairEdge','matchFraction','netPairReserveRatio']}})
            ev['decision']='CANCEL_WITH_ECONOMIC_REPLACEMENT_LEASE'
        else:ev['decision']='CANCEL_REQUEST_FAILED'
        self.economicLeaseEvents.append(ev);return bool(ok)

    def _submit_replacement(self,t,meta,rows,best):
        approved=meta.get('economicLeaseApprovedPrice') if isinstance(meta,dict) else None
        if approved is None:return super()._submit_replacement(int(t),meta,rows,best)
        side=str(meta.get('side') or '').upper();qv=v1.quotes(self.book)
        if not qv or side not in qv:return False
        bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);maker_max=math.floor(((ask-0.01)+1e-12)*100.0)/100.0
        target=min(float(approved),float(maker_max))
        if target<=EPS or target>=ask-EPS:return False
        olde=getattr(self,'carrierLedger',{}).get(str(meta['key']),{});pid=int(meta['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));oldrem=max(0.0,float(olde.get('submittedQty') or 0.0)-float(olde.get('actualFilled') or 0.0));qty=min(oldrem,avail)
        audit=self._econ(int(t),side,qty,target);ev={'t':int(t),'event':'ECONOMIC_HANDOFF_LEASE_EXECUTION','parentId':pid,'oldKey':str(meta['key']),'approvedAt':meta.get('economicLeaseApprovedAt'),'approvedPrice':float(approved),'executionPrice':float(target),'currentBestBid':bid,'currentBestAsk':ask,'currentV16AuditOnly':audit,'neverAboveApproved':float(target)<=float(approved)+EPS}
        ok=super()._submit_replacement(int(t),meta,rows,float(target));ev['submit']=bool(ok)
        if ok:self.leasedReplacementSubmits+=1;ev['decision']='LEASED_ECONOMIC_REPLACEMENT_SUBMITTED'
        else:ev['decision']='LEASED_ECONOMIC_REPLACEMENT_NOT_MATERIALIZED'
        self.economicLeaseEvents.append(ev);return bool(ok)

    def run_locked(self,models,winner):
        r=self.run_lease(models,winner);r.update({'economicHandoffLeaseEvents':self.economicLeaseEvents[:500],'economicCancelAllows':self.economicCancelAllows,'economicCancelBlocks':self.economicCancelBlocks,'leasedReplacementSubmits':self.leasedReplacementSubmits});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='economic_handoff_lock_1946475_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'ECONOMIC_HANDOFF_LEASE_LOCK','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ECONOMIC_HANDOFF_LEASE_LOCK_START','marketId':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(EconomicHandoffLeaseLockHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_locked(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(x or 0)<=EPS for x in ss.values());occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=base.slim(r);events=r.get('economicHandoffLeaseEvents') or []
        lease_exec=[x for x in events if x.get('event')=='ECONOMIC_HANDOFF_LEASE_EXECUTION'];never_above=all(bool(x.get('neverAboveApproved')) for x in lease_exec)
        gates={'economicCancelAdmissionExercised':int(r.get('economicCancelAllows') or 0)>0,'economicKeepQueueBlockExercised':int(r.get('economicCancelBlocks') or 0)>0,'leasedReplacementSubmitted':int(r.get('leasedReplacementSubmits') or 0)>0,'neverSubmitAboveLeasedEconomicPrice':never_above,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occok,'noNewRepairParent':m['repairParentBirths']==1,'pnlNotWorseThanFrozen':m['pnlDiagnosticOnly']>=FROZEN['pnlDiagnosticOnly']-1e-7,'floorNotWorseThanFrozen':m['floor']>=FROZEN['floor']-1e-7}
        if not(safe and cons and bound and occok and gates['noNewRepairParent'] and never_above):decision='ECONOMIC_HANDOFF_LEASE_ACCOUNTING_FAIL'
        elif not gates['economicCancelAdmissionExercised'] or not gates['leasedReplacementSubmitted']:decision='ECONOMIC_HANDOFF_LEASE_NOT_MATERIALIZED'
        elif gates['pnlNotWorseThanFrozen'] and gates['floorNotWorseThanFrozen']:decision='ECONOMIC_HANDOFF_LEASE_ONE_MARKET_PASS'
        else:decision='ECONOMIC_HANDOFF_LEASE_FUNCTIONAL_ECONOMICS_INCOMPLETE'
        out={'version':'ETH_MULTISLOT_ECONOMIC_HANDOFF_LEASE_LOCK_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'deltaVsFrozen':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-FROZEN['pnlDiagnosticOnly'],'floor':m['floor']-FROZEN['floor'],'activeRepairFillQty':m['activeRepairFillQty']-FROZEN['activeRepairFillQty']},'deltaVsSpread':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-SPREAD['pnlDiagnosticOnly'],'floor':m['floor']-SPREAD['floor'],'activeRepairFillQty':m['activeRepairFillQty']-SPREAD['activeRepairFillQty']},'gates':gates,'economicCancelAllows':r.get('economicCancelAllows'),'economicCancelBlocks':r.get('economicCancelBlocks'),'leasedReplacementSubmits':r.get('leasedReplacementSubmits'),'economicHandoffLeaseEvents':events[:500],'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'handoffLeaseActiveBlocks':r.get('handoffLeaseActiveBlocks'),'events':r.get('repeatedRollingEvents',[])[:500],'partitionFills':r.get('partitionFills') or [],'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['single market 1946475','V16 strict-past dynamic economics only','approved replacement price leased across cancel terminal','later inadmissible reanchor preserves current live queue','capacity contraction remains structural','Active blocked only while handoff is pending and uses shared parent occupancy afterward','no fixed cross-market ceiling','no +0.05 runtime transfer','realistic HFT','no dream fill','no 3-5 slots','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'deltaVsFrozen':out['deltaVsFrozen'],'deltaVsSpread':out['deltaVsSpread'],'gates':gates,'economicCancelAllows':r.get('economicCancelAllows'),'economicCancelBlocks':r.get('economicCancelBlocks'),'leasedReplacementSubmits':r.get('leasedReplacementSubmits'),'economicEvents':events[:18],'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
