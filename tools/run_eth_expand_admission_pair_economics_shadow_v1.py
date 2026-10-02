from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,statistics
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import run_eth_repair_modular_allocation_v2_generic_hft as g

def sibling(name,path):
 p=Path(path);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('resp_transition_for_expand_pair_shadow',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
EPS=1e-9;v38=g.v38;v80=g.v80
FIXED=[1916830,1916845,1916847,1916869]

class ExpandPairEconomicsShadow(front.ResponsibilityTransitionCandidate):
 def __init__(self,*a,**kw):
  self.expandPairShadow=[]
  super().__init__(*a,**kw)
 def _repair_fill_snapshot(self,t):
  self._refresh_carrier_ledger(int(t))
  rp=getattr(self,'repairParent',None);pid=int(rp.get('id')) if isinstance(rp,dict) and rp.get('id') is not None else None
  side=rp.get('side') if isinstance(rp,dict) else None;rows=[]
  if pid is not None:
   for key,e in getattr(self,'carrierLedger',{}).items():
    if str(e.get('objectiveRole') or '')!='REPAIR':continue
    try:
     if int(e.get('parentId'))!=pid:continue
    except Exception:continue
    fill=float(e.get('actualFilled') or 0.0)
    if fill<=EPS:continue
    o=getattr(self,'orders',{}).get(key,{})
    px=o.get('price')
    if px is None:continue
    rows.append({'key':key,'side':e.get('side'),'filledQty':fill,'price':float(px),'submittedAt':e.get('submittedAt'),'lane':e.get('lane')})
  q=sum(x['filledQty'] for x in rows);p=(sum(x['filledQty']*x['price'] for x in rows)/q) if q>EPS else None
  return {'parentId':pid,'parentSide':side,'filledQty':q,'weightedRepairPrice':p,'carriers':rows}
 def _score_state(self,t,after_kind):
  snap=self._repair_fill_snapshot(t);before=len(getattr(self,'v83Admissions',[]))
  ret=super()._score_state(t,after_kind)
  new=list(getattr(self,'v83Admissions',[]))[before:]
  for a in new:
   if not bool(a.get('submit')):continue
   ep=a.get('price');es=a.get('side');rp=snap.get('weightedRepairPrice');ps=snap.get('parentSide')
   pair=(float(rp)+float(ep)) if rp is not None and ep is not None else None
   expected_repair='DOWN' if es=='UP' else 'UP' if es=='DOWN' else None
   row={'t':int(t),'afterKind':after_kind,'admissionReason':a.get('reason'),'expandSide':es,'expandPrice':ep,'expandQty':a.get('qty'),'repairParentId':snap.get('parentId'),'repairParentSide':ps,'repairRealizedQty':snap.get('filledQty'),'repairWeightedPrice':rp,'pairSum':pair,'nonDamagingPair':None if pair is None else bool(pair<=1.0+EPS),'repairParentSideMatchesPair':None if expected_repair is None or ps is None else bool(ps==expected_repair),'repairCarriers':snap.get('carriers',[]),'pExpand':a.get('pExpand')}
   self.expandPairShadow.append(row)
  return ret
 def run_pair_shadow(self,models,winner):
  r=self.run_transition(models,winner);r.update({'expandPairEconomicsShadow':self.expandPairShadow[:240],'expandPairEconomicAdmissions':len(self.expandPairShadow)});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if any(x not in FIXED for x in mids) or not mids:raise ValueError(f'allowed fixed smoke subset {FIXED}; got {mids}')
 tmp=Path(tempfile.mkdtemp(prefix='expand_pair_shadow_'));stop=threading.Event()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'EXPAND_PAIR_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'EXPAND_PAIR_SHADOW_START','markets':mids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for i,mid in enumerate(mids,1):
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';s=ExpandPairEconomicsShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   try:r=s.run_pair_shadow(models,cr['winner'])
   finally:s.close()
   sh=r.get('expandPairEconomicsShadow',[]);sc=[x for x in sh if x.get('pairSum') is not None];nd=[x for x in sc if x.get('nonDamagingPair')];mis=[x for x in sc if x.get('repairParentSideMatchesPair') is False]
   row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'floor':r.get('floor'),'fills':r.get('actualFillEvents'),'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'transitionBlocks':r.get('transitionBlocks'),'economicAdmissionSubmits':len(sh),'pairScoredAdmissions':len(sc),'nonDamagingPairAdmissions':len(nd),'repairParentSideMismatchCount':len(mis),'pairRows':sh};rows.append(row)
   print(json.dumps({'idx':i,'of':len(mids),'marketId':mid,'admissions':len(sh),'scored':len(sc),'nonDamaging':len(nd),'pairSums':[x.get('pairSum') for x in sc],'truthMismatch':row['truthMismatch']},ensure_ascii=False),flush=True)
  allr=[x for r in rows for x in r['pairRows']];sc=[x for x in allr if x.get('pairSum') is not None];pairs=[float(x['pairSum']) for x in sc];nd=sum(bool(x.get('nonDamagingPair')) for x in sc);mismatch=sum(x.get('repairParentSideMatchesPair') is False for x in sc)
  med=statistics.median(pairs) if pairs else None;share=nd/len(sc) if sc else None
  exercised=len(sc)>=3;axis=bool(exercised and ((share is not None and share<.50) or (med is not None and med>1.0)))
  out={'version':'ETH_EXPAND_ADMISSION_PAIR_ECONOMICS_SHADOW_V1','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'fixedMarkets':mids,'aggregate':{'markets':len(rows),'economicAdmissionSubmits':len(allr),'pairScoredAdmissions':len(sc),'nonDamagingPairAdmissions':nd,'nonDamagingPairShare':share,'medianPairSum':med,'repairParentSideMismatchCount':mismatch,'aggregatePnlDiagnostic':sum(float(r['pnlDiagnosticOnly'] or 0) for r in rows)},'decision':'KEEP_ADMISSION_CLOCK_PAIR_ECONOMICS_AS_NEXT_QUALITY_AXIS' if axis else ('INSUFFICIENT_SMOKE_SUPPORT' if not exercised else 'DO_NOT_ADD_PAIR_ECONOMICS_GATE_YET'),'rows':rows,'stableNextCompositeShadow':{'status':'KEEP_UNCHANGED','actionAuthority':False},'boundary':['ResponsibilityTransition deterministic Repair-first retained','instrumentation only relative to transition-correct baseline','repair price uses same-clock strict-past realized current-parent Repair fills','frozen V83 candidate Expand price','pair sum 1.0 structural boundary','no Target runtime input','winner/PnL post-hoc only','no admission mutation','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
