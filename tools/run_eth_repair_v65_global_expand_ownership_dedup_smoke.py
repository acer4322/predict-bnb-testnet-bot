from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v64=sib('eth_v64_for_v65','run_eth_repair_v64_active_expand_execution_fallback_smoke.py');v53=v64.v53;v38=v64.v38;EPS=1e-9

class V65GlobalExpandDedup(v64.V64ActiveExpandFallback):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v65GlobalSupersedeBlocks=0;self.v65Events=[]
 def _later_v44_exists(self,key):
  src=self.carrierLedger.get(key,{});t0=int(src.get('submittedAt') or 0)
  # Global execution-certainty occupancy: any newer EXPAND carrier, from any lane/objective,
  # supersedes this failed passive V44 execution attempt. This avoids duplicate exposure routing.
  newer=[]
  for k,z in getattr(self,'carrierLedger',{}).items():
   if k==key or str(z.get('objectiveRole') or '')!='EXPAND':continue
   ts=int(z.get('submittedAt') or 0)
   if ts>t0:newer.append((ts,k,z.get('lane'),z.get('objectiveId'),float(z.get('actualFilled') or 0.0),bool(z.get('terminalConfirmed'))))
  if newer:
   newer.sort();self.v65GlobalSupersedeBlocks+=1;ts,k,lane,oid,filled,terminal=newer[-1];self.v65Events.append({'event':'GLOBAL_EXPAND_SUPERSEDE_BLOCK','sourceKey':key,'sourceSubmittedAt':t0,'newerKey':k,'newerSubmittedAt':ts,'newerLane':lane,'newerObjectiveId':oid,'newerFilled':filled,'newerTerminal':terminal});return True
  return False
 def run_exam_v65(self,models,winner):
  r=super().run_exam_v64(models,winner);r.update({'v65GlobalSupersedeBlocks':self.v65GlobalSupersedeBlocks,'v65Events':self.v65Events[:120]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v65_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V65','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V65_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v53.V53MultiCycleAudit(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v53(models,cr['winner'])
   finally:b.close()
   c=V65GlobalExpandDedup(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v65(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':rr});print(json.dumps({'marketId':mid,'submits':rr['v64Submits'],'fillQty':rr['v64FillQty'],'supersedeBlocks':rr['v65GlobalSupersedeBlocks'],'rounds':[br['repairExpandRepairRounds'],rr['v64Rounds']],'generatedRepaired':rr['v64GeneratedExpandThenRepair'],'pnl':[br['pnlDiagnosticOnly'],rr['pnlDiagnosticOnly']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0) for x in rows)
  agg={'markets':len(rows),'activeExpandSubmits':int(sm('candidate','v64Submits')),'activeExpandFillQty':sm('candidate','v64FillQty'),'globalSupersedeBlocks':int(sm('candidate','v65GlobalSupersedeBlocks')),'baselineRounds':int(sm('baseline','repairExpandRepairRounds')),'candidateRounds':int(sm('candidate','v64Rounds')),'roundGain':int(sm('candidate','v64Rounds')-sm('baseline','repairExpandRepairRounds')),'generatedRepaired':int(sm('candidate','v64GeneratedExpandThenRepair')),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill')}
  gates={'globalDedupExercised':agg['globalSupersedeBlocks']>0,'activeExpandStillExercises':agg['activeExpandFillQty']>EPS,'generatedActiveExpandGetsRepair':agg['generatedRepaired']>0,'roundsNonDecreasing':agg['roundGain']>=0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS}
  out={'version':'ETH_REPAIR_V65_GLOBAL_EXPAND_OWNERSHIP_DEDUP_SMOKE','researchOnly':True,'behaviorChange':True,'actionAuthority':'FUNCTIONAL_SMOKE_ONLY','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['V64 same-objective active execution fallback preserved','any newer EXPAND carrier from any lane/objective supersedes older failed V44 passive execution','execution certainty/ownership dedup, not numeric timing gate','small-scale only','no PnL/winner/threshold/qty/delay tuning','no H100/no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
