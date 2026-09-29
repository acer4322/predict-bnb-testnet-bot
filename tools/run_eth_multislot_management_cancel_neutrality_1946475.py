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
FROZEN_PARTITION={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'activeRepairFillQty':1.639344262295082}
COUNTED_CANCEL={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.6482758620689655,'floor':-0.6482758620689655,'activeRepairFillQty':1.7241379310344829}

class ManagementCancelNeutralHFT(prev.CapacityContractionRepeatedRollingHFT):
    def __init__(self,*a,**kw):
        self.managementCancelKeys=[];self.managementCancelEvents=[]
        super().__init__(*a,**kw)
    def _cancel_without_failure_churn(self,t,key):
        mro=type(self).mro();v34=next((c for c in mro if c.__name__=='V34ReadinessShadow'),None)
        if v34 is None:raise RuntimeError('V34ReadinessShadow not found in MRO')
        ok=super(v34,self)._cancel_key(int(t),str(key))
        if ok:
            self.managementCancelKeys.append(str(key));self.managementCancelEvents.append({'t':int(t),'event':'MANAGEMENT_GEOMETRY_CANCEL','key':str(key),'excludedFromRepairFailureChurn':True})
        return bool(ok)
    def _request_cancel(self,t,row,reason):
        if self._cancel_without_failure_churn(int(t),row['key']):
            self.pendingRollingCancel={'key':row['key'],'side':row['side'],'parentId':int(row['parentId']),'objectiveId':row['objectiveId'],'reason':reason,'oldPrice':row['price']}
            self.repeatedRollingEvents.append({'t':int(t),'event':'REPEATED_ROLLING_CANCEL_REQUEST','key':row['key'],'side':row['side'],'parentId':int(row['parentId']),'oldPrice':row['price'],'reason':reason,'slotCeiling':self.slotCeiling,'failureChurnCounted':False})
            return True
        return False
    def run_neutral(self,models,winner):
        r=self.run_repeated(models,winner)
        churn=list(getattr(self,'repairChurn',[]));r.update({'managementCancelKeys':list(self.managementCancelKeys),'managementCancelEvents':self.managementCancelEvents,'repairChurnCount':len(churn),'repairChurnEvents':churn[:100],'managementCancelLeakedIntoChurn':any(str(x.get('key')) in set(self.managementCancelKeys) for x in churn)})
        return r

def slim(r):return base.slim(r)
def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='multislot_cancel_neutral_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=pe.make(ManagementCancelNeutralHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_neutral(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=slim(r)
        gates={'managementCancelOccurred':len(r.get('managementCancelKeys') or [])>0,'managementCancelExcludedFromFailureChurn':not bool(r.get('managementCancelLeakedIntoChurn')),'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ_bound,'noNewRepairParent':m['repairParentBirths']==1}
        if not all([gates['managementCancelOccurred'],gates['managementCancelExcludedFromFailureChurn'],safe,bool(cons),bool(bound),occ_bound,gates['noNewRepairParent']]):decision='MANAGEMENT_CANCEL_NEUTRALITY_FAIL'
        else:decision='MANAGEMENT_CANCEL_NEUTRALITY_FUNCTIONAL_PASS'
        out={'version':'MULTISLOT_MANAGEMENT_CANCEL_NEUTRALITY_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'deltaVsCountedCancel':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-COUNTED_CANCEL['pnlDiagnosticOnly'],'floor':m['floor']-COUNTED_CANCEL['floor'],'activeRepairFillQty':m['activeRepairFillQty']-COUNTED_CANCEL['activeRepairFillQty']},'deltaVsFrozenPartition':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-FROZEN_PARTITION['pnlDiagnosticOnly'],'floor':m['floor']-FROZEN_PARTITION['floor'],'activeRepairFillQty':m['activeRepairFillQty']-FROZEN_PARTITION['activeRepairFillQty']},'gates':gates,'managementCancelKeys':r.get('managementCancelKeys'),'managementCancelEvents':r.get('managementCancelEvents'),'repairChurnCount':r.get('repairChurnCount'),'repairChurnEvents':r.get('repairChurnEvents'),'slotCeilingFinal':r.get('slotCeilingFinal'),'contractionEvents':r.get('contractionEvents'),'reanchorCycles':r.get('reanchorCycles'),'repeatedRollingSubmits':r.get('repeatedRollingSubmits'),'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'events':r.get('repeatedRollingEvents',[])[:600],'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['single market 1946475','only change: slot-geometry management cancel bypasses V34 failure-churn accounting','true TTL/ordinary Repair cancellation still counts as failure churn','all reservation/cancel-pending/terminal accounting preserved','Active router/economics/qty/price unchanged','no Target runtime input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'deltaVsCountedCancel':out['deltaVsCountedCancel'],'deltaVsFrozenPartition':out['deltaVsFrozenPartition'],'gates':gates,'repairChurnCount':r.get('repairChurnCount'),'managementCancelKeys':r.get('managementCancelKeys'),'repairChurnEvents':(r.get('repairChurnEvents') or [])[:8],'events':out['events'][:16],'safety':ss},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
