from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v43=sib('eth_v43_for_v45b','run_eth_repair_v43_coordination_teacher_shadow.py');v38=v43.v38;EPS=1e-9
class V45B(v43.V43CoordinationShadow):
 def __init__(self,*a,models45=None,**kw):
  super().__init__(*a,coord_model=None,**kw);self.models45=models45;self.v45bStates=[]
 def _coord_feature(self,t):
  f=super()._coord_feature(t);gp=f['gross']+1.;dp=f['debt']+1.
  f.update({'absNetGrossRatio':f['absNet']/gp,'repairQty5Gross':f['repairQty5']/gp,'repairQty15Gross':f['repairQty15']/gp,'repairQty30Gross':f['repairQty30']/gp,'expandQty5Gross':f['expandQty5']/gp,'expandQty15Gross':f['expandQty15']/gp,'expandQty30Gross':f['expandQty30']/gp,'repairQty30Debt':f['repairQty30']/dp,'expandQty30Debt':f['expandQty30']/dp})
  return f
 def _score_state(self,t,after_kind):
  if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.models45 is None:return
  f=self._coord_feature(t);is_taker=any(e.get('event')=='V36_ACTIVE_SHARED_FILL' and int(e.get('t',-1))==int(t) for e in getattr(self,'activeEvents',[]));f['lastRepairWasTaker']=1.0 if is_taker else 0.0
  row={'t':int(t),'parentId':int(self.repairParent.get('id')),'progress':f['repairProgressFrac'],'debt':f['debt'],'floor':f['floor'],'lastRepairWasTaker':bool(is_taker)}
  for name in ('EVENT_VALUE_NORM','ROLE_AWARE_NORM'):
   z=self.models45[name];x=np.asarray([[float(f[c]) for c in z['features']]],np.float32);row[name]=float(z['model'].predict_proba(x)[0,1])
  self.v45bStates.append(row)
 def run_exam_v45b(self,models,winner):
  r=v38.V38IncrementalOnly.run_exam_v38a(self,models,winner);r.update({'v45bStates':self.v45bStates[:120],'v45bStateCount':len(self.v45bStates),'v45bTakerRepairStates':sum(x['lastRepairWasTaker'] for x in self.v45bStates)});return r
def summarize(xs,k):
 if not xs:return {'n':0,'expand':0,'rate':0.0,'median':None}
 a=sorted(float(x[k]) for x in xs);n=len(xs);e=sum(float(x[k])>=.5 for x in xs);return {'n':n,'expand':e,'rate':e/n,'median':a[n//2]}
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','role-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v45b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V45B','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V45B_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);mm=joblib.load(a.role_model)['models'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V45B(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,models45=mm)
   try:r=sim.run_exam_v45b(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'states':r['v45bStateCount'],'takerStates':r['v45bTakerRepairStates']},ensure_ascii=False),flush=True)
  allst=[s for x in rows for s in x['functional']['v45bStates']];tk=[s for s in allst if s['lastRepairWasTaker']];mk=[s for s in allst if not s['lastRepairWasTaker']]
  agg={'states':len(allst),'takerRepairStates':len(tk),'makerRepairStates':len(mk),'eventAll':summarize(allst,'EVENT_VALUE_NORM'),'roleAll':summarize(allst,'ROLE_AWARE_NORM'),'eventTaker':summarize(tk,'EVENT_VALUE_NORM'),'roleTaker':summarize(tk,'ROLE_AWARE_NORM'),'eventMaker':summarize(mk,'EVENT_VALUE_NORM'),'roleMaker':summarize(mk,'ROLE_AWARE_NORM')}
  out={'version':'ETH_REPAIR_V45B_ROLE_AWARE_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'aggregate':agg,'rows':rows,'boundary':['consumed Stage-A realistic HFT only','actual V36 active fill event defines lastRepairWasTaker','no behavior change','no PnL/winner feature','no threshold tuning; 0.5 model majority descriptive only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
 finally:
  stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
