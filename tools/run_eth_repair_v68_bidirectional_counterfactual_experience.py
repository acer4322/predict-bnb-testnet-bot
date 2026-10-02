from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v67=sib('eth_v67_for_v68','run_eth_repair_v67_phase_aligned_counterfactual_experience.py')
v65=v67.v65;v53=v67.v53;v38=v67.v38;EPS=1e-9

class V68BidirectionalBranch(v67.V67CounterfactualBranch):
 def __init__(self,*a,suppress_ordinal=None,**kw):
  if suppress_ordinal is not None and kw.get('force_ordinal') is not None:raise ValueError('force and suppress are mutually exclusive')
  super().__init__(*a,**kw);self.v68SuppressOrdinal=suppress_ordinal;self.v68ScoringOrdinal=None;self.v68SuppressedSubmits=0;self.v68SuppressEvents=[]
 def submit(self,t,side,p,q):
  selected=(self.v68SuppressOrdinal is not None and self.v68ScoringOrdinal==self.v68SuppressOrdinal)
  lane=str(getattr(self,'_pendingLane',None) or '')
  role=str(getattr(self,'_pendingAuthorizedRole',None) or '')
  if selected and self.v68SuppressedSubmits==0 and lane=='V48_PARALLEL_SURPLUS' and role=='EXPAND':
   self.v68SuppressedSubmits+=1
   self.v68SuppressEvents.append({'t':int(t),'event':'COUNTERFACTUAL_CONTINUE_SUPPRESS','ordinal':int(self.v68ScoringOrdinal),'side':side,'price':float(p),'qty':float(q),'lane':lane,'objectiveId':getattr(self,'_pendingAuthorizedObjectiveId',None)})
   self._pendingAuthorizedRole=None;self._pendingAuthorizedObjectiveId=None;self._pendingParentId=None;self._pendingLane=None
   return False
  return super().submit(t,side,p,q)
 def _score_state(self,t,after_kind):
  n=len(self.v44Decisions)
  if after_kind=='REPAIR':self.v68ScoringOrdinal=int(self.v67Ordinal)+1
  try:out=super()._score_state(t,after_kind)
  finally:self.v68ScoringOrdinal=None
  if after_kind=='REPAIR' and self.v67Opportunities:
   learned=self.v44Decisions[-1] if len(self.v44Decisions)>n else None
   row=self.v67Opportunities[-1]
   if learned is not None:
    row.update({'learnedKey':learned.get('key'),'learnedPhase':learned.get('phase'),'learnedSide':learned.get('side'),'learnedPrice':learned.get('price'),'learnedQty':learned.get('qty')})
   if self.v68SuppressOrdinal==row.get('ordinal'):
    row['suppressSelected']=True;row['suppressedSubmits']=self.v68SuppressedSubmits
  return out
 @staticmethod
 def _action_fill_outcome(events,keys):
  idx=[i for i,e in enumerate(events) if e['key'] in keys and e['role'] in ('PASSIVE_EXPAND','ACTIVE_EXPAND')]
  fillqty=sum(float(events[i]['qty']) for i in idx);repaired=0
  if idx:
   i=max(idx);hi=next((j for j in range(i+1,len(events)) if events[j]['role'] in ('PASSIVE_EXPAND','ACTIVE_EXPAND')),len(events))
   repaired=int(any(events[j]['role'] in ('PASSIVE_REPAIR','ACTIVE_REPAIR') for j in range(i+1,hi)))
  return {'keys':sorted(keys),'fillQty':fillqty,'generatedRepaired':repaired,'fillEvents':[events[i] for i in idx]}
 def run_exam_v68(self,models,winner):
  r=super().run_exam_v67(models,winner);ev=sorted(self.v53Fills,key=lambda x:(int(x['t']),x['key']));out=[]
  for op in self.v67Opportunities:
   if not op.get('learnedSubmit') or not op.get('learnedKey'):continue
   key=op['learnedKey'];keys={key}
   for k,a in self.v64Active.items():
    if a.get('sourceKey')==key:keys.add(k)
   out.append({'ordinal':int(op['ordinal']),'sourceKey':key,**self._action_fill_outcome(ev,keys)})
  r.update({'v68SuppressOrdinal':self.v68SuppressOrdinal,'v68SuppressedSubmits':self.v68SuppressedSubmits,'v68SuppressEvents':self.v68SuppressEvents[:40],'v68LearnedActionOutcomes':out})
  return r

