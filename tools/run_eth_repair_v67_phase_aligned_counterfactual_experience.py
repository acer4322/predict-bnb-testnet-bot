from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v65=sib('eth_v65_for_v67','run_eth_repair_v65_global_expand_ownership_dedup_smoke.py')
v53=v65.v53;v38=v65.v38;v1=v65.v64.v1;EPS=1e-9

def scalar_state(d):
 out={}
 for k,v in d.items():
  if v is None or isinstance(v,(str,bool,int,float)):out[k]=v
 return out

class V67CounterfactualBranch(v65.V65GlobalExpandDedup):
 def __init__(self,*a,force_ordinal=None,**kw):
  super().__init__(*a,**kw);self.v67ForceOrdinal=force_ordinal;self.v67Ordinal=0;self.v67Opportunities=[];self.v67ForceKey=None;self.v67ForceSubmitted=False;self.v67ForceEvents=[]
 def _global_expand_occupancy(self,t):
  self._refresh_carrier_ledger(t);out=[]
  for k,e in getattr(self,'carrierLedger',{}).items():
   if str(e.get('objectiveRole') or '')!='EXPAND':continue
   rem=max(0.0,float(e.get('submittedQty') or 0.0)-float(e.get('actualFilled') or 0.0))
   if rem>EPS and not bool(e.get('terminalConfirmed')):
    out.append({'key':k,'objectiveId':e.get('objectiveId'),'lane':e.get('lane'),'remaining':rem,'submittedAt':e.get('submittedAt')})
  return sorted(out,key=lambda z:int(z.get('submittedAt') or 0))
 def _force_gate(self,t):
  if int(self.capEnd)-int(t)<=180000:return False,'LATE',{}
  if self.repairParent is None or float(getattr(self,'_coordDebt',0.0) or 0.0)<=EPS:return False,'NO_REPAIR_RESPONSIBILITY',{}
  occupied=self._global_expand_occupancy(t)
  if occupied:return False,'GLOBAL_EXPAND_OCCUPIED',{'occupied':occupied[-4:]}
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):return False,'NO_THESIS',{}
  qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('bid') is None:return False,'NO_QUOTES',{}
  px=float(qv[side]['bid']);qty=1.0/px if px>EPS else math.inf
  if px<=EPS or qty>12.0+EPS:return False,'VENUE_MIN_INFEASIBLE',{'side':side,'price':px,'qty':qty}
  return True,'ELIGIBLE',{'side':side,'price':px,'qty':qty}
 def _submit_force(self,t,info,ordinal):
  side=info['side'];px=float(info['price']);qty=float(info['qty']);oid=self._new_objective('EXPAND',side)['id'];n0=self.n
  self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V67_COUNTERFACTUAL_EXPAND'
  ok=self.submit(t,side,px,qty)
  if not ok:self.v67ForceEvents.append({'t':int(t),'event':'COUNTERFACTUAL_SUBMIT_FAILED','ordinal':ordinal});return False
  key=f'{side}_{n0}';self.v44Keys.add(key);self.v44Submits+=1;self.v67ForceKey=key;self.v67ForceSubmitted=True
  self.v67ForceEvents.append({'t':int(t),'event':'COUNTERFACTUAL_EXPAND_SUBMIT','ordinal':ordinal,'key':key,'objectiveId':oid,'side':side,'price':px,'qty':qty});return True
 def _score_state(self,t,after_kind):
  if after_kind!='REPAIR':return super()._score_state(t,after_kind)
  try:state=scalar_state(self._coord_feature(t))
  except Exception:state={}
  n=len(self.v44Decisions);super()._score_state(t,after_kind);self.v67Ordinal+=1
  learned=self.v44Decisions[-1] if len(self.v44Decisions)>n else None
  learned_submit=bool(learned and learned.get('submit'));learned_reason=None if learned is None else learned.get('reason')
  eligible,gate,info=self._force_gate(t) if not learned_submit else (False,'MODEL_ALREADY_EXPANDED',{})
  counterfactual_eligible=(not learned_submit and learned_reason in ('MODEL_REPAIR','GENERATION_VETO') and eligible)
  row={'ordinal':self.v67Ordinal,'t':int(t),'state':state,'repairParentId':None if self.repairParent is None else self.repairParent.get('id'),'learnedSubmit':learned_submit,'learnedReason':learned_reason,'pV44':None if learned is None else learned.get('pV44'),'pV47':None if learned is None else learned.get('pV47'),'counterfactualEligible':counterfactual_eligible,'gate':gate,**info}
  if self.v67ForceOrdinal==self.v67Ordinal:
   row['forceSelected']=True
   if counterfactual_eligible:row['forceSubmitted']=self._submit_force(t,info,self.v67Ordinal)
   else:row['forceSubmitted']=False;self.v67ForceEvents.append({'t':int(t),'event':'COUNTERFACTUAL_BLOCKED','ordinal':self.v67Ordinal,'gate':gate,'learnedReason':learned_reason})
  self.v67Opportunities.append(row)
 def run_exam_v67(self,models,winner):
  r=super().run_exam_v65(models,winner);ev=sorted(self.v53Fills,key=lambda x:(int(x['t']),x['key']));keys=set()
  if self.v67ForceKey:keys.add(self.v67ForceKey)
  for k,a in self.v64Active.items():
   if a.get('sourceKey')==self.v67ForceKey:keys.add(k)
  idx=[i for i,e in enumerate(ev) if e['key'] in keys and e['role'] in ('PASSIVE_EXPAND','ACTIVE_EXPAND')]
  fillqty=sum(float(ev[i]['qty']) for i in idx);repaired=0
  if idx:
   i=max(idx);hi=next((j for j in range(i+1,len(ev)) if ev[j]['role'] in ('PASSIVE_EXPAND','ACTIVE_EXPAND')),len(ev))
   repaired=int(any(ev[j]['role'] in ('PASSIVE_REPAIR','ACTIVE_REPAIR') for j in range(i+1,hi)))
  r.update({'v67ForceOrdinal':self.v67ForceOrdinal,'v67Opportunities':self.v67Opportunities[:120],'v67ForceSubmitted':self.v67ForceSubmitted,'v67ForceKey':self.v67ForceKey,'v67ForceEvents':self.v67ForceEvents[:80],'v67ForceFillQty':fillqty,'v67ForceGeneratedRepaired':repaired})
  return r

