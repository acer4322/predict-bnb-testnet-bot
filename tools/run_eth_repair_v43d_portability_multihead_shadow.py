from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v43=sib('eth_v43_for_v43d','run_eth_repair_v43_coordination_teacher_shadow.py')
v38=v43.v38;EPS=1e-9
class V43D(v43.V43CoordinationShadow):
 def __init__(self,*a,multi=None,**kw):super().__init__(*a,coord_model=None,**kw);self.multi=multi or {};self.v43States=[]
 def _coord_feature(self,t):
  f=super()._coord_feature(t);gp=f['gross']+1.;dp=f['debt']+1.;f.update({'absNetGrossRatio':f['absNet']/gp,'repairQty5Gross':f['repairQty5']/gp,'repairQty15Gross':f['repairQty15']/gp,'repairQty30Gross':f['repairQty30']/gp,'expandQty5Gross':f['expandQty5']/gp,'expandQty15Gross':f['expandQty15']/gp,'expandQty30Gross':f['expandQty30']/gp,'repairQty30Debt':f['repairQty30']/dp,'expandQty30Debt':f['expandQty30']/dp});return f
 def _score_state(self,t,after_kind):
  if self._coordDebt<=EPS:return
  f=self._coord_feature(t);pred={}
  for name,z in self.multi.items():
   x=np.asarray([[float(f[c]) for c in z['features']]],np.float32);pred[name]=float(z['model'].predict_proba(x)[0,1])
  self.v43States.append({'t':int(t),'afterKind':after_kind,'repairParentActive':bool(self.repairParent is not None),'floor':f['floor'],'best':f['best'],'debt':f['debt'],'repairProgressFrac':f['repairProgressFrac'],'pred':pred})
 def run_exam_v43d(self,models,winner):
  r=v38.V38IncrementalOnly.run_exam_v38a(self,models,winner);st=self.v43States;after=[z for z in st if z['afterKind']=='REPAIR' and z['debt']>EPS];r['v43dAfterRepair']=after[:120];r['v43dAfterRepairCount']=len(after);return r
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','portability-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v43d_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V43D','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V43D_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);multi=joblib.load(a.portability_model)['models'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V43D(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,multi=multi)
   try:r=sim.run_exam_v43d(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'afterRepair':r['v43dAfterRepairCount'],'partial':r['v38PartialProgressEvents']}),flush=True)
  after=[]
  for rr in rows:
   for s in rr['functional']['v43dAfterRepair']:after.append({'marketId':rr['marketId'],**s})
  names=list(multi);summ={}
  for n in names:
   ps=[x['pred'][n] for x in after];summ[n]={'n':len(ps),'majorityExpand':sum(p>=.5 for p in ps),'majorityRate':sum(p>=.5 for p in ps)/len(ps) if ps else 0.,'median':float(np.median(ps)) if ps else None,'min':min(ps) if ps else None,'max':max(ps) if ps else None}
  out={'version':'ETH_REPAIR_V43D_PORTABILITY_MULTIHEAD_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'models':names,'aggregate':{'markets':len(rows),'afterRepairDebtStates':len(after),'predictions':summ,'truthMismatch':sum(float(r['functional'].get('authorizedSubmitWithTruthRoleMismatch') or 0) for r in rows),'overOwned':sum(float(r['functional'].get('overOwnedSubmitViolations') or 0) for r in rows),'repairDrift':sum(float(r['functional'].get('repairToExpandAtFirstFill') or 0) for r in rows)},'afterRepairStates':after,'boundary':['consumed realistic HFT only','score-only multihead portability audit','no behavior change','no winner/PnL feature','no threshold tuning beyond descriptive p>=0.5','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
