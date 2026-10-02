from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_reanchor_handoff_lease_1946475 as prev
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.spread_aware_maker_priority import SpreadAwareMakerPriorityContext,SpreadAwareMakerPriorityPolicyV1
EPS=1e-9;MID=1946475;pe=base.pe
LEASE_BASE={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'activeRepairFillQty':1.639344262295082}

class SpreadAwareRollingHandoffHFT(prev.ReanchorHandoffLeaseHFT):
    def __init__(self,*a,**kw):
        self.spreadPriority=SpreadAwareMakerPriorityPolicyV1();self.spreadPriorityEvents=[]
        super().__init__(*a,**kw)
    def _submit_replacement(self,t,meta,rows,best):
        qv=v1.quotes(self.book);side=str(meta.get('side') or '').upper();target=float(best);dec=None
        if qv and side in qv:
            bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);dec=self.spreadPriority.evaluate(SpreadAwareMakerPriorityContext(bid,ask,0.01,None));target=float(dec.price)
            self.spreadPriorityEvents.append({'t':int(t),'event':'SPREAD_AWARE_ROLLING_PRICE','parentId':int(meta.get('parentId')),'oldKey':str(meta.get('key')),'side':side,'bestBid':bid,'bestAsk':ask,'baseTarget':float(best),'chosenTarget':target,'improved':bool(dec.improved),'reason':dec.reason})
        return super()._submit_replacement(int(t),meta,rows,target)
    def run_spread(self,models,winner):
        r=self.run_lease(models,winner);r.update({'spreadPriorityPolicy':self.spreadPriority.name,'spreadPriorityEvents':self.spreadPriorityEvents[:300],'spreadPriorityImprovements':sum(bool(x.get('improved')) for x in self.spreadPriorityEvents)})
        return r

def slim(r):return base.slim(r)
def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='spread_aware_rolling_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=pe.make(SpreadAwareRollingHandoffHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_spread(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=slim(r)
        gates={'handoffLeaseExercised':int(r.get('handoffLeaseActiveBlocks') or 0)>0,'spreadPriorityExercised':len(r.get('spreadPriorityEvents') or [])>0,'insideSpreadImprovementExercised':int(r.get('spreadPriorityImprovements') or 0)>0,'rollingPhysicalFill':float(r.get('repeatedRollingFillQty') or 0)>EPS,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ_bound,'noNewRepairParent':m['repairParentBirths']==1}
        if not safe or not cons or not bound or not occ_bound or not gates['noNewRepairParent']:decision='SPREAD_AWARE_ROLLING_ACCOUNTING_FAIL'
        elif not gates['insideSpreadImprovementExercised']:decision='SPREAD_AWARE_PRIORITY_NOT_EXERCISED'
        elif not gates['rollingPhysicalFill']:decision='SPREAD_AWARE_ROLLING_NO_PHYSICAL_FILL'
        else:decision='SPREAD_AWARE_ROLLING_PHYSICAL_LIVENESS_PASS'
        out={'version':'SPREAD_AWARE_ROLLING_HANDOFF_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'deltaVsLeaseBase':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-LEASE_BASE['pnlDiagnosticOnly'],'floor':m['floor']-LEASE_BASE['floor'],'activeRepairFillQty':m['activeRepairFillQty']-LEASE_BASE['activeRepairFillQty']},'gates':gates,'spreadPriorityEvents':r.get('spreadPriorityEvents'),'spreadPriorityImprovements':r.get('spreadPriorityImprovements'),'slotCeilingFinal':r.get('slotCeilingFinal'),'reanchorCycles':r.get('reanchorCycles'),'repeatedRollingSubmits':r.get('repeatedRollingSubmits'),'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'handoffLeaseActiveBlocks':r.get('handoffLeaseActiveBlocks'),'events':r.get('repeatedRollingEvents',[])[:600],'partitionFills':r.get('partitionFills') or [],'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['single market 1946475','only price change vs handoff-lease baseline: rolling replacement may improve one venue tick inside spread when at least two maker ticks exist','never crosses ask','joint all-live-sibling floor nonworse gate remains final economic authority','handoff lease/cancel-pending/shared occupancy frozen','Active router frozen','no Target runtime input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'deltaVsLeaseBase':out['deltaVsLeaseBase'],'gates':gates,'spreadPriorityEvents':(r.get('spreadPriorityEvents') or [])[:16],'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'events':out['events'][:28],'partitionFills':out['partitionFills'],'safety':ss},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
