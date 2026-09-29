from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def load_sibling(name,filename):
    p=Path(__file__).with_name(filename);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(f'cannot load {filename}')
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v36=load_sibling('eth_v36_for_v38','run_eth_repair_v36_event_confirmed_shared_active_child.py')
v37=load_sibling('eth_v37_for_v38','run_eth_repair_v37_parallel_surplus_credit_recycle.py')
EPS=1e-9

class IncrementalRepairMixin:
    def _v38_init(self):
        self.v38IncrementalQtyCapEvents=0;self.v38FullNeedQtySaved=0.0;self.v38RepairFillSeen={};self.v38PartialProgressEvents=[];self.v38PassiveRepairFillEvents=[]
    def _repair_payoff_budget(self,p):
        b=super()._repair_payoff_budget(p)
        if not b or not b.get('feasible'):return b
        deficit=float(b.get('deficit') or 0.0)
        if deficit<=EPS:return b
        legal=float(b.get('legal') or 0.0);room=float(b.get('room') or 0.0);old=float(b.get('qty') or 0.0)
        if legal<=EPS or legal>room+EPS:return b
        if old>legal+EPS:
            z=dict(b);z['fullPayoffQtyBeforeIncrementalCap']=old;z['qty']=legal;z['incrementalVenueMinTranche']=True
            self.v38IncrementalQtyCapEvents+=1;self.v38FullNeedQtySaved+=old-legal;return z
        return b
    def _v38_scan_partial_progress(self,t):
        for key,e in list(getattr(self,'carrierLedger',{}).items()):
            if e.get('objectiveRole')!='REPAIR':continue
            if str(e.get('lane') or '').startswith('ACTIVE_'):continue
            pid=e.get('parentId')
            if pid is None:continue
            now=float(e.get('actualFilled') or 0.0);old=float(self.v38RepairFillSeen.get(key,0.0))
            if now<=old+EPS:
                self.v38RepairFillSeen[key]=max(old,now);continue
            inc=now-old;self.v38RepairFillSeen[key]=now
            floor=float(self._raw_floor()[0]);row={'t':int(t),'key':key,'parentId':int(pid),'incQty':inc,'floorAfterFill':floor,'partialProgressWithDebtRemaining':bool(floor<-EPS and self.repairParent is not None and int(self.repairParent.get('id'))==int(pid))}
            self.v38PassiveRepairFillEvents.append(row)
            if row['partialProgressWithDebtRemaining']:self.v38PartialProgressEvents.append(row)
    def _v38_metrics(self):
        return {'v38IncrementalQtyCapEvents':self.v38IncrementalQtyCapEvents,'v38FullNeedQtySaved':self.v38FullNeedQtySaved,'v38PassiveRepairFillEvents':len(self.v38PassiveRepairFillEvents),'v38PartialProgressEvents':len(self.v38PartialProgressEvents),'v38PartialProgressQty':sum(float(x['incQty']) for x in self.v38PartialProgressEvents),'v38PartialProgressRows':self.v38PartialProgressEvents[:80]}

class V38IncrementalOnly(IncrementalRepairMixin,v36.V36EventConfirmedActive):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self._v38_init()
    def process(self,t):
        super().process(t);self._v38_scan_partial_progress(t)
    def run_exam_v38a(self,models,winner):
        r=super().run_exam_v36(models,winner);r.update(self._v38_metrics());return r

