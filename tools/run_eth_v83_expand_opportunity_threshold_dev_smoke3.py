from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,joblib,math,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sibling(name,path):
 p=Path(path);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('transition_for_threshold_dev',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
EPS=1e-9;v38=front.v38;v80=front.v80;v1=front.g.v1
FIXED=[1916924,1916954,1917062];THRESHOLDS=[0.45,0.50,0.55]

class ThresholdCandidate(front.ResponsibilityTransitionCandidate):
 def __init__(self,*a,opportunity_threshold=.5,**kw):
  self.opportunityThreshold=float(opportunity_threshold);super().__init__(*a,**kw)
 def _v83_threshold_score(self,t,after_kind):
  self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
  if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return None
  f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);qv=v1.quotes(self.book)
  if not qv:return None
  self._ownership_if_needed(t,pE,qv);row={'t':int(t),'parentId':int(self.repairParent.get('id')),'pExpand':pE,'submit':False,'reason':'MODEL_REPAIR','opportunityThreshold':self.opportunityThreshold};self.v83AdmissionChecks+=1
  if pE<self.opportunityThreshold:
   row['reason']='OPPORTUNITY_BELOW_DEV_THRESHOLD';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
  if int(self.capEnd)-int(t)<=180000:
   row['reason']='LATE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
  self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
  if self.v70gGenerationAuthorized:
   self.v70gDuplicateObjectiveBlocks+=1;row['reason']='GENERATION_ALREADY_OWNS_EXPAND';row['carrier']=self.v70gGenerationCarrier;self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
  if self._expand_occupied():
   row['reason']='GLOBAL_EXPAND_OCCUPIED';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):
   row['reason']='NO_RECOVERABLE_THESIS';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
  rec=self._v75_recoverability(t,side,qv);row.update({'side':side,'recoverability':rec})
  if not bool(rec.get('recoverable')):
   row['reason']='WHOLE_PORTFOLIO_UNRECOVERABLE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
  px=float(qv[side]['bid']);qty=1.0/px if px>EPS else math.inf
  if not math.isfinite(qty) or qty<=EPS or qty>12.+EPS:
   row['reason']='VENUE_MIN_INFEASIBLE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n;self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V83_ECONOMIC_PARALLEL_EXPAND';ok=self.submit(t,side,px,qty)
  if not ok:
   row['reason']='SUBMIT_FAILED';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);return None
  key=f'{side}_{n0}';self.v44Keys.add(key);self.v44Submits+=1;row.update({'submit':True,'reason':'V83_ECONOMIC_ADMISSION_DEV_THRESHOLD','key':key,'objectiveId':oid,'price':px,'qty':qty});self.v44Decisions.append(dict(row));self.v83AdmissionAllows+=1;self.v83Admissions.append(dict(row));self._refresh_carrier_ledger_no_v70d()
  if key in self.carrierLedger:self.v70gInheritedExpandBinds+=1;self._bind_existing_expand(t,key,'V83_ECONOMIC_ADMISSION_DEV_THRESHOLD')
  return True
 def _score_state(self,t,after_kind):
  self._refresh_carrier_ledger(int(t));debt,rows=self._live_repair_debt_by_side();th=getattr(self,'thesis',None);side=th.get('side') if isinstance(th,dict) else None;d=self.transitionPolicy.evaluate(front.trans.ResponsibilityTransitionContext(side,debt['UP'],debt['DOWN']));self.transitionChecks+=1;ev={'t':int(t),'event':'RESPONSIBILITY_TRANSITION_CHECK','afterKind':after_kind,'thesisSide':side,'repairDebtBySide':debt,'debtRows':rows,'allowExpandOwnership':d.allow_expand_ownership,'bindRole':d.bind_role,'reason':d.reason,'liveRepairDebt':d.live_repair_debt}
  if after_kind=='REPAIR' and not d.allow_expand_ownership and d.bind_role=='REPAIR':self.transitionBlocks+=1;ev['event']='EXPAND_OWNERSHIP_SUSPENDED_REPAIR_FIRST';self.transitionEvents.append(ev);return None
  self.transitionEvents.append(ev);return self._v83_threshold_score(t,after_kind)

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True);ap.add_argument('--thresholds',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];ths=[float(x) for x in a.thresholds.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(f'market mismatch {mids}');
 if ths!=THRESHOLDS:raise ValueError(f'threshold mismatch {ths}')
 tmp=Path(tempfile.mkdtemp(prefix='v83_threshold_dev_'));stop=threading.Event()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'V83_THRESHOLD_DEV','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V83_THRESHOLD_DEV_START','markets':mids,'thresholds':ths}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];results={}
  for thv in ths:
   rows=[]
   for mid in mids:
    cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';c=ThresholdCandidate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile(),opportunity_threshold=thv)
    try:r=c.run_transition(models,cr['winner'])
    finally:c.close()
    ss=front.safety(r);cons=abs(float(r.get('v84CompositeFillQty') or 0)-float(r.get('v84RepairAllocatedQty') or 0)-float(r.get('v84OverflowAllocatedQty') or 0))<=1e-7
    row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'threshold':thv,'admissionChecks':int(r.get('v83AdmissionChecks') or 0),'admissionAllows':int(r.get('v83AdmissionAllows') or 0),'admissionBlocks':int(r.get('v83AdmissionBlocks') or 0),'fills':int(r.get('actualFillEvents') or 0),'rounds':r.get('v70dSemanticRounds'),'floor':r.get('floor'),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'transitionBlocks':r.get('transitionBlocks'),'safety':ss,'allocationConservation':cons};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
   safety_keys=list(rows[0]['safety']);sums={k:sum(float(r['safety'][k]) for r in rows) for k in safety_keys};agg={'threshold':thv,'markets':len(rows),'admissionChecks':sum(r['admissionChecks'] for r in rows),'admissionAllows':sum(r['admissionAllows'] for r in rows),'admissionBlocks':sum(r['admissionBlocks'] for r in rows),'fills':sum(r['fills'] for r in rows),'rounds':sum(float(r['rounds'] or 0) for r in rows),'positivePnlMarkets':sum(float(r['pnlDiagnosticOnly'] or 0)>EPS for r in rows),'aggregatePnlDiagnostic':sum(float(r['pnlDiagnosticOnly'] or 0) for r in rows),'aggregateFloor':sum(float(r['floor'] or 0) for r in rows),'safety':sums,'allocationConservationAll':all(r['allocationConservation'] for r in rows)};results[str(thv)]= {'aggregate':agg,'rows':rows}
  base=results['0.5']['aggregate'];cands=[]
  for k,v in results.items():
   if k=='0.5':continue
   a1=v['aggregate'];safe=a1['allocationConservationAll'] and all(abs(x)<=EPS for x in a1['safety'].values());support=a1['admissionAllows']>base['admissionAllows'];floor_ok=all(float(r['floor'] or 0)+1e-6>=min(float(b['floor'] or 0),-5.0) for r,b in zip(v['rows'],results['0.5']['rows']))
   if safe and support and floor_ok:cands.append(float(k))
  out={'version':'ETH_V83_EXPAND_OPPORTUNITY_THRESHOLD_DEV_SMOKE3','date':'2026-09-04','researchOnly':True,'promotionEvidence':False,'fixedMarkets':mids,'thresholds':ths,'results':results,'developmentCandidatesForStageAOnly':cands,'decision':'PARAMETER_HAS_ACTION_SUPPORT' if cands else 'NO_SAFE_ACTION_SUPPORT_GAIN_IN_SMOKE3','boundary':['ResponsibilityTransition frozen semantics','AllocationLedger V2 frozen','V83 recoverability/qty/price frozen','only pExpand opportunity threshold varies','winner post-hoc only','no promotion claim','no 8781','realistic HFT only']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'candidates':cands,'aggregates':{k:v['aggregate'] for k,v in results.items()}},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
