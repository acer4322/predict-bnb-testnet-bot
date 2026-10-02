from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9
FIXED=1912961

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v83=sib('eth_v83_for_v84','run_eth_repair_v83_clean_modular_candidate_smoke.py')
v80=v83.v80;v38=v83.v38;v1=v83.v1

class V84CompositeRepairCrossingShadow(v83.V83CleanModularCandidate):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v84Rows=[];self.v84Seen=set()
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is not None:
   side,qty,oldp,role,oid=z;rb=getattr(self,'reserveBuilder',None)
   if role=='REPAIR' and rb is not None and rb.get('firstFillAt') is not None and int(t)<=int(rb.get('pairDeadline') or -1):
    try:
     floor,u,d,cost=self._raw_floor();ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));p=float(qv[side]['bid']);legal=1.0/p if p>EPS else math.inf
     if math.isfinite(legal) and 0<p<1 and gap>EPS:
      alloc=min(legal,gap);overflow=max(0.0,legal-gap)
      hu=float(u)+(legal if side=='UP' else 0.0);hd=float(d)+(legal if side=='DOWN' else 0.0);hc=float(cost)+legal*p;hf=min(hu,hd)-hc
      pair=float(rb.get('firstPrice') or 0.0)+p
      key=(round(p,4),round(gap,6),round(float(floor),6))
      if key not in self.v84Seen:
       self.v84Seen.add(key);self.v84Rows.append({'t':int(t),'side':side,'proposalQty':float(qty),'firstPrice':float(rb.get('firstPrice') or 0.0),'passiveRepairPrice':p,'pairSum':pair,'floorBefore':float(floor),'repairGap':gap,'venueMinQty':legal,'venueMinCrossesGap':bool(legal>gap+EPS),'repairAllocation':alloc,'overflowAllocation':overflow,'hypFloorAfterVenueMin':hf,'hypFloorDelta':hf-float(floor),'compositeFloorImproving':bool(hf>float(floor)+EPS),'secondsLeft':(int(self.capEnd)-int(t))/1000.0})
    except Exception as ex:
     self.v84Rows.append({'t':int(t),'error':str(ex)})
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_exam_v84(self,models,winner):
  r=super().run_exam_v83(models,winner);r.update({'v84CompositeRepairShadowRows':self.v84Rows});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=FIXED:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v84_shadow_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V84_COMPOSITE_REPAIR_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V84_COMPOSITE_REPAIR_SHADOW_START','market':FIXED}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};cr=by[FIXED]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
  b=v83.V83CleanModularCandidate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:br=b.run_exam_v83(models,cr['winner'])
  finally:b.close()
  c=V84CompositeRepairCrossingShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:rr=c.run_exam_v84(models,cr['winner'])
  finally:c.close()
  rows=rr.get('v84CompositeRepairShadowRows',[]);cross=[x for x in rows if not x.get('error') and x.get('venueMinCrossesGap')];good=[x for x in cross if x.get('compositeFloorImproving') and float(x.get('overflowAllocation') or 0)>EPS]
  parity=all(abs(float(rr.get(k) or 0)-float(br.get(k) or 0))<=1e-9 for k in ['floor','pnlDiagnosticOnly','actualFillEvents','v36ActiveFillQty','v64FillQty'])
  gates={'legacyRepairBlocked':int(rr.get('packageRepairBlock') or 0)>0,'crossingContextObserved':len(cross)>0,'floorImprovingCompositeObserved':len(good)>0,'shadowBehaviorParity':parity,'zeroSafetyChange':sum(float(rr.get(k) or 0) for k in ['authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])<=EPS}
  decision='KEEP_COMPOSITE_REPAIR_CARRIER_AS_NEXT_FUNCTIONAL_AXIS' if all(gates.values()) else 'REJECT_OR_REFINE_COMPOSITE_REPAIR_CROSSING_HYPOTHESIS'
  out={'version':'ETH_REPAIR_V84_COMPOSITE_REPAIR_CROSSING_SHADOW','date':'2026-09-03','researchOnly':True,'marketId':FIXED,'decision':decision,'gates':gates,'aggregate':{'uniqueBlockedRepairStates':len(rows),'crossingStates':len(cross),'floorImprovingCompositeStates':len(good),'legacyPackageRepairBlocks':int(rr.get('packageRepairBlock') or 0),'baselineFloor':br.get('floor'),'shadowFloor':rr.get('floor'),'baselinePnl':br.get('pnlDiagnosticOnly'),'shadowPnl':rr.get('pnlDiagnosticOnly')},'rows':rows,'boundary':['shadow only','frozen V83 behavior unchanged','no action authority','no tuning','realistic HFT only','winner post-hoc only','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'aggregate':out['aggregate'],'examples':good[:8]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