def target_stats(events,cutoff=None):
 e=[x for x in events if cutoff is None or int(x['t'])<=int(cutoff)]
 cs=v53.V53MultiCycleAudit._cycle_stats(e);counts={}
 for x in e:counts[x['role']]=counts.get(x['role'],0)+1
 return {'events':len(e),'roleCounts':counts,**cs}

def safety(r):
 return {'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0.0),'overOwned':float(r.get('overOwnedSubmitViolations') or 0.0),'repairDrift':float(r.get('repairToExpandAtFirstFill') or 0.0),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0.0),'generationOverpay':float(r.get('v48GenerationOverpay') or 0.0),'staleEvidenceLeak':float(r.get('v52StaleEvidenceLeak') or 0.0)}

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','target-teacher-json','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v67_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V67','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V67_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};target=json.load(open(a.target_teacher_json,encoding='utf-8'))['markets']
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[];experience=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';te=target.get(str(mid),[])
   p=V67CounterfactualBranch(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:base=p.run_exam_v67(models,cr['winner'])
   finally:p.close()
   tpre=target_stats(te,int(p.capEnd)-180000);tfull=target_stats(te);eligible=[x for x in base['v67Opportunities'] if x['counterfactualEligible']];branches=[]
   for op in eligible:
    ordinal=int(op['ordinal']);c=V67CounterfactualBranch(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,force_ordinal=ordinal)
    try:rr=c.run_exam_v67(models,cr['winner'])
    finally:c.close()
    sf=safety(rr);safe=all(abs(v)<=EPS for v in sf.values());materialized=float(rr['v67ForceFillQty'])>EPS;covered=int(rr['v67ForceGeneratedRepaired'])>0;round_delta=int(rr['v64Rounds'])-int(base['v64Rounds']);label=int(bool(rr['v67ForceSubmitted']) and materialized and covered and round_delta>=0 and safe)
    sample={'marketId':mid,'decisionOrdinal':ordinal,'t':int(op['t']),'state':op['state'],'learnedReason':op['learnedReason'],'pV44':op.get('pV44'),'pV47':op.get('pV47'),'targetPre180Rounds':tpre['repairExpandRepairRounds'],'baselineRounds':base['v64Rounds'],'targetRoundDeficit':max(0,int(tpre['repairExpandRepairRounds'])-int(base['v64Rounds'])),'action':'OPEN_OR_EXTEND_EXPAND','forcedSubmitted':rr['v67ForceSubmitted'],'forcedFillQty':rr['v67ForceFillQty'],'forcedGeneratedRepaired':rr['v67ForceGeneratedRepaired'],'candidateRounds':rr['v64Rounds'],'roundDelta':round_delta,'strictRoundDelta':int(rr['v64StrictRounds'])-int(base['v64StrictRounds']),'floorDelta':float(rr['floor'])-float(base['floor']),'absNetDelta':float(rr['absNet'])-float(base['absNet']),'pnlDiagnosticDelta':float(rr['pnlDiagnosticOnly'])-float(base['pnlDiagnosticOnly']),'safety':sf,'labelOpenOrExtend':label,'forceEvents':rr['v67ForceEvents'],'candidateSequence':rr['v64CompressedSequence']}
    experience.append(sample);branches.append({'ordinal':ordinal,'result':rr,'sample':sample});print(json.dumps({'marketId':mid,'ordinal':ordinal,'fillQty':rr['v67ForceFillQty'],'repaired':rr['v67ForceGeneratedRepaired'],'roundDelta':round_delta,'label':label,'safety':sf},ensure_ascii=False),flush=True)
   rows.append({'marketId':mid,'winner':cr['winner'],'targetPre180':tpre,'targetFull':tfull,'baseline':base,'eligibleOpportunities':eligible,'branches':branches});print(json.dumps({'marketId':mid,'baselineRounds':base['v64Rounds'],'targetPre180Rounds':tpre['repairExpandRepairRounds'],'eligibleBranches':len(eligible)},ensure_ascii=False),flush=True)
  positives=sum(x['labelOpenOrExtend'] for x in experience);negatives=len(experience)-positives;submitted=sum(x['forcedSubmitted'] for x in experience);filled=sum(float(x['forcedFillQty']) for x in experience);covered=sum(int(x['forcedGeneratedRepaired']) for x in experience);viol=sum(sum(abs(float(v)) for v in x['safety'].values()) for x in experience)
  agg={'markets':len(rows),'baselineRounds':sum(int(x['baseline']['v64Rounds']) for x in rows),'targetPre180Rounds':sum(int(x['targetPre180']['repairExpandRepairRounds']) for x in rows),'targetRoundDeficit':sum(max(0,int(x['targetPre180']['repairExpandRepairRounds'])-int(x['baseline']['v64Rounds'])) for x in rows),'eligibleCounterfactuals':len(experience),'submittedBranches':submitted,'forcedFillQty':filled,'coveredBranches':covered,'positiveDemonstrations':positives,'negativeDemonstrations':negatives,'safetyViolationMagnitude':viol}
  gates={'hasTargetLifecycleDeficit':agg['targetRoundDeficit']>0,'hasEligibleOurStateBranches':agg['eligibleCounterfactuals']>0,'counterfactualActionMaterializes':agg['forcedFillQty']>EPS,'producesPositiveDemonstration':positives>0,'producesNegativeDemonstration':negatives>0,'allAccountingSafe':viol<=EPS}
  out={'version':'ETH_REPAIR_V67_PHASE_ALIGNED_COUNTERFACTUAL_EXPERIENCE_SMOKE','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'trainingDataOnly':True,'aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'experience':experience,'rows':rows,'boundary':['Target is used only after market completion to establish lifecycle phase/count deficit.','No Target timestamp, side, price, quantity, action or winner enters a branch decision.','Every branch begins from an actual OUR Repair-fill decision opportunity and changes only OPEN_OR_EXTEND versus learned CONTINUE.','Each candidate action is replayed in OUR realistic-HFT state and labeled from actual fill, subsequent Repair, rounds and ledger safety.','No PnL/floor threshold in label; values are diagnostic only.','1-3 market smoke/no tuning/no dream fill/no 8781/<=180s new exposure guard.']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
