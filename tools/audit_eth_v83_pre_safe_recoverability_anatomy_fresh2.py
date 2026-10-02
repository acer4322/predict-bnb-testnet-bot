from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,joblib,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sibling(name,path):
 p=Path(path);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('resp_transition_for_rec_anatomy',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
g=front.g;v38=front.v38;v80=front.v80;v1=g.v1
FIXED=[1916847,1916869]

class RecoverabilityAnatomy(front.ResponsibilityTransitionCandidate):
 def __init__(self,*a,**kw):
  self.recAnatomy=[];super().__init__(*a,**kw)
 def _ownership_if_needed(self,t,pE,qv):
  before=getattr(self,'thesis',None);side=self._signal_side(qv);rec=self._v75_recoverability(t,side,qv)
  ctx=v80.OwnershipContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,has_thesis=before is not None,p_expand=float(pE),signal_side=side,recoverable=bool(rec.get('recoverable')))
  dec=self.policyProfile.ownership.evaluate(ctx)
  row={'t':int(t),'pExpand':float(pE),'signalSide':side,'secondsLeft':ctx.seconds_left,'beforeThesis':None if before is None else dict(before),'recoverability':rec,'ownershipDecision':{'createThesis':bool(dec.create_thesis),'side':dec.side,'reason':dec.reason}}
  ret=super()._ownership_if_needed(t,pE,qv);after=getattr(self,'thesis',None);row['afterThesis']=None if after is None else dict(after);row['created']=before is None and after is not None;self.recAnatomy.append(row);return ret
 def run_anatomy(self,models,winner):
  r=self.run_transition(models,winner);r.update({'preSafeRecoverabilityAnatomy':self.recAnatomy[:80],'preSafeRecoverabilityAnatomyCount':len(self.recAnatomy)});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(mids)
 tmp=Path(tempfile.mkdtemp(prefix='v83_rec_anatomy_'));stop=threading.Event()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'V83_RECOVERABILITY_ANATOMY','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V83_RECOVERABILITY_ANATOMY_START','markets':mids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for i,mid in enumerate(mids,1):
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';s=RecoverabilityAnatomy(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   try:r=s.run_anatomy(models,cr['winner'])
   finally:s.close()
   ar=r.get('preSafeRecoverabilityAnatomy',[]);rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'fills':r.get('actualFillEvents'),'floor':r.get('floor'),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'v83Checks':r.get('v83AdmissionChecks'),'v83Allows':r.get('v83AdmissionAllows'),'v83Blocks':r.get('v83AdmissionBlocks'),'anatomy':ar});print(json.dumps({'idx':i,'marketId':mid,'anatomy':ar},ensure_ascii=False),flush=True)
  reasons=Counter();decisions=Counter()
  for r in rows:
   for x in r['anatomy']:
    reasons[str((x.get('recoverability') or {}).get('reason'))]+=1;decisions[str((x.get('ownershipDecision') or {}).get('reason'))]+=1
  out={'version':'ETH_V83_PRE_SAFE_RECOVERABILITY_ANATOMY_FRESH2','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'fixedMarkets':mids,'recoverabilityReasonCounts':dict(reasons),'ownershipDecisionCounts':dict(decisions),'rows':rows,'decision':'SINGLE_SHARED_RECOVERABILITY_SEAM' if len([k for k,v in reasons.items() if v])==1 else 'MULTIPLE_RECOVERABILITY_SEAMS','boundary':['instrumentation only','ResponsibilityTransition frozen','V75/V83 formulas frozen','winner post-hoc only','no tuning','no 8781','realistic HFT only']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'recoverabilityReasons':dict(reasons),'ownershipDecisions':dict(decisions)},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
