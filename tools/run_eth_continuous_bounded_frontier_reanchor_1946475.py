from __future__ import annotations
import argparse,json,math,os,shutil,sys,tempfile,time,zipfile
from itertools import combinations
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))

import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
import tools.run_eth_v83_same_parent_parallel_repair_hft_smoke_1916869 as v83sp
from tools.eth_repair_modular.frontier_bounded_passive_repair_placement import (
    FrontierBoundedPassiveRepairContext,FrontierBoundedPassiveRepairPlacementPolicyV1,
)

EPS=1e-9; MID=1946475; pe=base.pe
CONTROL={
    'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,
    'repairParentBirths':1,'repairParentCompletions':0,'passiveRepairFills':1,'activeRepairFillQty':1.639344262295082,
    'partitionPhysicalFillQty':0.0,
}

class ContinuousBoundedFrontierReanchorHFT(base.InitialRepairQuotaPartitionHFT):
    def __init__(self,*a,**kw):
        self.frontierPolicy=FrontierBoundedPassiveRepairPlacementPolicyV1()
        self.continuousEvents=[];self.pendingReanchor=None;self.reanchorCancelRequests=0;self.reanchorSubmits=0
        self.trackedFillSeen={};self.partitionPhysicalFillQty=0.0;self._now=0
        super().__init__(*a,**kw)

    def _live_partition_rows(self):
        self._refresh_carrier_ledger(int(self._now))
        out=[]
        for key in list(dict.fromkeys(self.partitionKeys)):
            e=getattr(self,'carrierLedger',{}).get(str(key),{});o=getattr(self,'orders',{}).get(str(key),{})
            if bool(e.get('terminalConfirmed')): continue
            sub=float(e.get('submittedQty') or o.get('qty') or 0.0);fill=float(e.get('actualFilled') or 0.0)
            rem=max(0.0,sub-fill)
            if rem<=EPS: continue
            out.append({'key':str(key),'entry':e,'side':str(e.get('side') or o.get('side') or '').upper(),
                        'price':float(o.get('price') or e.get('price') or 0.0),'remaining':rem,
                        'parentId':e.get('parentId'),'objectiveId':e.get('objectiveId')})
        return out

    def _scan_partition_fills(self,t):
        self._refresh_carrier_ledger(int(t))
        for key in list(dict.fromkeys(self.partitionKeys)):
            e=getattr(self,'carrierLedger',{}).get(str(key),{})
            now=float(e.get('actualFilled') or 0.0);prev=float(self.trackedFillSeen.get(str(key),0.0))
            if now>prev+EPS:
                inc=now-prev;self.partitionPhysicalFillQty+=inc
                self.continuousEvents.append({'t':int(t),'event':'CONTINUOUS_FRONTIER_PHYSICAL_FILL','key':str(key),
                                              'incQty':inc,'cumQty':now,'parentId':e.get('parentId')})
            self.trackedFillSeen[str(key)]=max(prev,now)

    def _quotes(self,side):
        qv=v1.quotes(self.book)
        if not qv or side not in qv:return None,None
        return qv[side].get('bid'),qv[side].get('ask')

    def _candidate_target(self,side,proposed,qty,pid):
        bid,ask=self._quotes(side)
        if bid is None or ask is None:return None,{'reason':'NO_QUOTES','bid':bid,'ask':ask}
        raw=min(float(bid),float(v83sp.ECON_CEILING));target=math.floor((raw+1e-12)/.01)*.01
        projected=self._floor_after_buys(side,[(target,float(qty))])[0] if target>EPS else None
        d=self.frontierPolicy.evaluate(FrontierBoundedPassiveRepairContext(
            objective_role='REPAIR',parent_id=int(pid),side=side,proposed_price=float(proposed),proposed_qty=float(qty),
            live_best_bid=float(bid),live_best_ask=float(ask),inherited_economic_ceiling=float(v83sp.ECON_CEILING),
            floor_before=float(self._current_payoffs().get('floor') or 0.0),projected_floor_at_target=projected,tick_size=.01))
        meta={'bid':float(bid),'ask':float(ask),'economicCeiling':float(v83sp.ECON_CEILING),'policyReason':d.reason,
              'policyChange':bool(d.change_price),'target':float(d.target_price),'projectedFloorSingle':d.projected_floor}
        return (float(d.target_price) if d.change_price else None),meta

    def _all_subset_floor_safe(self,side,plans,before):
        vals=[]
        for r in range(1,len(plans)+1):
            for idxs in combinations(range(len(plans)),r):
                sub=[plans[i] for i in idxs];f,_=self._floor_after_buys(side,sub);vals.append({'idx':list(idxs),'floor':f})
                if f is None or float(f)<float(before)-1e-7:return False,vals
        return True,vals

    def _maybe_reanchor(self,t):
        self._now=int(t);self._scan_partition_fills(int(t));rows=self._live_partition_rows()
        # If a cancel is pending, never release or replace until terminal confirmation.
        if self.pendingReanchor is not None:
            p=self.pendingReanchor;olde=getattr(self,'carrierLedger',{}).get(str(p['key']),{})
            if not bool(olde.get('terminalConfirmed')):
                return
            side=str(p['side']);pid=int(p['parentId']);oid=p['objectiveId']
            debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();available=float(self.parentExecutionOccupancy.available(pid,debt))
            qty=min(float(p['oldRemainingQty']),available)
            target,meta=self._candidate_target(side,float(p['oldPrice']),qty,pid)
            ev={'t':int(t),'event':'CONTINUOUS_FRONTIER_REPLACEMENT_EVALUATION','oldKey':p['key'],'side':side,'parentId':pid,
                'objectiveId':oid,'oldPrice':p['oldPrice'],'oldRemainingQty':p['oldRemainingQty'],'authoritativeDebt':debt,
                'availableQty':available,'candidateQty':qty,**meta}
            if target is None:
                ev['decision']='WAIT_NO_BOUNDED_FRONTIER_IMPROVEMENT';self.continuousEvents.append(ev);return
            legal=1.0/float(target) if target>EPS else 1e99;ev['legalQty']=legal
            if qty<=EPS or qty+EPS<legal:
                ev['decision']='WAIT_NO_LEGAL_REPLACEMENT_QTY';self.continuousEvents.append(ev);return
            others=[r for r in rows if str(r['key'])!=str(p['key'])]
            if any(abs(float(r['price'])-float(target))<=1e-10 for r in others):
                ev['decision']='WAIT_DUPLICATE_LIVE_PRICE';self.continuousEvents.append(ev);return
            before=float(self._current_payoffs().get('floor') or 0.0);plans=[(float(r['price']),float(r['remaining'])) for r in others]+[(float(target),float(qty))]
            safe,subset=self._all_subset_floor_safe(side,plans,before);ev.update({'floorBefore':before,'allLivePlans':plans,'subsetFloors':subset,'jointFloorSafe':safe})
            if not safe:
                ev['decision']='WAIT_JOINT_WORST_CASE_FLOOR_DAMAGE';self.continuousEvents.append(ev);return
            n0=int(self.n);self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='CONTINUOUS_BOUNDED_FRONTIER_REANCHOR'
            prev=bool(getattr(self,'_allowSameSideParallelReservation',False));self._allowSameSideParallelReservation=True
            try:ok=super().submit(int(t),side,float(target),float(qty))
            finally:self._allowSameSideParallelReservation=prev
            ev['submit']=bool(ok)
            if not ok:
                ev['decision']='SUBMIT_FAILED';self.continuousEvents.append(ev);return
            key=f'{side}_{n0}';self.partitionKeys.append(key);self.reanchorSubmits+=1;self.pendingReanchor=None
            ev.update({'decision':'CONTINUOUS_BOUNDED_REPLACEMENT_SUBMITTED','replacementKey':key});self.continuousEvents.append(ev);return

        if not rows:return
        side=rows[0]['side'];pid=rows[0]['parentId']
        if not side or pid is None or any(r['side']!=side or int(r['parentId'])!=int(pid) for r in rows):return
        deepest=min(rows,key=lambda r:r['price']);target,meta=self._candidate_target(side,float(deepest['price']),float(deepest['remaining']),int(pid))
        if target is None:return
        # Reanchor only when the entire currently live option set is behind the bounded frontier.
        if max(float(r['price']) for r in rows)>=float(target)-EPS:return
        if self._cancel_key(int(t),deepest['key']):
            self.reanchorCancelRequests+=1
            self.pendingReanchor={'key':deepest['key'],'side':side,'parentId':int(pid),'objectiveId':deepest['objectiveId'],
                                  'oldPrice':float(deepest['price']),'oldRemainingQty':float(deepest['remaining'])}
            self.continuousEvents.append({'t':int(t),'event':'CONTINUOUS_FRONTIER_CANCEL_REQUEST','key':deepest['key'],'side':side,
                                          'parentId':int(pid),'oldPrice':float(deepest['price']),'oldRemainingQty':float(deepest['remaining']),
                                          'livePrices':[float(r['price']) for r in rows],**meta,
                                          'reason':'ALL_LIVE_PARTITION_CARRIERS_BEHIND_BOUNDED_FRONTIER'})

    def process(self,t):
        out=super().process(t);self._maybe_reanchor(int(t));return out

    def run_continuous(self,models,winner):
        r=self.run_partition(models,winner);self._now=int(self.capEnd);self._scan_partition_fills(int(self.capEnd))
        r.update({'continuousFrontierPolicy':self.frontierPolicy.name,'reanchorCancelRequests':self.reanchorCancelRequests,
                  'reanchorSubmits':self.reanchorSubmits,'partitionPhysicalFillQty':self.partitionPhysicalFillQty,
                  'continuousFrontierEvents':self.continuousEvents[:600]})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='continuous_frontier_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(ContinuousBoundedFrontierReanchorHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_continuous(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=base.slim(r)
        physical=float(r.get('partitionPhysicalFillQty') or 0.0)
        gates={'twoInitialChildrenSubmitted':int(r.get('partitionApplied') or 0)>0,'reanchorCancelRequested':int(r.get('reanchorCancelRequests') or 0)>0,
               'boundedReplacementSubmitted':int(r.get('reanchorSubmits') or 0)>0,'partitionOrReplacementPhysicalFill':physical>EPS,
               'lifecycleProgressBeyondControl':physical>EPS or m['rounds']>CONTROL['rounds'] or m['repairParentCompletions']>CONTROL['repairParentCompletions'],
               'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ_bound,
               'noNewRepairParent':m['repairParentBirths']==CONTROL['repairParentBirths']}
        if not safe or not cons or not bound or not occ_bound or not gates['noNewRepairParent']:decision='CONTINUOUS_FRONTIER_ACCOUNTING_FAIL'
        elif not gates['reanchorCancelRequested'] or not gates['boundedReplacementSubmitted']:decision='CONTINUOUS_FRONTIER_NO_REACHABILITY'
        elif not gates['partitionOrReplacementPhysicalFill']:decision='CONTINUOUS_FRONTIER_REANCHOR_NO_FILL'
        elif gates['lifecycleProgressBeyondControl']:decision='CONTINUOUS_FRONTIER_PHYSICAL_LIVENESS_PASS'
        else:decision='CONTINUOUS_FRONTIER_PHYSICAL_FILL_NEEDS_LIFECYCLE_PROGRESS'
        out={'version':'ETH_CONTINUOUS_BOUNDED_FRONTIER_REANCHOR_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,
             'winnerPostHocOnly':cr['winner'],'decision':decision,'controlFrozen':CONTROL,'candidate':m,
             'delta':{'fills':m['fills']-CONTROL['fills'],'rounds':m['rounds']-CONTROL['rounds'],'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-CONTROL['pnlDiagnosticOnly'],
                      'floor':m['floor']-CONTROL['floor'],'passiveRepairFills':m['passiveRepairFills']-CONTROL['passiveRepairFills'],'activeRepairFillQty':m['activeRepairFillQty']-CONTROL['activeRepairFillQty']},
             'gates':gates,'partitionPhysicalFillQty':physical,'reanchorCancelRequests':r.get('reanchorCancelRequests'),'reanchorSubmits':r.get('reanchorSubmits'),
             'partitionFills':r.get('partitionFills',[]),'continuousFrontierEvents':r.get('continuousFrontierEvents',[])[:600],
             'safety':ss,'allocationParents':parents,'occupancyParents':occ,
             'boundary':['one market 1946475','same first Repair parent/objective/role','initial max two siblings','reanchor changes only an existing partition/replacement passive carrier',
                         'cancel-pending reservation retained until terminal confirmation','bounded target <= min(strict-past live best bid,inherited economic ceiling)','all subsets of currently-live sibling fills must keep exact floor nonworse at replacement admission',
                         'replacement uses only real parent available quota','Active policy frozen','no Target runtime input','realistic HFT only','no dream fill','no 3-5 slot promotion','no 8781']}
        op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'candidate':m,'delta':out['delta'],'gates':gates,'partitionPhysicalFillQty':physical,
                          'reanchorCancelRequests':r.get('reanchorCancelRequests'),'reanchorSubmits':r.get('reanchorSubmits'),
                          'events':out['continuousFrontierEvents'][:30],'safety':ss},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
