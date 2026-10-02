from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_capacity_contraction_repeated_rolling_1946475 as prev
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
EPS=1e-9;MID=1946475;pe=base.pe
COUNTED_NO_LEASE={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.6482758620689655,'floor':-0.6482758620689655,'activeRepairFillQty':1.7241379310344829}
FROZEN_PARTITION={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'activeRepairFillQty':1.639344262295082}

class ReanchorHandoffLeaseHFT(prev.CapacityContractionRepeatedRollingHFT):
    def __init__(self,*a,**kw):
        self.handoffLeaseActiveBlocks=0;self.handoffLeaseEvents=[]
        super().__init__(*a,**kw)
    def _maybe_hard_active(self,t):
        meta=getattr(self,'pendingRollingCancel',None)
        rp=getattr(self,'repairParent',None)
        if meta is not None and isinstance(rp,dict) and int(rp.get('id') or -1)==int(meta.get('parentId') or -2):
            self.handoffLeaseActiveBlocks+=1
            if self.handoffLeaseActiveBlocks<=200:
                self.handoffLeaseEvents.append({'t':int(t),'event':'REANCHOR_HANDOFF_LEASE_BLOCK_ACTIVE','parentId':int(meta['parentId']),'oldKey':str(meta['key']),'reason':str(meta.get('reason')),'leaseUntil':'REPLACEMENT_SUBMITTED_OR_MANAGER_ABANDONS'})
            return False
        return super()._maybe_hard_active(t)
    def run_lease(self,models,winner):
        r=self.run_repeated(models,winner)
        r.update({'handoffLeaseActiveBlocks':self.handoffLeaseActiveBlocks,'handoffLeaseEvents':self.handoffLeaseEvents[:200]})
        return r

def slim(r):return base.slim(r)
def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='reanchor_handoff_lease_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=pe.make(ReanchorHandoffLeaseHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_lease(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=slim(r)
        gates={'handoffLeaseExercised':int(r.get('handoffLeaseActiveBlocks') or 0)>0,'repeatedRollingSubmitted':int(r.get('repeatedRollingSubmits') or 0)>0,'repeatedRollingPhysicalFill':float(r.get('repeatedRollingFillQty') or 0)>EPS,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ_bound,'noNewRepairParent':m['repairParentBirths']==1}
        if not safe or not cons or not bound or not occ_bound or not gates['noNewRepairParent']:decision='REANCHOR_HANDOFF_LEASE_ACCOUNTING_FAIL'
        elif not gates['handoffLeaseExercised']:decision='REANCHOR_HANDOFF_LEASE_NOT_EXERCISED'
        elif not gates['repeatedRollingSubmitted']:decision='LEASE_PASS_ROLLING_NOT_MATERIALIZED'
        elif not gates['repeatedRollingPhysicalFill']:decision='LEASE_PASS_ROLLING_SUBMIT_NO_FILL'
        else:decision='REANCHOR_HANDOFF_LEASE_PHYSICAL_LIVENESS_PASS'
        out={'version':'REANCHOR_HANDOFF_LEASE_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'deltaVsNoLease':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-COUNTED_NO_LEASE['pnlDiagnosticOnly'],'floor':m['floor']-COUNTED_NO_LEASE['floor'],'activeRepairFillQty':m['activeRepairFillQty']-COUNTED_NO_LEASE['activeRepairFillQty']},'deltaVsFrozenPartition':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-FROZEN_PARTITION['pnlDiagnosticOnly'],'floor':m['floor']-FROZEN_PARTITION['floor'],'activeRepairFillQty':m['activeRepairFillQty']-FROZEN_PARTITION['activeRepairFillQty']},'gates':gates,'slotCeilingFinal':r.get('slotCeilingFinal'),'contractionEvents':r.get('contractionEvents'),'reanchorCycles':r.get('reanchorCycles'),'repeatedRollingSubmits':r.get('repeatedRollingSubmits'),'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'handoffLeaseActiveBlocks':r.get('handoffLeaseActiveBlocks'),'handoffLeaseEvents':r.get('handoffLeaseEvents'),'events':r.get('repeatedRollingEvents',[])[:600],'partitionFills':r.get('partitionFills') or [],'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['single market 1946475','only new authority is same-parent Active block while a management reanchor cancel is between old carrier and replacement/abandon decision','lease does not create debt or physical quantity','cancel-pending and late-fill accounting unchanged','after replacement/abandon Active router is frozen/original','capacity contraction and repeated rolling logic frozen from prior candidate','no threshold/qty/price tuning','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'deltaVsNoLease':out['deltaVsNoLease'],'deltaVsFrozenPartition':out['deltaVsFrozenPartition'],'gates':gates,'slotCeilingFinal':r.get('slotCeilingFinal'),'reanchorCycles':r.get('reanchorCycles'),'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'handoffLeaseActiveBlocks':r.get('handoffLeaseActiveBlocks'),'leaseEvents':(r.get('handoffLeaseEvents') or [])[:12],'events':out['events'][:24],'partitionFills':out['partitionFills'],'safety':ss},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
