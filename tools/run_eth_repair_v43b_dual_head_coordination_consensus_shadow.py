from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(file)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v43=sib('eth_v43_for_v43b','run_eth_repair_v43_coordination_teacher_shadow.py')
EPS=1e-9
class V43BDualHeadShadow(v43.V43CoordinationShadow):
 def __init__(self,*a,progress_model=None,**kw):super().__init__(*a,**kw);self.progress=progress_model;self.v43bStates=[]
 def _score_state(self,t,after_kind):
  if self.coord is None or self.progress is None or self._coordDebt<=EPS:return
  f=self._coord_feature(t)
  xc=np.asarray([[float(f[c]) for c in self.coord['features']]],np.float32);xp=np.asarray([[float(f[c]) for c in self.progress['features']]],np.float32)
  pc=float(self.coord['model'].predict_proba(xc)[0,1]);pp=float(self.progress['model'].predict_proba(xp)[0,1]);cons=pc>=.5 and pp>=.5
  z={'t':int(t),'afterKind':after_kind,'repairParentActive':bool(self.repairParent is not None),'floor':f['floor'],'best':f['best'],'debt':f['debt'],'repairProgressFrac':f['repairProgressFrac'],'pCoordExpand':pc,'pProgressExpand':pp,'coordExpand':pc>=.5,'progressExpand':pp>=.5,'consensusExpand':cons}
  self.v43bStates.append(z)
  # keep parent V43-compatible audit too
  self.v43States.append({'t':int(t),'afterKind':after_kind,'repairParentActive':z['repairParentActive'],'floor':f['floor'],'best':f['best'],'debt':f['debt'],'repairProgressFrac':f['repairProgressFrac'],'pExpand':pc,'teacherMajorityExpand':pc>=.5})
 def run_exam_v43b(self,models,winner):
  r=super().run_exam_v43(models,winner);a=[z for z in self.v43bStates if z['afterKind']=='REPAIR' and z['debt']>EPS];ap=[z for z in a if z['repairParentActive']]
  r.update({'v43bAfterRepairDebtStates':len(a),'v43bConsensusExpandCount':sum(z['consensusExpand'] for z in a),'v43bConsensusExpandRate':sum(z['consensusExpand'] for z in a)/len(a) if a else 0.0,'v43bCoordOnlyRejectByProgress':sum(z['coordExpand'] and not z['progressExpand'] for z in a),'v43bConsensusActiveParentCount':sum(z['consensusExpand'] for z in ap),'v43bStates':self.v43bStates[:120]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','teacher-models','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v43b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V43B','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V43B_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v43.v38.v36.v34.v30.load_runtime(a);tm=joblib.load(a.teacher_models);coord=tm[('ETH','COORDINATION')];prog=tm[('ETH','PROGRESS')];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V43BDualHeadShadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,coord_model=coord,progress_model=prog)
   try:r=sim.run_exam_v43b(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'afterRepair':r['v43bAfterRepairDebtStates'],'consensus':r['v43bConsensusExpandCount'],'rejectedByProgress':r['v43bCoordOnlyRejectByProgress'],'partial':r['v38PartialProgressEvents']},ensure_ascii=False),flush=True)
  def sm(k):return sum(float(x['functional'].get(k) or 0) for x in rows)
  n=sm('v43bAfterRepairDebtStates');agg={'markets':len(rows),'afterRepairDebtStates':int(n),'consensusExpandCount':int(sm('v43bConsensusExpandCount')),'consensusExpandRate':sm('v43bConsensusExpandCount')/n if n else 0.0,'coordOnlyRejectedByProgress':int(sm('v43bCoordOnlyRejectByProgress')),'partialProgressEvents':int(sm('v38PartialProgressEvents')),'truthMismatch':sm('authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('overOwnedSubmitViolations'),'repairDrift':sm('repairToExpandAtFirstFill')}
  gates={'support':agg['afterRepairDebtStates']>=3,'consensusWindowExists':agg['consensusExpandCount']>0,'progressHeadActuallyFilters':agg['coordOnlyRejectedByProgress']>0,'accountingUnchanged':agg['truthMismatch']==0 and agg['overOwned']==0 and agg['repairDrift']==0}
  out={'version':'ETH_REPAIR_V43B_DUAL_HEAD_COORDINATION_CONSENSUS_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'aggregate':agg,'gates':gates,'shadowPass':all(gates.values()),'rows':rows,'boundary':['COORDINATION and PROGRESS ETH Target heads must both have pExpand>=0.5','no fitted repair-progress threshold','strict-past OUR actual-fill state only','no order change','no winner/PnL','realistic HFT only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'shadowPass':out['shadowPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
