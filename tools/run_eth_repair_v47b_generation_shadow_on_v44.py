from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v44=sib('eth_v44_for_v47b','run_eth_repair_v44_coadapted_parallel_reexpand_causal.py');v38=v44.v38;EPS=1e-9
class V47B(v44.V44):
 def __init__(self,*a,genTeacher=None,**kw):
  super().__init__(*a,**kw);self.genTeacher=genTeacher;self.latestGenDebt=0.;self.latestGenPaid=0.;self.v47bShadow=[]
 def _coord_feature(self,t):
  f=super()._coord_feature(t);gp=f['gross']+1.;rem=max(0.,self.latestGenDebt-self.latestGenPaid);f.update({'latestGenerationDebtGross':self.latestGenDebt/gp,'latestGenerationRemainingGross':rem/gp,'latestGenerationRepairProgress':min(1.,self.latestGenPaid/(self.latestGenDebt+EPS)) if self.latestGenDebt>EPS else 1.});return f
 def _consume_new_fills(self):
  rows=self.authHist[self._coordFillSeen:]
  if not rows:return
  for x in rows:
   t=int(x['time']);side=str(x['side']).upper();q=float(x['shares']);preAbs=abs(self._coordU-self._coordD);preDebt=self._coordDebt
   if side=='UP':self._coordU+=q
   else:self._coordD+=q
   postAbs=abs(self._coordU-self._coordD);delta=postAbs-preAbs
   if delta>EPS:
    kind='EXPAND';self._coordDebt=max(0.,preDebt)+delta;self._coordLastExpandT=t;self.latestGenDebt=delta;self.latestGenPaid=0.
   elif delta<-EPS:
    kind='REPAIR';pay=min(max(0.,preDebt),-delta);self._coordDebt=max(0.,preDebt-pay);self._coordRepairedCum+=pay;self._coordLastRepairT=t;self.latestGenPaid=min(self.latestGenDebt,self.latestGenPaid+pay)
   else:kind='FLAT'
   if kind in ('EXPAND','REPAIR'):
    self._coordStreak=self._coordStreak+1 if kind==self._coordLastKind else 1;self._coordLastKind=kind;self._coordHist.append((t,kind,q));self._score_state(t,kind)
   if self._coordDebt<=EPS:self._coordRepairedCum=0.;self.latestGenDebt=self.latestGenPaid=0.
  self._coordFillSeen=len(self.authHist)
 def _score_state(self,t,after_kind):
  if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return
  f=self._coord_feature(t);xa=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pA=float(self.teacher['model'].predict_proba(xa)[0,1]);is_taker=any(e.get('event')=='V36_ACTIVE_SHARED_FILL' and int(e.get('t',-1))==int(t) for e in getattr(self,'activeEvents',[]));pG=None
  if self.genTeacher is not None:
   f['lastRepairWasTaker']=1.0 if is_taker else 0.0;xg=np.asarray([[float(f[c]) for c in self.genTeacher['features']]],np.float32);pG=float(self.genTeacher['model'].predict_proba(xg)[0,1])
  shadow={'t':int(t),'parentId':int(self.repairParent.get('id')),'progress':f['repairProgressFrac'],'latestGenerationRepairProgress':f['latestGenerationRepairProgress'],'latestGenerationDebtGross':f['latestGenerationDebtGross'],'debt':f['debt'],'floor':f['floor'],'lastRepairWasTaker':bool(is_taker),'v44PExpand':pA,'v47GenerationPExpand':pG,'v44WouldExpand':bool(pA>=.5),'v47WouldExpand':bool(pG is not None and pG>=.5)};self.v47bShadow.append(shadow)
  # Preserve V44 behavior exactly: action authority remains old EVENT_VALUE_NORM only.
  row={'t':int(t),'parentId':int(self.repairParent.get('id')),'progress':f['repairProgressFrac'],'debt':f['debt'],'floor':f['floor'],'pExpand':pA,'submit':False,'reason':'MODEL_REPAIR'}
  if pA<.5:self.v44Decisions.append(row);return
  if int(self.capEnd)-int(t)<=180000:self.v44LateBlocks+=1;row['reason']='LATE';self.v44Decisions.append(row);return
  if self._v44_unresolved(t):row['reason']='V44_CHILD_UNRESOLVED';self.v44Decisions.append(row);return
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):row['reason']='NO_THESIS';self.v44Decisions.append(row);return
  qv=v38.v36.v34.v30.v1.quotes(self.book)
  if not qv:row['reason']='NO_QUOTES';self.v44Decisions.append(row);return
  px=float(qv[side]['bid']);qty=1.0/px if px>EPS else 1e9
  if px<=EPS or qty>12.+EPS:row['reason']='VENUE_MIN_INFEASIBLE';self.v44Decisions.append(row);return
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n;self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V44_PARALLEL_SURPLUS';ok=self.submit(t,side,px,qty)
  if ok:
   k=f'{side}_{n0}';self.v44Keys.add(k);self.v44Submits+=1;row.update({'submit':True,'reason':'SUBMIT','key':k,'side':side,'price':px,'qty':qty})
  self.v44Decisions.append(row)
 def run_exam_v47b(self,models,winner):
  r=super().run_exam_v44(models,winner);r.update({'v47bGenerationShadow':self.v47bShadow[:160],'v47bShadowCount':len(self.v47bShadow),'v47bDisagreements':sum(bool(x['v44WouldExpand'])!=bool(x['v47WouldExpand']) for x in self.v47bShadow)});return r
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v47b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V47B','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V47B_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V47B(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:r=sim.run_exam_v47b(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'shadow':r['v47bShadowCount'],'disagree':r['v47bDisagreements'],'submits':r['v44Submits'],'fillQty':r['v44ActualFillQty']},ensure_ascii=False),flush=True)
  st=[s for x in rows for s in x['functional']['v47bGenerationShadow']];out={'version':'ETH_REPAIR_V47B_GENERATION_SHADOW_ON_V44','researchOnly':True,'behaviorChange':'V44_ONLY','generationTeacherActionAuthority':False,'aggregate':{'markets':len(rows),'states':len(st),'v44Expand':sum(x['v44WouldExpand'] for x in st),'v47Expand':sum(x['v47WouldExpand'] for x in st),'disagreements':sum(x['v44WouldExpand']!=x['v47WouldExpand'] for x in st),'latestGenerationLowProgressStates':sum(float(x['latestGenerationRepairProgress'])<.5 for x in st),'lowProgressV44Expand':sum(float(x['latestGenerationRepairProgress'])<.5 and x['v44WouldExpand'] for x in st),'lowProgressV47Expand':sum(float(x['latestGenerationRepairProgress'])<.5 and x['v47WouldExpand'] for x in st)},'rows':rows,'boundary':['V44 orders unchanged','V47 generation teacher shadow only','each actual Expand fill resets latest generation debt/progress','no threshold tuning','realistic HFT consumed Stage-A','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
 finally:
  stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
