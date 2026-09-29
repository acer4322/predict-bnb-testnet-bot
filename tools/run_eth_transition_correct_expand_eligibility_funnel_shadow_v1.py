from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,statistics
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import run_eth_repair_modular_allocation_v2_generic_hft as g

def sibling(name,path):
 p=Path(path);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('resp_transition_for_funnel_v1',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
EPS=1e-9;v38=g.v38;v80=g.v80;v1=g.v1
FIXED=[1916830,1916845,1916847,1916869]

class FunnelShadow(front.ResponsibilityTransitionCandidate):
 def __init__(self,*a,**kw):
  self.funnelRows=[]
  super().__init__(*a,**kw)
 def _repair_snapshot(self,t):
  rp=getattr(self,'repairParent',None);pid=int(rp.get('id')) if isinstance(rp,dict) and rp.get('id') is not None else None;side=rp.get('side') if isinstance(rp,dict) else None
  rows=[]
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
    rows.append({'key':key,'side':e.get('side'),'filledQty':fill,'price':float(px),'lane':e.get('lane')})
  q=sum(x['filledQty'] for x in rows);p=(sum(x['filledQty']*x['price'] for x in rows)/q) if q>EPS else None
  return {'parentId':pid,'parentSide':side,'filledQty':q,'weightedRepairPrice':p,'carriers':rows}
 def _score_state(self,t,after_kind):
  # Preserve the frozen ResponsibilityTransition logic exactly, while logging one terminal reason per call.
  self._refresh_carrier_ledger(int(t))
  debt,debt_rows=self._live_repair_debt_by_side();th=getattr(self,'thesis',None);th_side=th.get('side') if isinstance(th,dict) else None
  d=self.transitionPolicy.evaluate(front.trans.ResponsibilityTransitionContext(th_side,debt['UP'],debt['DOWN']))
  self.transitionChecks+=1
  tev={'t':int(t),'event':'RESPONSIBILITY_TRANSITION_CHECK','afterKind':after_kind,'thesisSide':th_side,'repairDebtBySide':debt,'debtRows':debt_rows,'allowExpandOwnership':d.allow_expand_ownership,'bindRole':d.bind_role,'reason':d.reason,'liveRepairDebt':d.live_repair_debt}
  base={'t':int(t),'afterKind':after_kind,'transitionReason':d.reason,'transitionAllowExpand':bool(d.allow_expand_ownership),'transitionBindRole':d.bind_role,'liveRepairDebt':float(d.live_repair_debt or 0.0),'thesisSideBefore':th_side}
  if after_kind!='REPAIR':
   base['terminalReason']='SCORE_SEAM_NOT_REPAIR';self.funnelRows.append(base);self.transitionEvents.append(tev);return super(front.ResponsibilityTransitionCandidate,self)._score_state(t,after_kind)
  if not d.allow_expand_ownership and d.bind_role=='REPAIR':
   self.transitionBlocks+=1;tev['event']='EXPAND_OWNERSHIP_SUSPENDED_REPAIR_FIRST';self.transitionEvents.append(tev);base['terminalReason']='RESPONSIBILITY_TRANSITION_REPAIR_FIRST';self.funnelRows.append(base);return None
  self.transitionEvents.append(tev)

  snap=self._repair_snapshot(t);base['repairSnapshot']=snap
  checks0=int(getattr(self,'v83AdmissionChecks',0));adm0=len(getattr(self,'v83Admissions',[]));sub0=int(getattr(self,'v83AdmissionAllows',0))
  ret=super(front.ResponsibilityTransitionCandidate,self)._score_state(t,after_kind)
  checks1=int(getattr(self,'v83AdmissionChecks',0));new=list(getattr(self,'v83Admissions',[]))[adm0:];sub1=int(getattr(self,'v83AdmissionAllows',0))
  qv=v1.quotes(self.book);th2=getattr(self,'thesis',None);side2=th2.get('side') if isinstance(th2,dict) else None
  base.update({'v83ChecksDelta':checks1-checks0,'thesisSideAfter':side2,'coordDebtAfter':float(getattr(self,'_coordDebt',0.0) or 0.0),'repairParentPresentAfter':isinstance(getattr(self,'repairParent',None),dict),'quotesAvailableAfter':bool(qv)})
  if new:
   a=dict(new[-1]);base['admissionRow']=a;base['pExpand']=a.get('pExpand');base['terminalReason']=str(a.get('reason') or 'V83_UNKNOWN_TERMINAL')
  elif sub1>sub0:
   base['terminalReason']='V83_ECONOMIC_ADMISSION'
  else:
   base['terminalReason']='V83_PRECHECK_NOT_REACHED'
  # Deterministic nominal pair diagnostic only. No authority and no fill assumption.
  rp=snap.get('weightedRepairPrice');bid=None
  if qv and side2 in ('UP','DOWN'):
   try:bid=float(qv[side2]['bid'])
   except Exception:bid=None
  base['hypotheticalExpandBid']=bid;base['nominalPairSum']=None if rp is None or bid is None else float(rp)+float(bid)
  self.funnelRows.append(base);return ret
 def run_funnel(self,models,winner):
  r=self.run_allocation_v2(models,winner);r.update({'eligibilityFunnelRows':self.funnelRows[:500],'eligibilityFunnelCount':len(self.funnelRows)});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);ap.add_argument('--allow-subset',action='store_true');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if a.allow_subset:
  if not mids or any(x not in FIXED for x in mids): raise ValueError(f'subset must come from fixed funnel cohort {mids}')
 elif mids!=FIXED: raise ValueError(f'fixed funnel cohort mismatch {mids}')
 tmp=Path(tempfile.mkdtemp(prefix='expand_funnel_'));stop=threading.Event()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'EXPAND_FUNNEL_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'EXPAND_FUNNEL_SHADOW_START','markets':mids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[];allf=[]
  for i,mid in enumerate(mids,1):
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';s=FunnelShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
   try:r=s.run_funnel(models,cr['winner'])
   finally:s.close()
   fr=r.get('eligibilityFunnelRows',[]);cnt=Counter(x.get('terminalReason') for x in fr);pairs=[float(x['nominalPairSum']) for x in fr if x.get('nominalPairSum') is not None]
   row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'fills':int(r.get('actualFillEvents') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'transitionBlocks':int(r.get('transitionBlocks') or 0),'v83AdmissionChecks':int(r.get('v83AdmissionChecks') or 0),'v83AdmissionAllows':int(r.get('v83AdmissionAllows') or 0),'v83AdmissionBlocks':int(r.get('v83AdmissionBlocks') or 0),'terminalReasonCounts':dict(cnt),'nominalPairSums':pairs,'funnelRows':fr};rows.append(row);allf.extend(fr);print(json.dumps({'idx':i,'marketId':mid,'fills':row['fills'],'checks':row['v83AdmissionChecks'],'allows':row['v83AdmissionAllows'],'blocks':row['v83AdmissionBlocks'],'reasons':dict(cnt)},ensure_ascii=False),flush=True)
  total=Counter(x.get('terminalReason') for x in allf);market_cov=defaultdict(set)
  for r in rows:
   for k,n in r['terminalReasonCounts'].items():
    if n:market_cov[k].add(r['marketId'])
  pairvals=[float(x['nominalPairSum']) for x in allf if x.get('nominalPairSum') is not None];below=sum(v<=1.0+EPS for v in pairvals)
  pvals=[float(x['pExpand']) for x in allf if x.get('pExpand') is not None]
  binding=total.get('OPPORTUNITY_BELOW_FROZEN_V44_THRESHOLD',0)>0
  out={'version':'ETH_TRANSITION_CORRECT_EXPAND_ELIGIBILITY_FUNNEL_SHADOW_V1','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'fixedMarkets':mids,'aggregate':{'markets':len(rows),'fills':sum(r['fills'] for r in rows),'v83AdmissionChecks':sum(r['v83AdmissionChecks'] for r in rows),'v83AdmissionAllows':sum(r['v83AdmissionAllows'] for r in rows),'v83AdmissionBlocks':sum(r['v83AdmissionBlocks'] for r in rows),'terminalReasonCounts':dict(total),'terminalReasonMarketCoverage':{k:len(v) for k,v in market_cov.items()},'pExpandN':len(pvals),'pExpandMin':min(pvals) if pvals else None,'pExpandMedian':statistics.median(pvals) if pvals else None,'pExpandMax':max(pvals) if pvals else None,'nominalPairN':len(pairvals),'nominalPairMedian':statistics.median(pairvals) if pairvals else None,'nominalPairNonDamagingShare':below/len(pairvals) if pairvals else None},'thresholdBindingObserved':binding,'recommendedNextStep':'RUN_THRESHOLD_REACHABILITY_SMOKE_ONLY' if binding else 'DO_NOT_TUNE_THRESHOLD_YET_DIAGNOSE_UPSTREAM_OR_OTHER_GATE','rows':rows,'boundary':['instrumentation only','ResponsibilityTransition frozen','V83 threshold fixed 0.50','all execution/economic policy frozen','winner post-hoc only','no 8781','realistic HFT only']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate'],'thresholdBindingObserved':binding,'recommendedNextStep':out['recommendedNextStep']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
