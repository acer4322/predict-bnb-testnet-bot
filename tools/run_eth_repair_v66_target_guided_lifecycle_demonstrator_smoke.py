from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v65=sib('eth_v65_for_v66','run_eth_repair_v65_global_expand_ownership_dedup_smoke.py')
v53=v65.v53;v38=v65.v38;v1=v65.v64.v1;EPS=1e-9

def lifecycle_tokens(events):
 out=[];repair_ready=False
 for e in sorted(events,key=lambda z:int(z['t'])):
  role=str(e.get('role') or '')
  if role in ('PASSIVE_REPAIR','ACTIVE_REPAIR'):
   repair_ready=True
  elif role in ('PASSIVE_EXPAND','ACTIVE_EXPAND') and repair_ready:
   out.append({'tokenId':len(out)+1,'targetT':int(e['t']),'targetRole':role,'targetSide':e.get('side'),'targetQty':float(e.get('qty') or 0.0),'targetPrice':float(e.get('price') or 0.0)})
   repair_ready=False
 return out

class V66TargetLifecycleDemonstrator(v65.V65GlobalExpandDedup):
 def __init__(self,*a,target_events=None,**kw):
  super().__init__(*a,**kw)
  self.v66Tokens=lifecycle_tokens(target_events or [])
  self.v66Consumed={}
  self.v66Decisions=[]
  self.v66Keys=set()
  self.v66KeyToken={}
  self.v66TeacherSubmits=0
 def _past_token(self,t):
  return next((z for z in self.v66Tokens if z['tokenId'] not in self.v66Consumed and int(z['targetT'])<int(t)),None)
 def _global_expand_occupied(self,t):
  self._refresh_carrier_ledger(t)
  occupied=[]
  for k,e in getattr(self,'carrierLedger',{}).items():
   if str(e.get('objectiveRole') or '')!='EXPAND':continue
   rem=max(0.0,float(e.get('submittedQty') or 0.0)-float(e.get('actualFilled') or 0.0))
   if rem>EPS and not bool(e.get('terminalConfirmed')):
    occupied.append({'key':k,'lane':e.get('lane'),'objectiveId':e.get('objectiveId'),'remaining':rem,'submittedAt':e.get('submittedAt')})
  return sorted(occupied,key=lambda z:int(z.get('submittedAt') or 0))
 def _experience_state(self,t):
  try:f=self._coord_feature(t)
  except Exception:f={}
  keep=['gross','debt','floor','absNet','repairProgressFrac','repairQty5','repairQty15','repairQty30','expandQty5','expandQty15','expandQty30']
  return {k:(None if f.get(k) is None else float(f[k])) for k in keep}
 def _score_state(self,t,after_kind):
  state=self._experience_state(t) if after_kind=='REPAIR' else {}
  n=len(self.v44Decisions)
  super()._score_state(t,after_kind)
  if after_kind!='REPAIR':return
  learned=self.v44Decisions[-1] if len(self.v44Decisions)>n else None
  tok=self._past_token(t)
  row={'oursT':int(t),'afterKind':after_kind,'strictPastState':state,'learnedDecision':None if learned is None else {'submit':bool(learned.get('submit')),'reason':learned.get('reason'),'pV44':learned.get('pV44'),'pV47':learned.get('pV47')},'teacherToken':tok}
  if tok is None:
   row['outcome']='NO_STRICT_PAST_TARGET_TOKEN';self.v66Decisions.append(row);return
  row['teacherLagMs']=int(t)-int(tok['targetT'])
  if learned and learned.get('submit'):
   self.v66Consumed[tok['tokenId']]={'oursT':int(t),'kind':'MODEL_AGREEMENT','key':learned.get('key')}
   row['outcome']='MODEL_AGREEMENT';row['key']=learned.get('key');self.v66Decisions.append(row);return
  if int(self.capEnd)-int(t)<=180000:
   row['outcome']='BLOCK_LATE';self.v66Decisions.append(row);return
  occupied=self._global_expand_occupied(t)
  if occupied:
   row['outcome']='BLOCK_GLOBAL_EXPAND_OCCUPIED';row['occupied']=occupied[-4:];self.v66Decisions.append(row);return
  if self.repairParent is None or float(getattr(self,'_coordDebt',0.0) or 0.0)<=EPS:
   row['outcome']='BLOCK_NO_OUR_REPAIR_RESPONSIBILITY';self.v66Decisions.append(row);return
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):
   row['outcome']='BLOCK_NO_OUR_THESIS';self.v66Decisions.append(row);return
  qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('bid') is None:
   row['outcome']='BLOCK_NO_QUOTES';self.v66Decisions.append(row);return
  px=float(qv[side]['bid']);qty=1.0/px if px>EPS else math.inf
  if px<=EPS or qty>12.0+EPS:
   row['outcome']='BLOCK_VENUE_MIN_INFEASIBLE';self.v66Decisions.append(row);return
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n
  self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V66_TARGET_GUIDED_OBJECTIVE'
  ok=self.submit(t,side,px,qty)
  if not ok:
   row['outcome']='SUBMIT_FAILED';self.v66Decisions.append(row);return
  key=f'{side}_{n0}';self.v44Keys.add(key);self.v44Submits+=1;self.v66Keys.add(key);self.v66KeyToken[key]=int(tok['tokenId']);self.v66TeacherSubmits+=1
  self.v66Consumed[tok['tokenId']]={'oursT':int(t),'kind':'TARGET_GUIDED_SUBMIT','key':key}
  row.update({'outcome':'TARGET_GUIDED_SUBMIT','key':key,'objectiveId':oid,'oursSide':side,'oursPrice':px,'oursQty':qty});self.v66Decisions.append(row)
 def run_exam_v66(self,models,winner):
  r=super().run_exam_v65(models,winner)
  ev=sorted(self.v53Fills,key=lambda x:(int(x['t']),x['key']))
  token_events={}
  for i,e in enumerate(ev):
   tid=self.v66KeyToken.get(e['key'])
   if tid is None:
    a=self.v64Active.get(e['key'])
    if a:tid=self.v66KeyToken.get(a.get('sourceKey'))
   if tid is not None and e['role'] in ('PASSIVE_EXPAND','ACTIVE_EXPAND'):
    token_events.setdefault(int(tid),[]).append(i)
  materialized=sorted(token_events)
  repaired=0
  repaired_tokens=[]
  for tid,idxs in token_events.items():
   i=max(idxs);hi=next((j for j in range(i+1,len(ev)) if ev[j]['role'] in ('PASSIVE_EXPAND','ACTIVE_EXPAND')),len(ev))
   if any(ev[j]['role'] in ('PASSIVE_REPAIR','ACTIVE_REPAIR') for j in range(i+1,hi)):
    repaired+=1;repaired_tokens.append(tid)
  teacher_keys=set(self.v66Keys)
  for k,a in self.v64Active.items():
   if a.get('sourceKey') in self.v66Keys:teacher_keys.add(k)
  fillqty=sum(float(e['qty']) for e in ev if e['key'] in teacher_keys and e['role'] in ('PASSIVE_EXPAND','ACTIVE_EXPAND'))
  strict_viol=sum(1 for tid,z in self.v66Consumed.items() if int(next(x['targetT'] for x in self.v66Tokens if x['tokenId']==tid))>=int(z['oursT']))
  r.update({'v66TargetTokens':len(self.v66Tokens),'v66TargetTokensPre180':sum(int(x['targetT'])<int(self.capEnd)-180000 for x in self.v66Tokens),'v66ConsumedTokens':len(self.v66Consumed),'v66ModelAgreements':sum(z['kind']=='MODEL_AGREEMENT' for z in self.v66Consumed.values()),'v66TeacherSubmits':self.v66TeacherSubmits,'v66TeacherFillQty':fillqty,'v66TeacherMaterializedTokens':len(materialized),'v66TeacherGeneratedRepaired':repaired,'v66TeacherRepairedTokenIds':repaired_tokens,'v66StrictPastViolations':strict_viol,'v66UnconsumedTokenIds':[x['tokenId'] for x in self.v66Tokens if x['tokenId'] not in self.v66Consumed],'v66ExperienceRows':self.v66Decisions[:240]})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','target-teacher-json','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v66_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V66','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V66_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};target=json.load(open(a.target_teacher_json,encoding='utf-8'))['markets']
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';te=target.get(str(mid),[])
   b=v65.V65GlobalExpandDedup(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v65(models,cr['winner'])
   finally:b.close()
   c=V66TargetLifecycleDemonstrator(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,target_events=te)
   try:rr=c.run_exam_v66(models,cr['winner'])
   finally:c.close()
   ts=v53.V53MultiCycleAudit._cycle_stats(te)
   rows.append({'marketId':mid,'winner':cr['winner'],'targetEvents':te,'targetRounds':ts,'baseline':br,'candidate':rr})
   print(json.dumps({'marketId':mid,'targetTokens':rr['v66TargetTokens'],'consumed':rr['v66ConsumedTokens'],'teacherSubmits':rr['v66TeacherSubmits'],'teacherFillQty':rr['v66TeacherFillQty'],'teacherRepaired':rr['v66TeacherGeneratedRepaired'],'rounds':[br['v64Rounds'],rr['v64Rounds']],'safety':[rr['authorizedSubmitWithTruthRoleMismatch'],rr['overOwnedSubmitViolations'],rr['repairToExpandAtFirstFill'],rr['v51ResponsibilityOverfill']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.0) for x in rows)
  roles={};target_roles={}
  for x in rows:
   for k,v in x['candidate'].get('v64RoleCounts',{}).items():roles[k]=roles.get(k,0)+int(v)
   for e in x['targetEvents']:target_roles[e['role']]=target_roles.get(e['role'],0)+1
  agg={'markets':len(rows),'targetTokens':int(sm('candidate','v66TargetTokens')),'targetTokensPre180':int(sm('candidate','v66TargetTokensPre180')),'consumedTokens':int(sm('candidate','v66ConsumedTokens')),'modelAgreements':int(sm('candidate','v66ModelAgreements')),'teacherSubmits':int(sm('candidate','v66TeacherSubmits')),'teacherFillQty':sm('candidate','v66TeacherFillQty'),'teacherMaterializedTokens':int(sm('candidate','v66TeacherMaterializedTokens')),'teacherGeneratedRepaired':int(sm('candidate','v66TeacherGeneratedRepaired')),'baselineRounds':int(sm('baseline','v64Rounds')),'candidateRounds':int(sm('candidate','v64Rounds')),'roundGain':int(sm('candidate','v64Rounds')-sm('baseline','v64Rounds')),'strictPastViolations':int(sm('candidate','v66StrictPastViolations')),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill'),'generationOverpay':sm('candidate','v48GenerationOverpay'),'staleEvidenceLeak':sm('candidate','v52StaleEvidenceLeak'),'ourRoleCounts':roles,'targetRoleCounts':target_roles,'baselinePnlDiagnostic':sm('baseline','pnlDiagnosticOnly'),'candidatePnlDiagnostic':sm('candidate','pnlDiagnosticOnly'),'baselineFloor':sm('baseline','floor'),'candidateFloor':sm('candidate','floor'),'baselineAbsNet':sm('baseline','absNet'),'candidateAbsNet':sm('candidate','absNet')}
  gates={'targetGuidanceActuallySubmitted':agg['teacherSubmits']>0,'targetGuidanceActuallyFilled':agg['teacherFillQty']>EPS,'guidedExpandGetsRepair':agg['teacherGeneratedRepaired']>0,'roundsNonDecreasing':agg['roundGain']>=0,'strictPastOnly':agg['strictPastViolations']==0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroGenerationOverpay':agg['generationOverpay']<=EPS,'zeroStaleEvidenceLeak':agg['staleEvidenceLeak']==0}
  out={'version':'ETH_REPAIR_V66_TARGET_GUIDED_LIFECYCLE_DEMONSTRATOR_SMOKE','researchOnly':True,'behaviorChange':True,'privilegedTeacher':True,'actionAuthority':'TRAINING_DEMONSTRATOR_ONLY','hypothesis':'If Target lifecycle experience is the main missing element, strict-past Target Repair-to-Expand responsibility tokens should materialize through OUR existing V65 ownership/execution/repair machinery and increase rounds without accounting violations.','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['Target supplies only compressed Repair-to-Expand responsibility tokens from same-market strict-past history.','OUR state must independently reach a legitimate actual Repair-fill decision event.','OUR chooses thesis side, venue-minimum quantity, current bid, passive carrier, V65 fallback, ownership and Repair payment.','One Target lifecycle token can be consumed at most once.','Target side/price/qty/winner/PnL never drive submission.','Privileged demonstration generator only; no runtime promotion.','1-3 market smoke only; no threshold/qty/delay tuning.','realistic HFT/no dream fill/no 8781/<=180s no new exposure.']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
