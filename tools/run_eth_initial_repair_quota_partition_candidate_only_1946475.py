from __future__ import annotations
import argparse,json,os,shutil,sys,tempfile,time,zipfile,math
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))

import tools.run_eth_quota_preserving_multi_carrier_repair_1946475 as old
from tools.eth_repair_modular.initial_repair_quota_partition import InitialRepairQuotaPartitionContext,InitialRepairQuotaPartitionPolicyV1

pg=old.pg; pe=old.pe; EPS=1e-9; MID=1946475
BASELINE={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'repairParentBirths':1,'repairParentCompletions':0,'passiveRepairFills':1,'activeRepairFillQty':1.639344262295082}

class InitialRepairQuotaPartitionHFT(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        self.partitionPolicy=InitialRepairQuotaPartitionPolicyV1();self.partitionEvents=[];self.partitionParents=set();self.partitionKeys=[];self.partitionAttempts=0;self.partitionApplied=0
        super().__init__(*a,**kw)
    def _visible_prices(self,side):
        b=getattr(self,'book',{})
        if side=='UP': return [float(p) for p in sorted((b.get('bids') or {}).keys(),reverse=True)[:12]]
        if side=='DOWN': return [1.0-float(p) for p in sorted((b.get('asks') or {}).keys())[:12]]
        return []
    def _floor_after_buys(self,side,plans):
        pay=self._current_payoffs();up=float(pay.get('up') or 0.0);down=float(pay.get('down') or 0.0)
        floors=[]
        for p,q in plans:
            p=float(p);q=float(q)
            if side=='UP': up+=(1-p)*q;down-=p*q
            elif side=='DOWN': down+=(1-p)*q;up-=p*q
            else:return None,[]
            floors.append(min(up,down))
        return min(up,down),floors
    def submit(self,t,side,p,q):
        role=str(getattr(self,'_pendingAuthorizedRole',None) or '').upper();pid=getattr(self,'_pendingParentId',None);lane=str(getattr(self,'_pendingLane',None) or '')
        if role!='REPAIR' or pid is None or 'ACTIVE' in lane.upper() or int(pid) in self.partitionParents:
            return super().submit(t,side,p,q)
        debt=float(self._parent_debt_now(int(pid)))
        if debt<=EPS or float(q)<debt-1e-7:
            return super().submit(t,side,p,q)
        self.partitionAttempts+=1
        oid=getattr(self,'_pendingAuthorizedObjectiveId',None);orig_lane=getattr(self,'_pendingLane',None)
        visible=self._visible_prices(str(side))
        dec=self.partitionPolicy.evaluate(InitialRepairQuotaPartitionContext(int(pid),debt,float(p),float(q),visible,None,2,12.0))
        row={'t':int(t),'event':'INITIAL_REPAIR_QUOTA_PARTITION_EVALUATION','parentId':int(pid),'side':str(side),'debt':debt,'originalPrice':float(p),'originalQty':float(q),'visiblePrices':visible[:12],'policyReason':dec.reason,'planned':[x.__dict__ for x in dec.plans]}
        if not dec.partition or len(dec.plans)<2:
            row['decision']='FALLBACK_ORIGINAL_NO_TWO_CHILD_PLAN';self.partitionEvents.append(row);return super().submit(t,side,p,q)
        before=float(self._current_payoffs().get('floor') or 0.0);after,floors=self._floor_after_buys(str(side),[(x.price,x.qty) for x in dec.plans]);row.update({'floorBefore':before,'floorAfterAllChildren':after,'incrementalFloors':floors})
        if after is None or after<before-1e-7 or any(float(x)<before-1e-7 for x in floors):
            row['decision']='FALLBACK_ORIGINAL_PARTITION_FLOOR_DAMAGE';self.partitionEvents.append(row);return super().submit(t,side,p,q)
        # First child retains the original responsibility lineage expected by the caller.
        submitted=[]
        n0=int(getattr(self,'n',0))
        for i,plan in enumerate(dec.plans):
            self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=int(pid);self._pendingLane=(orig_lane if i==0 else 'INITIAL_REPAIR_QUOTA_PARTITION_SIBLING')
            prev_parallel=bool(getattr(self,'_allowSameSideParallelReservation',False))
            if i>0:self._allowSameSideParallelReservation=True
            try:ok=super().submit(int(t),str(side),float(plan.price),float(plan.qty))
            finally:self._allowSameSideParallelReservation=prev_parallel
            key=f'{side}_{n0+i}' if ok else None
            submitted.append({'ordinal':i+1,'ok':bool(ok),'key':key,'price':float(plan.price),'qty':float(plan.qty)})
            if not ok: break
        self.partitionParents.add(int(pid));self.partitionKeys.extend([x['key'] for x in submitted if x['key']]);
        if len(submitted)>=2 and all(x['ok'] for x in submitted[:2]): self.partitionApplied+=1
        row.update({'event':'INITIAL_REPAIR_QUOTA_PARTITION_SUBMIT','decision':'PARTITION_SUBMITTED' if self.partitionApplied else 'PARTIAL_PARTITION_SUBMIT','submitted':submitted,'aggregateReservedPlanned':dec.total_reserved_qty,'residualUnreservedDebt':dec.residual_unreserved_debt})
        self.partitionEvents.append(row)
        return bool(submitted and submitted[0]['ok'])
    def run_partition(self,models,winner):
        r=self.run_guard(models,winner)
        self._refresh_carrier_ledger(int(self.capEnd))
        fills=[]
        for key in self.partitionKeys:
            e=getattr(self,'carrierLedger',{}).get(str(key),{});fills.append({'key':key,'submittedQty':float(e.get('submittedQty') or 0.0),'actualFilled':float(e.get('actualFilled') or 0.0),'terminalConfirmed':bool(e.get('terminalConfirmed')),'parentId':e.get('parentId'),'role':e.get('objectiveRole')})
        r.update({'initialRepairQuotaPartitionPolicy':self.partitionPolicy.name,'partitionAttempts':self.partitionAttempts,'partitionApplied':self.partitionApplied,'partitionKeys':list(self.partitionKeys),'partitionFills':fills,'partitionEvents':self.partitionEvents[:200]})
        return r

def slim(r):
    return {'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'passiveRepairFills':(int(r.get('v38PassiveRepairFillEvents') or 0) if isinstance(r.get('v38PassiveRepairFillEvents'),(int,float)) else len(r.get('v38PassiveRepairFillEvents') or [])),'activeRepairFillQty':float(r.get('parallelRepairActiveFillQty') or r.get('v36ActiveFillQty') or 0.0)}

def dump(path,state,extra=None):
    row={'version':'INITIAL_REPAIR_QUOTA_PARTITION_CANDIDATE_ONLY_1946475_V1','state':state,'ts':time.time(),'baselineFrozen':BASELINE}
    if extra:row.update(extra)
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(row,indent=2),encoding='utf-8')

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',default='AUTO');a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));partial=outdir/'partial.json';dump(partial,'STARTED')
    tmp=Path(tempfile.mkdtemp(prefix='initial_partition_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];dump(partial,'BUNDLE_READY')
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];dump(partial,'RUNTIME_READY')
        tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(InitialRepairQuotaPartitionHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_partition(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=slim(r);physical=sum(float(x.get('actualFilled') or 0) for x in r.get('partitionFills',[]))
        gates={'partitionExercised':int(r.get('partitionAttempts') or 0)>0,'twoChildrenSubmitted':int(r.get('partitionApplied') or 0)>0 and len(r.get('partitionKeys') or [])>=2,'partitionPhysicalFill':physical>EPS,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ_bound,'noNewRepairParent':m['repairParentBirths']==BASELINE['repairParentBirths']}
        if not safe or not cons or not bound or not occ_bound or not gates['noNewRepairParent']:decision='PARTITION_ACCOUNTING_FAIL'
        elif not gates['twoChildrenSubmitted']:decision='PARTITION_NOT_EXERCISED'
        elif not gates['partitionPhysicalFill']:decision='PARTITION_SUBMIT_NO_FILL'
        else:decision='PARTITION_EXECUTION_PASS'
        out={'version':'INITIAL_REPAIR_QUOTA_PARTITION_CANDIDATE_ONLY_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'baselineFrozen':BASELINE,'candidate':m,'delta':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-BASELINE['pnlDiagnosticOnly'],'floor':m['floor']-BASELINE['floor'],'fills':m['fills']-BASELINE['fills'],'rounds':m['rounds']-BASELINE['rounds']},'gates':gates,'partitionFills':r.get('partitionFills',[]),'partitionEvents':r.get('partitionEvents',[])[:200],'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['one market 1946475','first passive Repair submit only','same parent/objective/role','exactly two children max','strict-past visible price options; no fixed tick spacing','each child venue-min quantity; aggregate planned reservation <= authoritative debt','all-child projected exact floor cannot go below pre-partition floor','remaining parent debt stays explicit','no Target runtime input','realistic HFT only','no dream fill','no 8781']}
        op=outdir/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');dump(partial,'DONE',{'decision':decision,'candidate':m,'gates':gates});print(json.dumps({'ok':True,'decision':decision,'candidate':m,'delta':out['delta'],'gates':gates,'partitionFills':out['partitionFills'],'partitionEvents':out['partitionEvents'][:8],'safety':ss},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
