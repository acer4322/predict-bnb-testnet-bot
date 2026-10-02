from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from collections import deque
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v43=sib('eth_v43_for_v48b','run_eth_repair_v43_coordination_teacher_shadow.py');v38=v43.v38;EPS=1e-9
class V48B(v43.V43CoordinationShadow):
 def __init__(self,*a,models48=None,**kw):
  super().__init__(*a,coord_model=None,**kw);self.models48=models48;self.v48bStates=[];self.gLastExpandPrice=0.;self.gLastExpandQty=0.;self.gLastExpandCost=0.;self.gDebtAfterExpand=0.;self.gRepairGain=0.;self.gRepairQty=0.;self.gRepairCost=0.;self.gRepairCount=0;self.gLastRepairPrice=0.;self.gLastRepairWasTaker=0.
 def _coord_feature(self,t):
  f=super()._coord_feature(t);gp=f['gross']+1.;dp=f['debt']+1.;f.update({'absNetGrossRatio':f['absNet']/gp,'repairQty5Gross':f['repairQty5']/gp,'repairQty15Gross':f['repairQty15']/gp,'repairQty30Gross':f['repairQty30']/gp,'expandQty5Gross':f['expandQty5']/gp,'expandQty15Gross':f['expandQty15']/gp,'expandQty30Gross':f['expandQty30']/gp,'repairQty30Debt':f['repairQty30']/dp,'expandQty30Debt':f['expandQty30']/dp,'lastRepairWasTaker':self.gLastRepairWasTaker,'genRepairFloorGainToExpandCost':self.gRepairGain/(self.gLastExpandCost+EPS) if self.gLastExpandCost>EPS else 0.,'genRepairQtyToExpandQty':self.gRepairQty/(self.gLastExpandQty+EPS) if self.gLastExpandQty>EPS else 0.,'genDebtPaidFrac':max(0.,min(2.,(self.gDebtAfterExpand-f['debt'])/(self.gDebtAfterExpand+EPS))) if self.gDebtAfterExpand>EPS else 0.,'genRepairCostToExpandCost':self.gRepairCost/(self.gLastExpandCost+EPS) if self.gLastExpandCost>EPS else 0.,'lastExpandPrice':self.gLastExpandPrice,'lastRepairPrice':self.gLastRepairPrice,'genRepairCount':min(self.gRepairCount,12)});return f
 def _score_state(self,t,after_kind):
  if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.models48 is None:return
  f=self._coord_feature(t);row={'t':int(t),'parentId':int(self.repairParent.get('id')),'progress':f['repairProgressFrac'],'debt':f['debt'],'floor':f['floor'],'lastRepairWasTaker':bool(self.gLastRepairWasTaker),'genRepairFloorGainToExpandCost':f['genRepairFloorGainToExpandCost'],'genRepairQtyToExpandQty':f['genRepairQtyToExpandQty'],'genDebtPaidFrac':f['genDebtPaidFrac'],'lastExpandPrice':f['lastExpandPrice'],'lastRepairPrice':f['lastRepairPrice'],'genRepairCount':f['genRepairCount']}
  for name in ('ROLE_AWARE_EVENT_NORM','GEN_ECON_NORM'):
   z=self.models48[name];x=np.asarray([[float(f[c]) for c in z['features']]],np.float32);row[name]=float(z['model'].predict_proba(x)[0,1])
  self.v48bStates.append(row)
 def _consume_new_fills(self):
  rows=self.authHist[self._coordFillSeen:]
  if not rows:return
  for x in rows:
   t=int(x['time']);side=str(x['side']).upper();q=float(x['shares']);price=float(x['price']);preAbs=abs(self._coordU-self._coordD);preDebt=self._coordDebt
   if side=='UP':self._coordU+=q
   else:self._coordD+=q
   postAbs=abs(self._coordU-self._coordD);delta=postAbs-preAbs
   if delta>EPS:
    kind='EXPAND';self._coordDebt=max(0.,preDebt)+delta;self._coordLastExpandT=t;self.gLastExpandPrice=price;self.gLastExpandQty=delta;self.gLastExpandCost=price*delta;self.gDebtAfterExpand=self._coordDebt;self.gRepairGain=0.;self.gRepairQty=0.;self.gRepairCost=0.;self.gRepairCount=0
   elif delta<-EPS:
    kind='REPAIR';pay=min(max(0.,preDebt),-delta);self._coordDebt=max(0.,preDebt-pay);self._coordRepairedCum+=pay;self._coordLastRepairT=t;is_taker=any(e.get('event')=='V36_ACTIVE_SHARED_FILL' and int(e.get('t',-1))==t for e in getattr(self,'activeEvents',[]));self.gLastRepairWasTaker=1. if is_taker else 0.;self.gLastRepairPrice=price;self.gRepairGain+=pay*max(0.,1.-price);self.gRepairQty+=pay;self.gRepairCost+=pay*price;self.gRepairCount+=1
   else:kind='FLAT'
   if kind in ('EXPAND','REPAIR'):
    self._coordStreak=self._coordStreak+1 if kind==self._coordLastKind else 1;self._coordLastKind=kind;self._coordHist.append((t,kind,q));self._score_state(t,kind)
   if self._coordDebt<=EPS:self._coordRepairedCum=0.
  self._coordFillSeen=len(self.authHist)
 def run_exam_v48b(self,models,winner):
  r=v38.V38IncrementalOnly.run_exam_v38a(self,models,winner);r.update({'v48bStates':self.v48bStates[:120],'v48bStateCount':len(self.v48bStates)});return r
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','generation-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v48b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V48B','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V48B_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);allm=joblib.load(a.generation_model)['models'];mm={name:allm[('ETH',name)] for name in ('ROLE_AWARE_EVENT_NORM','GEN_ECON_NORM')};rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V48B(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,models48=mm)
   try:r=sim.run_exam_v48b(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'states':r['v48bStateCount']}),flush=True)
  st=[s for r in rows for s in r['functional']['v48bStates']]
  def sm(k):
   if not st:return {'n':0,'expand':0,'rate':0.,'median':None}
   a=sorted(float(x[k]) for x in st);return {'n':len(st),'expand':sum(float(x[k])>=.5 for x in st),'rate':sum(float(x[k])>=.5 for x in st)/len(st),'median':a[len(a)//2]}
  out={'version':'ETH_REPAIR_V48B_GENERATION_ECONOMIC_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'aggregate':{'states':len(st),'roleAware':sm('ROLE_AWARE_EVENT_NORM'),'genEcon':sm('GEN_ECON_NORM')},'rows':rows,'boundary':['consumed Stage-A realistic HFT only','generation economics from actual realized OUR fills only','V38 behavior unchanged','no winner/PnL feature','0.5 is model majority descriptive only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