class V38IncrementalRecycle(IncrementalRepairMixin,v37.V37ParallelSurplus):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self._v38_init()
    def process(self,t):
        super().process(t);self._v38_scan_partial_progress(t)
    def run_exam_v38b(self,models,winner):
        r=super().run_exam_v37(models,winner);r.update(self._v38_metrics());return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v38_incremental_'))
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(10):print(json.dumps({'heartbeat':'V38_RUN','ts':time.time()}),flush=True)
    threading.Thread(target=heartbeat,daemon=True).start();print(json.dumps({'heartbeat':'V38_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort}
        models,life,cap,tim,econ,price,sur=v36.v34.v30.load_runtime(a);rows=[]
        for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
            cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            s0=v36.V36EventConfirmedActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
            try:b=s0.run_exam_v36(models,cr['winner'])
            finally:s0.close()
            s1=V38IncrementalOnly(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
            try:i=s1.run_exam_v38a(models,cr['winner'])
            finally:s1.close()
            s2=V38IncrementalRecycle(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
            try:r=s2.run_exam_v38b(models,cr['winner'])
            finally:s2.close()
            rows.append({'marketId':mid,'baselineV36':b,'incrementalOnly':i,'incrementalRecycle':r})
            print(json.dumps({'marketId':mid,'partialOnly':i['v38PartialProgressEvents'],'partialRecycle':r['v38PartialProgressEvents'],'incCaps':i['v38IncrementalQtyCapEvents'],'creditMinted':r['v37CreditMintedQty'],'surplusFillQty':r['v37SurplusFillQty'],'matchedGain':r['v37SurplusMatchedGain'],'floors':[b['floor'],i['floor'],r['floor']],'absNet':[b.get('absNet'),i.get('absNet'),r.get('absNet')],'truth':[b.get('authorizedSubmitWithTruthRoleMismatch'),i.get('authorizedSubmitWithTruthRoleMismatch'),r.get('authorizedSubmitWithTruthRoleMismatch')]},ensure_ascii=False),flush=True)
        def sm(side,key):return sum(float(x[side].get(key) or 0.0) for x in rows)
        agg={
          'markets':len(rows),
          'incrementalOnlyCapEvents':int(sm('incrementalOnly','v38IncrementalQtyCapEvents')),
          'incrementalOnlyPartialProgressEvents':int(sm('incrementalOnly','v38PartialProgressEvents')),
          'incrementalOnlyPartialProgressQty':sm('incrementalOnly','v38PartialProgressQty'),
          'recyclePartialProgressEvents':int(sm('incrementalRecycle','v38PartialProgressEvents')),
          'recyclePartialProgressQty':sm('incrementalRecycle','v38PartialProgressQty'),
          'creditMintedQty':sm('incrementalRecycle','v37CreditMintedQty'),'surplusSubmits':int(sm('incrementalRecycle','v37SurplusSubmitCount')),'surplusFillQty':sm('incrementalRecycle','v37SurplusFillQty'),'surplusMatchedGain':sm('incrementalRecycle','v37SurplusMatchedGain'),
          'nonPositivePairFillQty':sm('incrementalRecycle','v37NonPositivePairFillQty'),'creditOverspend':sm('incrementalRecycle','v37CreditOverspend'),'crossParentCreditLeak':int(sm('incrementalRecycle','v37CrossParentCreditLeak')),'lateSurplusFillAfterParentCompletion':sm('incrementalRecycle','v37LateSurplusFillAfterParentCompletion'),
          'baselineFloorSum':sm('baselineV36','floor'),'incrementalOnlyFloorSum':sm('incrementalOnly','floor'),'incrementalRecycleFloorSum':sm('incrementalRecycle','floor'),
          'baselineAbsNetSum':sm('baselineV36','absNet'),'incrementalOnlyAbsNetSum':sm('incrementalOnly','absNet'),'incrementalRecycleAbsNetSum':sm('incrementalRecycle','absNet'),
          'incrementalOnlyTruthMismatch':sm('incrementalOnly','authorizedSubmitWithTruthRoleMismatch'),'incrementalOnlyOverOwned':sm('incrementalOnly','overOwnedSubmitViolations'),'incrementalOnlyRepairDrift':sm('incrementalOnly','repairToExpandAtFirstFill'),
          'recycleTruthMismatch':sm('incrementalRecycle','authorizedSubmitWithTruthRoleMismatch'),'recycleOverOwned':sm('incrementalRecycle','overOwnedSubmitViolations'),'recycleRepairDrift':sm('incrementalRecycle','repairToExpandAtFirstFill')
        }
        gates={
          'partialProgressExercised':agg['incrementalOnlyPartialProgressEvents']>0,
          'incrementalOnlyAccountingSafe':agg['incrementalOnlyTruthMismatch']==0 and agg['incrementalOnlyOverOwned']==0 and agg['incrementalOnlyRepairDrift']==0,
          'recycleAccountingSafe':agg['recycleTruthMismatch']==0 and agg['recycleOverOwned']==0 and agg['recycleRepairDrift']==0 and agg['creditOverspend']<=1e-7 and agg['crossParentCreditLeak']==0 and agg['lateSurplusFillAfterParentCompletion']<=EPS,
          'recycleEconomicsSafeIfExercised':agg['surplusFillQty']<=EPS or (agg['nonPositivePairFillQty']<=EPS and agg['surplusMatchedGain']>EPS)
        }
        out={'version':'ETH_REPAIR_V38_INCREMENTAL_REPAIR_PARALLEL_SURPLUS_CAUSAL','researchOnly':True,'behaviorChange':True,'aggregate':agg,'gates':gates,'stageAStructuralPass':all(gates.values()),'rows':rows,'boundary':['three-way V36 vs venue-minimum incremental Repair vs incremental+V37 recycle','Repair price selection unchanged','no fitted Repair fraction; passive tranche=venue legal minimum only','V37 recycle rule unchanged r+e<1','winner/PnL excluded from rule/gates','realistic HFT only','no dream fill','no 8781','<=180s no-new-exposure inherited']}
        Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':all(gates.values()),'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
