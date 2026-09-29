from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,joblib,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sibling(name,path):
 p=Path(path);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('resp_transition_for_post_settlement_shadow',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
g=front.g;v38=front.v38;v80=front.v80;v1=g.v1;EPS=1e-9;FIXED=1916845

class PostSettlementRebirthShadow(front.ResponsibilityTransitionCandidate):
 def __init__(self,*a,**kw):self.postSettlementRows=[];super().__init__(*a,**kw)
 def _allocation_aware(self,z):
  out=dict(z);changed=False
  if not bool(out.get('recoverable')) and str(out.get('reason'))=='FUTURE_REPAIR_QTY_EXCEEDS_ROOM':
   need=out.get('futureNeedQty');room=out.get('repairRoomAfterOwned');req=out.get('futureRequiredQty')
   if need is not None and room is not None and req is not None and float(need)<=float(room)+EPS:
    physical=float(req);repair=min(physical,float(room));overflow=max(0.,physical-repair);out.update({'recoverable':True,'reason':'PASS_ALLOCATION_AWARE_COMPOSITE_OVERFLOW','legacyReason':'FUTURE_REPAIR_QTY_EXCEEDS_ROOM','physicalCarrierQty':physical,'prospectiveRepairAllocationQty':repair,'prospectiveOverflowQty':overflow});changed=True
  return out,changed
 def _score_state(self,t,after_kind):
  ret=super()._score_state(t,after_kind)
  if after_kind!='REPAIR':return ret
  debt=float(getattr(self,'_coordDebt',0.) or 0.);th=getattr(self,'thesis',None);rp=getattr(self,'repairParent',None);seconds=(int(self.capEnd)-int(t))/1000.
  if debt>EPS or th is not None or not isinstance(rp,dict) or seconds<=180.:return ret
  qv=v1.quotes(self.book);row={'t':int(t),'coordDebt':debt,'secondsLeft':seconds,'repairParentId':rp.get('id'),'quotesAvailable':bool(qv),'floor':self._raw_floor()[0]}
  if not qv:row['reason']='NO_QUOTES';self.postSettlementRows.append(row);return ret
  side=self._signal_side(qv);row['signalSide']=side
  if side not in ('UP','DOWN'):row['reason']='NO_SIGNAL_SIDE';self.postSettlementRows.append(row);return ret
  legacy=self._v75_recoverability(t,side,qv);aware,changed=self._allocation_aware(legacy);row.update({'legacyRecoverability':legacy,'allocationAwareRecoverability':aware,'allocationAwareChanged':changed,'structurallyFeasible':bool(aware.get('recoverable')),'reason':'POST_SETTLEMENT_STRUCTURAL_CHECK'});self.postSettlementRows.append(row);return ret
 def run_shadow(self,models,winner):
  r=self.run_transition(models,winner);r.update({'postSettlementRebirthStructuralRows':self.postSettlementRows,'postSettlementRebirthStructuralChecks':len(self.postSettlementRows)});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='post_settle_rebirth_'));stop=threading.Event()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'POST_SETTLEMENT_REBIRTH_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'POST_SETTLEMENT_REBIRTH_SHADOW_START','market':FIXED}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz';s=PostSettlementRebirthShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:r=s.run_shadow(models,cr['winner'])
  finally:s.close()
  rows=r.get('postSettlementRebirthStructuralRows',[]);feasible=sum(bool(x.get('structurallyFeasible')) for x in rows);out={'version':'ETH_POST_SETTLEMENT_REBIRTH_STRUCTURAL_SHADOW_V1','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'marketId':FIXED,'checks':len(rows),'structurallyFeasibleChecks':feasible,'decision':'POST_SETTLEMENT_CONTINUATION_MODULE_IS_MISSING_SEAM' if feasible>0 else 'NO_STRUCTURALLY_FEASIBLE_REBIRTH_IN_SMOKE','rows':rows,'baselineDiagnostics':{'fills':r.get('actualFillEvents'),'floor':r.get('floor'),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'v83Checks':r.get('v83AdmissionChecks'),'v83Allows':r.get('v83AdmissionAllows')},'stableNextCompositeShadow':'KEEP_UNCHANGED_NO_AUTHORITY','boundary':['instrumentation only','no submit/thesis birth','ResponsibilityTransition frozen','AllocationLedger V2 frozen','no pExpand authority','no tuning','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'checks':len(rows),'feasible':feasible,'rows':rows},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
