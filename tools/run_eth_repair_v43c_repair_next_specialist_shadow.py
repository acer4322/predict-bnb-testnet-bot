from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from collections import deque
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(file)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v38=sib('eth_v38_for_v43','run_eth_repair_v38_incremental_repair_parallel_surplus_causal.py')
EPS=1e-9

class V43CRepairNextSpecialistShadow(v38.V38IncrementalOnly):
 def __init__(self,*a,coord_model=None,**kw):
  super().__init__(*a,**kw);self.coord=coord_model;self._coordFillSeen=0;self._coordU=0.0;self._coordD=0.0;self._coordDebt=0.0;self._coordRepairedCum=0.0;self._coordHist=deque();self._coordLastKind=None;self._coordStreak=0;self._coordLastRepairT=None;self._coordLastExpandT=None;self.v43States=[]
 def _coord_feature(self,t):
  while self._coordHist and int(t)-int(self._coordHist[0][0])>30000:self._coordHist.popleft()
  floor,u,d,cost=self._raw_floor();gross=float(u+d);absn=abs(float(u-d));debt=float(self._coordDebt);best=max(float(u),float(d))-float(cost)
  def agg(kind,win):
   z=[h for h in self._coordHist if h[1]==kind and int(t)-int(h[0])<=win];return sum(float(h[2]) for h in z),len(z)
  rq5,rc5=agg('REPAIR',5000);rq15,rc15=agg('REPAIR',15000);rq30,rc30=agg('REPAIR',30000);eq5,ec5=agg('EXPAND',5000);eq15,ec15=agg('EXPAND',15000);eq30,ec30=agg('EXPAND',30000)
  return {'floor':float(floor),'best':best,'absNet':absn,'gross':gross,'debt':debt,'floorBestRatio':float(floor)/(abs(best)+1.0),'debtGrossRatio':debt/(gross+1.0),'repairProgressFrac':self._coordRepairedCum/(self._coordRepairedCum+debt+EPS),'repairQty5':rq5,'repairQty15':rq15,'repairQty30':rq30,'expandQty5':eq5,'expandQty15':eq15,'expandQty30':eq30,'repairCount5':rc5,'repairCount15':rc15,'repairCount30':rc30,'expandCount5':ec5,'expandCount15':ec15,'expandCount30':ec30,'secSinceRepair':120.0 if self._coordLastRepairT is None else min(120.0,(int(t)-int(self._coordLastRepairT))/1000.0),'secSinceExpand':120.0 if self._coordLastExpandT is None else min(120.0,(int(t)-int(self._coordLastExpandT))/1000.0),'sameRoleStreak':self._coordStreak,'lastWasRepair':1.0 if self._coordLastKind=='REPAIR' else 0.0}
 def _score_state(self,t,after_kind):
  if self.coord is None or self._coordDebt<=EPS:return
  feat=self._coord_feature(t);cols=self.coord['features'];x=np.asarray([[float(feat[c]) for c in cols]],np.float32);p=float(self.coord['model'].predict_proba(x)[0,1]);self.v43States.append({'t':int(t),'afterKind':after_kind,'repairParentActive':bool(self.repairParent is not None),'floor':feat['floor'],'best':feat['best'],'debt':feat['debt'],'repairProgressFrac':feat['repairProgressFrac'],'pExpand':p,'teacherMajorityExpand':bool(p>=0.5)})
 def _consume_new_fills(self):
  rows=self.authHist[self._coordFillSeen:]
  if not rows:return
  for x in rows:
   t=int(x['time']);side=str(x['side']).upper();q=float(x['shares']);preAbs=abs(self._coordU-self._coordD);preDebt=self._coordDebt
   if side=='UP':self._coordU+=q
   else:self._coordD+=q
   postAbs=abs(self._coordU-self._coordD);delta=postAbs-preAbs
   if delta>EPS:kind='EXPAND';self._coordDebt=max(0.0,preDebt)+delta;self._coordLastExpandT=t
   elif delta< -EPS:kind='REPAIR';pay=min(max(0.0,preDebt),-delta);self._coordDebt=max(0.0,preDebt-pay);self._coordRepairedCum+=pay;self._coordLastRepairT=t
   else:kind='FLAT'
   if kind in ('EXPAND','REPAIR'):
    self._coordStreak=self._coordStreak+1 if kind==self._coordLastKind else 1;self._coordLastKind=kind;self._coordHist.append((t,kind,q));self._score_state(t,kind)
   if self._coordDebt<=EPS:self._coordRepairedCum=0.0
  self._coordFillSeen=len(self.authHist)
 def process(self,t):
  super().process(t);self._consume_new_fills()
 def run_exam_v43(self,models,winner):
  r=super().run_exam_v38a(models,winner);st=self.v43States;after=[z for z in st if z['afterKind']=='REPAIR' and z['debt']>EPS];active=[z for z in after if z['repairParentActive']]
  def med(vals):
   a=sorted(vals);return a[len(a)//2] if a else None
  r.update({'v43StateCount':len(st),'v43AfterRepairDebtStates':len(after),'v43AfterRepairActiveParentStates':len(active),'v43TeacherExpandAfterRepairCount':sum(z['teacherMajorityExpand'] for z in after),'v43TeacherExpandAfterRepairRate':sum(z['teacherMajorityExpand'] for z in after)/len(after) if after else 0.0,'v43TeacherExpandActiveParentCount':sum(z['teacherMajorityExpand'] for z in active),'v43TeacherExpandActiveParentRate':sum(z['teacherMajorityExpand'] for z in active)/len(active) if active else 0.0,'v43PExpandMedianAfterRepair':med([z['pExpand'] for z in after]),'v43PExpandMedianActiveParent':med([z['pExpand'] for z in active]),'v43States':st[:120]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','coordination-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v43_shadow_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V43C_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V43C_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);cm=joblib.load(a.coordination_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V43CRepairNextSpecialistShadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,coord_model=cm)
   try:r=sim.run_exam_v43(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'states':r['v43StateCount'],'afterRepair':r['v43AfterRepairDebtStates'],'activeParent':r['v43AfterRepairActiveParentStates'],'teacherExpandRate':r['v43TeacherExpandAfterRepairRate'],'activeExpandRate':r['v43TeacherExpandActiveParentRate'],'partialProgress':r['v38PartialProgressEvents']},ensure_ascii=False),flush=True)
  def sm(k):return sum(float(x['functional'].get(k) or 0) for x in rows)
  n=sm('v43AfterRepairDebtStates');na=sm('v43AfterRepairActiveParentStates');agg={'markets':len(rows),'states':int(sm('v43StateCount')),'afterRepairDebtStates':int(n),'afterRepairActiveParentStates':int(na),'teacherExpandAfterRepairCount':int(sm('v43TeacherExpandAfterRepairCount')),'teacherExpandAfterRepairRate':sm('v43TeacherExpandAfterRepairCount')/n if n else 0.0,'teacherExpandActiveParentCount':int(sm('v43TeacherExpandActiveParentCount')),'teacherExpandActiveParentRate':sm('v43TeacherExpandActiveParentCount')/na if na else 0.0,'partialProgressEvents':int(sm('v38PartialProgressEvents')),'truthMismatch':sm('authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('overOwnedSubmitViolations'),'repairDrift':sm('repairToExpandAtFirstFill')}
  gates={'shadowSupport':agg['afterRepairDebtStates']>=3,'teacherSeesParallelExpandWindow':agg['teacherExpandAfterRepairCount']>0,'activeParentSupport':agg['afterRepairActiveParentStates']>0,'accountingUnchanged':agg['truthMismatch']==0 and agg['overOwned']==0 and agg['repairDrift']==0}
  out={'version':'ETH_REPAIR_V43C_REPAIR_NEXT_SPECIALIST_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'teacher':'TARGET_ETH_REPAIR_NEXT_RESPONSIBILITY_SPECIALIST_V42C','aggregate':agg,'gates':gates,'shadowPass':all(gates.values()),'rows':rows,'boundary':['strict-past actual OUR HFT fills reconstruct expansion-debt/repair-progress event memory','teacher score only after actual fills; no order behavior change','no winner/PnL feature','no threshold tuning; p>=0.5 only descriptive teacher majority','realistic HFT only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'shadowPass':out['shadowPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
