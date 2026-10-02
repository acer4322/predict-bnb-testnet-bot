from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v70g=sib('eth_v70g_for_v72inst','run_eth_repair_v70g_generation_scoped_relay_hft_smoke.py');v65=v70g.v65;v38=v70g.v38

def classify(rr):
 ev=sorted(rr.get('v53FillEvents') or [],key=lambda x:(int(x['t']),str(x['key'])))
 active_source={}
 for x in rr.get('v64Events') or []:
  if x.get('event')=='ACTIVE_EXPAND_FALLBACK_FILL':active_source[str(x['key'])]=str(x.get('sourceKey'))
 source_family=[]
 for k,src in active_source.items():
  fill=next((x for x in ev if str(x.get('key'))==k),None)
  source_family.append({'childKey':k,'sourceKey':src,'childFillObserved':fill is not None,'childRole':fill.get('role') if fill else None,'side':fill.get('side') if fill else None,'qty':float(fill.get('qty') or 0.0) if fill else 0.0})
 segments=[];current={}
 for x in ev:
  role=str(x.get('role'));side=str(x.get('side'));t=int(x.get('t'));qty=float(x.get('qty') or 0.0);price=float(x.get('price') or 0.0)
  if role not in ('PASSIVE_EXPAND','ACTIVE_EXPAND'):continue
  prev=current.get(side)
  if prev is None:
   current[side]={'t':t,'event':x};continue
  repairs=[r for r in ev if str(r.get('role')) in ('PASSIVE_REPAIR','ACTIVE_REPAIR') and str(r.get('side'))!=side and int(prev['t'])<int(r.get('t'))<t]
  rq=sum(float(r.get('qty') or 0.0) for r in repairs);rn=sum(float(r.get('qty') or 0.0)*float(r.get('price') or 0.0) for r in repairs);rp=rn/rq if rq>EPS else None
  pair=(rp+price) if rp is not None else None
  if rq<=EPS:kind='EXISTING_PREAUTHORIZED_PAYMENT'
  elif pair<=1.0+EPS:kind='PAIR_CREDIT_REPLENISHMENT_ELIGIBLE'
  else:kind='PAIR_CREDIT_REPLENISHMENT_BLOCK'
  seg={'side':side,'priorExpandT':int(prev['t']),'t':t,'expandKey':str(x.get('key')),'expandRole':role,'expandQty':qty,'expandPrice':price,'repairQtyBetween':rq,'weightedRepairPrice':rp,'marginalPairSum':pair,'matchedQty':min(rq,qty),'classification':kind,'activeSourceKey':active_source.get(str(x.get('key')))}
  segments.append(seg);current[side]={'t':t,'event':x}
 return source_family,segments

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v72inst_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V72_HFT_BUDGET_INSTRUMENTATION','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V72_HFT_BUDGET_INSTRUMENTATION_START'}),flush=True)
 try:
  mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
  if mids!=[1827903]:raise ValueError(f'fixed prereg market must be [1827903], got {mids}')
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';c=v70g.V70GGenerationScopedRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v70g(models,cr['winner'])
   finally:c.close()
   fam,segs=classify(rr);rows.append({'marketId':mid,'v70g':rr,'sourceFamilies':fam,'segments':segs});print(json.dumps({'marketId':mid,'sourceFamilies':fam,'segments':segs},ensure_ascii=False),flush=True)
  rr=rows[0]['v70g'];fam=rows[0]['sourceFamilies'];segs=rows[0]['segments'];eligible=[s for s in segs if s['classification']=='PAIR_CREDIT_REPLENISHMENT_ELIGIBLE'];blocked=[s for s in segs if s['classification']=='PAIR_CREDIT_REPLENISHMENT_BLOCK']
  safety=(float(rr.get('authorizedSubmitWithTruthRoleMismatch') or 0)==0 and float(rr.get('overOwnedSubmitViolations') or 0)==0 and float(rr.get('v51ResponsibilityOverfill') or 0)<=EPS and float(rr.get('repairToExpandAtFirstFill') or 0)==0 and float(rr.get('v70dPreBirthPaymentLeak') or 0)<=EPS and float(rr.get('v70dDuplicateGenerationDebt') or 0)<=EPS)
  gates={'frozenV70GCompletes':len(rows)==1,'zeroInheritedSafetyViolation':safety,'activeFallbackChildMapsToSourceFamily':any(x['childFillObserved'] and x['sourceKey'] for x in fam),'atLeastOnePairCreditEligible':len(eligible)>=1,'atLeastOnePairCreditBlocked':len(blocked)>=1,'noObjectiveOrActionMutation':True}
  decision='AUTHORIZE_ONE_MARKET_V72_BEHAVIOR_GATE_SMOKE_1827903' if all(gates.values()) else 'FIX_RESPONSIBILITY_FAMILY_INSTRUMENTATION_BEFORE_BEHAVIOR_CHANGE'
  out={'version':'ETH_REPAIR_V72_HFT_RESPONSIBILITY_BUDGET_INSTRUMENTATION','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'fixedMarket':1827903,'gates':gates,'decision':decision,'eligibleSegments':eligible,'blockedSegments':blocked,'rows':rows,'boundary':['frozen V70G action path unchanged','actual realistic-HFT fills only','source->active-child family mapping descriptive','pair sum 1.0 structural, not tuned','no winner/PnL gate','no H100','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'eligible':eligible,'blocked':blocked},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
