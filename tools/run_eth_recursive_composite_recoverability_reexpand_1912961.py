from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9; MID=1912961; MARKET_DURATION_MS=300000

def sib(name,file):
    p=Path(__file__).with_name(file); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

v90d=sib('eth_v90d_for_recursive_reexpand','run_eth_repair_v90d_min_legal_incremental_repair_1912961.py')
try:
    from tools.eth_repair_modular.recoverability import RecursiveCompositeCurrentCoordinateRecoverabilityPolicy, RecursiveCompositeRecoverabilityContext
except Exception:
    rec=sib('recursive_recoverability_fallback','recoverability.py')
    RecursiveCompositeCurrentCoordinateRecoverabilityPolicy=rec.RecursiveCompositeCurrentCoordinateRecoverabilityPolicy
    RecursiveCompositeRecoverabilityContext=rec.RecursiveCompositeRecoverabilityContext
v90=v90d.v90;v80=v90.v80;v38=v90.v38;v1=v90.v1

class RecursiveRecoverabilityReexpand(v90d.V90DMinLegalIncremental):
    def __init__(self,*a,**kw):
        self.recPolicy=RecursiveCompositeCurrentCoordinateRecoverabilityPolicy()
        self.recChecks=0;self.recPasses=0;self.recRows=[];self.recReclassifyClock=None
        self.v90dFillSeen={};self.v90dFirstActualFillAt=None;self.v90dConfirmedFillQty=0.0
        self.postPartialExpandKeys=set();self.postPartialExpandFillSeen={};self.postPartialExpandFirstFillAt=None;self.postPartialExpandFillQty=0.0;self.postPartialExpandSide=None
        self.postReexpandRepairActualFillQty=0.0;self.postReexpandRepairEvents=[]
        super().__init__(*a,**kw)
    def _phase(self,t):
        start=int(self.capEnd)-MARKET_DURATION_MS
        return max(0.0,min(1.0,(int(t)-start)/float(MARKET_DURATION_MS)))
    def _confirmed_v90d_fill(self):
        return sum(float(self.carrierLedger.get(k,{}).get('actualFilled') or 0.0) for k in getattr(self,'v90dSubmitKeys',[]))
    def _v75_recoverability(self,t,side,qv):
        z=dict(super()._v75_recoverability(t,side,qv));self.recChecks+=1
        z['legacyRecoverable']=bool(z.get('recoverable'));z['legacyReason']=z.get('reason')
        if z.get('recoverable'):
            return z
        if self._confirmed_v90d_fill()<=EPS:
            return z
        if z.get('reason') not in ('FUTURE_REPAIR_QTY_EXCEEDS_ROOM','NO_ADMISSIBLE_FUTURE_REPAIR_PRICE'):
            return z
        if self.postPartialExpandKeys and self.recReclassifyClock is not None and int(t)!=int(self.recReclassifyClock):
            return z
        if side not in ('UP','DOWN') or not qv or qv['UP'].get('bid') is None or qv['DOWN'].get('bid') is None:
            return z
        floor_before=z.get('floorBefore'); projected=z.get('projectedFloorAfterOwnedRepair',z.get('hypFloorAfterExpand')); debt=z.get('repairRoomAfterOwned',z.get('repairGapAfterExpand'))
        if floor_before is None or projected is None or debt is None:
            return z
        ctx=RecursiveCompositeRecoverabilityContext(
            floor_before_expand=float(floor_before),floor_after_expand=float(projected),debt_side=str(side),repair_debt=float(debt),
            up_bid=float(qv['UP']['bid']),down_bid=float(qv['DOWN']['bid']),max_carriers=4,max_venue_qty=12.0)
        dec=self.recPolicy.evaluate(ctx)
        row={'t':int(t),'normalizedPhase':self._phase(t),'secondsLeftDiagnosticOnly':(int(self.capEnd)-int(t))/1000.0,'side':side,'pExpand':None,'legacyReason':z.get('reason'),'legacyRecoverable':False,'pairSumCurrentBid':dec.pair_sum,'recoverable':dec.recoverable,'policyReason':dec.reason,'recoveredStep':dec.recovered_step,'terminalFloor':dec.terminal_floor,'terminalDebt':dec.terminal_debt,'maxDebt':dec.max_debt,'path':[dict(x) for x in dec.path]}
        self.recRows.append(row)
        if dec.recoverable:
            self.recPasses+=1;self.recReclassifyClock=int(t)
            z.update({'recoverable':True,'reason':'RECURSIVE_COMPOSITE_CURRENT_COORDINATE_RECOVERABLE','recursiveCompositeRecoveredStep':dec.recovered_step,'recursiveCompositePairSum':dec.pair_sum,'recursiveCompositeMaxDebt':dec.max_debt,'recursiveCompositePath':[dict(x) for x in dec.path],'legacyRecoverable':False,'legacyReason':row['legacyReason']})
        return z
    def _score_state(self,t,after_kind):
        n=len(getattr(self,'v83Admissions',[]));out=super()._score_state(t,after_kind)
        if self.v90dFirstActualFillAt is not None:
            for row in (getattr(self,'v83Admissions',[]) or [])[n:]:
                if row.get('submit') and int(row.get('t') or -1)>=int(self.v90dFirstActualFillAt) and row.get('key'):
                    self.postPartialExpandKeys.add(str(row['key']));self.postPartialExpandSide=row.get('side')
                    row['normalizedPhase']=self._phase(int(row['t']))
        return out
    def process(self,t):
        auth0=len(getattr(self,'authHist',[]));super().process(t)
        # Track confirmed V90D partial Repair fill.
        for key in getattr(self,'v90dSubmitKeys',[]):
            now=float(self.carrierLedger.get(key,{}).get('actualFilled') or 0.0);old=float(self.v90dFillSeen.get(key,0.0))
            if now>old+EPS:
                inc=now-old;self.v90dConfirmedFillQty+=inc
                if self.v90dFirstActualFillAt is None:self.v90dFirstActualFillAt=int(t)
            self.v90dFillSeen[key]=max(old,now)
        # Track actual fill of post-partial re-Expand carrier(s).
        for key in list(self.postPartialExpandKeys):
            now=float(self.carrierLedger.get(key,{}).get('actualFilled') or 0.0);old=float(self.postPartialExpandFillSeen.get(key,0.0))
            if now>old+EPS:
                inc=now-old;self.postPartialExpandFillQty+=inc
                if self.postPartialExpandFirstFillAt is None:self.postPartialExpandFirstFillAt=int(t)
            self.postPartialExpandFillSeen[key]=max(old,now)
        # Any strictly-later authoritative fill classified by the existing ledger as REPAIR counts as physical recovery support.
        if self.postPartialExpandFirstFillAt is not None:
            for x in (getattr(self,'authHist',[]) or [])[auth0:]:
                if int(x.get('time') or -1)>int(self.postPartialExpandFirstFillAt) and int(x.get('rel') or 0)==1:
                    q=float(x.get('shares') or 0.0);self.postReexpandRepairActualFillQty+=q
                    self.postReexpandRepairEvents.append({'t':int(x['time']),'normalizedPhase':self._phase(int(x['time'])),'secondsLeftDiagnosticOnly':(int(self.capEnd)-int(x['time']))/1000.0,'side':x.get('side'),'shares':q,'price':x.get('price')})
    def run_candidate(self,models,winner):
        r=self.run_v90d(models,winner)
        r.update({'recursiveRecoverabilityChecks':self.recChecks,'recursiveRecoverabilityPasses':self.recPasses,'recursiveRecoverabilityRows':self.recRows[:320],
                  'v90dFirstActualFillAt':self.v90dFirstActualFillAt,'v90dConfirmedFillQtyRuntime':self.v90dConfirmedFillQty,
                  'postPartialExpandKeys':sorted(self.postPartialExpandKeys),'postPartialExpandFirstFillAt':self.postPartialExpandFirstFillAt,'postPartialExpandFillQty':self.postPartialExpandFillQty,'postPartialExpandSide':self.postPartialExpandSide,
                  'postReexpandRepairActualFillQty':self.postReexpandRepairActualFillQty,'postReexpandRepairEvents':self.postReexpandRepairEvents[:120]})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=MID:raise ValueError(a.market_id)
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='eth_recursive_reexpand_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'RECURSIVE_RECOVERABILITY_REEXPAND','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'RECURSIVE_RECOVERABILITY_REEXPAND_START','market':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        c=RecursiveRecoverabilityReexpand(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        try:rr=c.run_candidate(models,cr['winner'])
        finally:c.close()
        safety=v90.safety_summary(rr);cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        out={'version':'RECURSIVE_COMPOSITE_RECOVERABILITY_REEXPAND_1912961_CANDIDATE','date':'2026-09-04','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],
             'candidate':{'fills':rr.get('actualFillEvents'),'pnlDiagnosticOnly':rr.get('pnlDiagnosticOnly'),'terminalFloor':rr.get('floor'),'worstObservedFloor':v90.actual_floor_min(rr),'shareSettlements':rr.get('v80ShareRepairSettlements'),'rounds':rr.get('v70dSemanticRounds'),'v90dFillQty':rr.get('v90dFillQty'),'v90dResidualDebt':rr.get('v90dTerminalAbsGap'),'recursiveRecoverabilityChecks':rr.get('recursiveRecoverabilityChecks'),'recursiveRecoverabilityPasses':rr.get('recursiveRecoverabilityPasses'),'postPartialExpandKeys':rr.get('postPartialExpandKeys'),'postPartialExpandFillQty':rr.get('postPartialExpandFillQty'),'postReexpandRepairActualFillQty':rr.get('postReexpandRepairActualFillQty')},
             'physicalChain':{'v90dFirstActualFillAt':rr.get('v90dFirstActualFillAt'),'postPartialExpandFirstFillAt':rr.get('postPartialExpandFirstFillAt'),'postReexpandRepairEvents':rr.get('postReexpandRepairEvents')},
             'recursiveRecoverabilityRows':rr.get('recursiveRecoverabilityRows'),
             'v83Admissions':rr.get('v83Admissions',[])[:240],'safety':safety,'allocationConservation':cons,
             'timeSemantics':{'managementClock':'event/progress + normalized phase','normalizedPhaseFormula':'elapsed / 300s market duration','secondsLeft':'diagnostic only','fixedOurSafetyFenceSeconds':180,'targetFixedSecondRuleClaim':False},
             'boundary':['single consumed development market','only recoverability interpretation changes','no direct action from recoverability policy','pExpand 0.50 frozen','V90D tranche frozen','ResponsibilityTransition/AllocationLedgerV2/RepairExecutionRouter frozen','<=180s OUR new-exposure safety fence frozen','no Target runtime input','realistic HFT only','no dream fill','no 8781']}
        op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'candidate':out['candidate'],'safety':safety,'allocationConservation':cons,'chain':out['physicalChain']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