def target_stats(events,cutoff=None):
 return v67.target_stats(events,cutoff)

def safety(r):
 return v67.safety(r)

def common_sample(mid,op,tpre,base):
 return {'marketId':mid,'decisionOrdinal':int(op['ordinal']),'t':int(op['t']),'state':op.get('state') or {},'learnedReason':op.get('learnedReason'),'learnedPhase':op.get('learnedPhase'),'pV44':op.get('pV44'),'pV47':op.get('pV47'),'targetPre180Rounds':int(tpre['repairExpandRepairRounds']),'baselineV65Rounds':int(base['v64Rounds']),'targetRoundDeficit':max(0,int(tpre['repairExpandRepairRounds'])-int(base['v64Rounds'])),'action':'OPEN_OR_EXTEND_EXPAND'}

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','target-teacher-json','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v68_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V68','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V68_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};target=json.load(open(a.target_teacher_json,encoding='utf-8'))['markets']
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[];experience=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';te=target.get(str(mid),[])
   p=V68BidirectionalBranch(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:base=p.run_exam_v68(models,cr['winner'])
   finally:p.close()
   tpre=target_stats(te,int(p.capEnd)-180000);tfull=target_stats(te);base_safe=safety(base)
   holds=[x for x in base['v67Opportunities'] if x.get('counterfactualEligible')]
   opens=[x for x in base['v67Opportunities'] if x.get('learnedSubmit') and x.get('learnedKey')]
   branches=[]
   for op in holds:
    ordinal=int(op['ordinal']);c=V68BidirectionalBranch(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,force_ordinal=ordinal)
    try:rr=c.run_exam_v68(models,cr['winner'])
    finally:c.close()
    sf=safety(rr);safe=all(abs(v)<=EPS for v in sf.values()) and all(abs(v)<=EPS for v in base_safe.values());round_adv=int(rr['v64Rounds'])-int(base['v64Rounds'])
    label=int(bool(rr['v67ForceSubmitted']) and float(rr['v67ForceFillQty'])>EPS and int(rr['v67ForceGeneratedRepaired'])>0 and round_adv>=0 and safe)
    sample={**common_sample(mid,op,tpre,base),'source':'HOLD_TO_OPEN','observedAction':'CONTINUE','counterfactualAction':'OPEN_OR_EXTEND','openSubmitted':bool(rr['v67ForceSubmitted']),'openFillQty':float(rr['v67ForceFillQty']),'openGeneratedRepaired':int(rr['v67ForceGeneratedRepaired']),'openRounds':int(rr['v64Rounds']),'continueRounds':int(base['v64Rounds']),'roundAdvantageOpenMinusContinue':round_adv,'strictRoundAdvantageOpenMinusContinue':int(rr['v64StrictRounds'])-int(base['v64StrictRounds']),'floorDeltaOpenMinusContinue':float(rr['floor'])-float(base['floor']),'absNetDeltaOpenMinusContinue':float(rr['absNet'])-float(base['absNet']),'pnlDiagnosticDeltaOpenMinusContinue':float(rr['pnlDiagnosticOnly'])-float(base['pnlDiagnosticOnly']),'safetyOpen':sf,'safetyContinue':base_safe,'labelOpenOrExtend':label,'interventionEvents':rr['v67ForceEvents']}
    experience.append(sample);branches.append({'source':'HOLD_TO_OPEN','ordinal':ordinal,'sample':sample})
    print(json.dumps({'marketId':mid,'source':'HOLD_TO_OPEN','ordinal':ordinal,'fillQty':sample['openFillQty'],'repaired':sample['openGeneratedRepaired'],'roundAdvantage':round_adv,'label':label},ensure_ascii=False),flush=True)
   outcomes={int(x['ordinal']):x for x in base.get('v68LearnedActionOutcomes',[])}
   for op in opens:
    ordinal=int(op['ordinal']);c=V68BidirectionalBranch(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,suppress_ordinal=ordinal)
    try:rr=c.run_exam_v68(models,cr['winner'])
    finally:c.close()
    sf=safety(rr);safe=all(abs(v)<=EPS for v in sf.values()) and all(abs(v)<=EPS for v in base_safe.values());oo=outcomes.get(ordinal,{'fillQty':0.0,'generatedRepaired':0,'keys':[]});round_adv=int(base['v64Rounds'])-int(rr['v64Rounds']);suppressed=int(rr['v68SuppressedSubmits'])
    label=int(suppressed==1 and float(oo['fillQty'])>EPS and int(oo['generatedRepaired'])>0 and round_adv>=0 and safe)
    sample={**common_sample(mid,op,tpre,base),'source':'OPEN_TO_CONTINUE','observedAction':'OPEN_OR_EXTEND','counterfactualAction':'CONTINUE','learnedKey':op.get('learnedKey'),'openFillQty':float(oo['fillQty']),'openGeneratedRepaired':int(oo['generatedRepaired']),'openExecutionKeys':oo.get('keys',[]),'suppressedSubmits':suppressed,'openRounds':int(base['v64Rounds']),'continueRounds':int(rr['v64Rounds']),'roundAdvantageOpenMinusContinue':round_adv,'strictRoundAdvantageOpenMinusContinue':int(base['v64StrictRounds'])-int(rr['v64StrictRounds']),'floorDeltaOpenMinusContinue':float(base['floor'])-float(rr['floor']),'absNetDeltaOpenMinusContinue':float(base['absNet'])-float(rr['absNet']),'pnlDiagnosticDeltaOpenMinusContinue':float(base['pnlDiagnosticOnly'])-float(rr['pnlDiagnosticOnly']),'safetyOpen':base_safe,'safetyContinue':sf,'labelOpenOrExtend':label,'interventionEvents':rr['v68SuppressEvents']}
    experience.append(sample);branches.append({'source':'OPEN_TO_CONTINUE','ordinal':ordinal,'sample':sample})
    print(json.dumps({'marketId':mid,'source':'OPEN_TO_CONTINUE','ordinal':ordinal,'suppressed':suppressed,'openFillQty':sample['openFillQty'],'openRepaired':sample['openGeneratedRepaired'],'roundAdvantage':round_adv,'label':label},ensure_ascii=False),flush=True)
   rows.append({'marketId':mid,'winner':cr['winner'],'targetPre180':tpre,'targetFull':tfull,'baseline':base,'holdOpportunities':holds,'openOpportunities':opens,'branches':branches})
   print(json.dumps({'marketId':mid,'baselineRounds':base['v64Rounds'],'holds':len(holds),'opens':len(opens),'samples':len(branches)},ensure_ascii=False),flush=True)
  holdn=sum(x['source']=='HOLD_TO_OPEN' for x in experience);openn=sum(x['source']=='OPEN_TO_CONTINUE' for x in experience);pos=sum(int(x['labelOpenOrExtend']) for x in experience);neg=len(experience)-pos;viol=sum(sum(abs(float(v)) for v in x['safetyOpen'].values())+sum(abs(float(v)) for v in x['safetyContinue'].values()) for x in experience);supp=sum(int(x.get('suppressedSubmits') or 0) for x in experience if x['source']=='OPEN_TO_CONTINUE')
  agg={'markets':len(rows),'samples':len(experience),'holdToOpenSamples':holdn,'openToContinueSamples':openn,'positiveDemonstrations':pos,'negativeDemonstrations':neg,'suppressedSubmits':supp,'allOpenBranchesSuppressedExactlyOnce':bool(openn>0 and all(int(x.get('suppressedSubmits') or 0)==1 for x in experience if x['source']=='OPEN_TO_CONTINUE')),'safetyViolationMagnitude':viol}
  gates={'holdToOpenExercised':holdn>0,'openToContinueExercised':openn>0,'everySelectedOpenSuppressedExactlyOnce':agg['allOpenBranchesSuppressedExactlyOnce'],'bidirectionalCoverageExpanded':len(experience)>holdn,'hasBothOutcomeClasses':pos>0 and neg>0,'allAccountingSafe':viol<=EPS}
  out={'version':'ETH_REPAIR_V68_BIDIRECTIONAL_COUNTERFACTUAL_EXPERIENCE_SMOKE','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'trainingDataOnly':True,'aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'experience':experience,'rows':rows,'boundary':['Target is curriculum phase/count context only and is excluded from action features.','HOLD_TO_OPEN changes only one CONTINUE decision into one venue-min Expand.','OPEN_TO_CONTINUE suppresses exactly one selected V48_PARALLEL_SURPLUS submit and leaves every other decision and execution event endogenous.','Labels use actual realistic-HFT fill, subsequent Repair, relative lifecycle rounds and zero safety violations.','No PnL/floor trigger or label; diagnostics only.','No threshold/qty/delay/price tuning, no dream fill, no 8781, <=180s exposure guard inherited.']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
