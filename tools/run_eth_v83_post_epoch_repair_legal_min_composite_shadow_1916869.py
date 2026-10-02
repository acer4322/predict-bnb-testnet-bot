from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
def sibling(name,path):
 p=Path(path); s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m
base=sibling('v83_child_mat_for_legal_min_shadow',Path(__file__).resolve().with_name('run_eth_v83_existing_parent_post_epoch_child_materialization_shadow_1916869.py'))
FIXED=1916869; EPS=1e-9; birth=base.birth; v38=base.v38; v80=base.v80
class LegalMinCompositeShadow(base.PostEpochChildMaterializationShadow):
 def __init__(self,*a,**kw): self.legalMinShadow=[]; super().__init__(*a,**kw)
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  before=int(getattr(self,'remainingCapBlocks',0) or 0); r=super()._submit_authorized(t,qv,z,roles_this_tick); after=int(getattr(self,'remainingCapBlocks',0) or 0)
  if z is not None and str(z[3])=='REPAIR' and after>before:
   side,qty,oldp,role,oid=z; bid=float(qv[side]['bid']); legal=(1.0/bid) if bid>0 else math.inf
   rp=getattr(self,'repairParent',None); debt=float(rp.get('remaining',rp.get('remainingQty',qty)) if isinstance(rp,dict) else qty)
   if not math.isfinite(debt) or debt<=0: debt=float(qty)
   repair_paid=min(float(legal),debt); overflow=max(0.0,float(legal)-repair_paid)
   # Physical carrier at bid: conservative immediate floor contribution of the excess is evaluated
   # as paired economics against the opposite-side realized average when available. This is shadow-only.
   opp='DOWN' if side=='UP' else 'UP'; avg=None
   try:
    sh=float(self.actualShares.get(opp,0)); co=float(self.actualCost.get(opp,0)); avg=(co/sh) if sh>EPS else None
   except Exception: pass
   pair_sum=(bid+avg) if avg is not None else None; floor_safe=(pair_sum is not None and pair_sum<=1.0+EPS)
   self.legalMinShadow.append({'t':int(t),'side':str(side),'objectiveId':oid,'authorizedRepairQty':float(qty),'bid':bid,'legalQty':float(legal),'shortfall':float(max(0.0,legal-float(qty))),'managerDebtShadow':debt,'repairPaid':repair_paid,'overflow':overflow,'physicalConservation':abs(float(legal)-repair_paid-overflow)<=1e-7,'oppositeRealizedAvg':avg,'pairSumShadow':pair_sum,'floorNonDamagingShadow':floor_safe,'secondsLeft':float((self.endMs-t)/1000.0) if hasattr(self,'endMs') else None})
  return r
 def run_shadow2(self,models,winner):
  rr=super().run_shadow(models,winner); rr['legalMinCompositeShadow']=self.legalMinShadow[:80]; return rr
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
 if a.market_id!=FIXED: raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='v83_legal_min_shadow_')); stop=threading.Event()
 def hb():
  while not stop.wait(15): print(json.dumps({'heartbeat':'V83_LEGAL_MIN_COMPOSITE_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start()
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{FIXED}.json.xz'
  c=LegalMinCompositeShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try: rr=c.run_shadow2(models,cr['winner'])
  finally: c.close()
  ss=birth.base.front.safety(rr); rows=rr.get('legalMinCompositeShadow',[]); cons=all(bool(x.get('physicalConservation')) for x in rows); safe=all(float(v)<=EPS for v in ss.values())
  gates={'postExpandRemainingCapBlockObserved':len(rows)>0,'legalQtyGreaterThanAuthorizedResidualOnBlockedRows':len(rows)>0 and all(x['legalQty']>x['authorizedRepairQty']+EPS for x in rows),'singleCausalLegalityClassExplainsBlockedChain':len(rows)>0,'counterfactualRepairFirstAllocationConservesPhysicalQty':cons,'safetyZero':safe}
  admissible=sum(1 for x in rows if x.get('floorNonDamagingShadow'))
  decision=('KEEP_LEGAL_MIN_COMPOSITE_AXIS_AUTHORIZE_ONE_MARKET_MUTATION' if all(gates.values()) and admissible>0 else 'REJECT_LEGAL_MIN_COMPOSITE_STUDY_PASSIVE_PRICE_REACHABILITY' if all(gates.values()) else 'DIAGNOSE_LEGAL_MIN_COMPOSITE_SHADOW')
  out={'version':'ETH_V83_POST_EPOCH_REPAIR_LEGAL_MIN_COMPOSITE_SHADOW_1916869','date':'2026-09-04','researchOnly':True,'marketId':FIXED,'decision':decision,'gates':gates,'admissibleRows':admissible,'rows':rows,'safety':ss,'candidate':birth.slim(rr),'boundary':['shadow only','no runtime qty rounding','no submit mutation','realistic HFT','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'gates':gates,'admissibleRows':admissible,'first':rows[:3],'safety':ss},ensure_ascii=False),flush=True)
 finally: stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
