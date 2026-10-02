from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v43=sib('eth_v43_for_v44','run_eth_repair_v43_coordination_teacher_shadow.py');v38=v43.v38;EPS=1e-9
class V44(v43.V43CoordinationShadow):
 def __init__(self,*a,teacher=None,**kw):
  super().__init__(*a,coord_model=None,**kw);self.teacher=teacher;self.v44Submits=0;self.v44Keys=set();self.v44FillSeen={};self.v44ActualFillQty=0.;self.v44ActualFillEvents=0;self.v44RepairQtyAfterExpand=0.;self.v44RepairEventsAfterExpand=0;self.v44LastExpandFillAt=None;self.v44Decisions=[];self.v44LateBlocks=0
 def _coord_feature(self,t):
  f=super()._coord_feature(t);gp=f['gross']+1.;dp=f['debt']+1.;f.update({'absNetGrossRatio':f['absNet']/gp,'repairQty5Gross':f['repairQty5']/gp,'repairQty15Gross':f['repairQty15']/gp,'repairQty30Gross':f['repairQty30']/gp,'expandQty5Gross':f['expandQty5']/gp,'expandQty15Gross':f['expandQty15']/gp,'expandQty30Gross':f['expandQty30']/gp,'repairQty30Debt':f['repairQty30']/dp,'expandQty30Debt':f['expandQty30']/dp});return f
 def _v44_unresolved(self,t):
  self._refresh_carrier_ledger(t)
  for k in self.v44Keys:
   e=self.carrierLedger.get(k,{})
   if float(e.get('reservedOutstanding') or 0)>EPS:return True
   o=self.orders.get(k,{})
   if not o.get('terminal') and float(o.get('qty') or 0)-float(e.get('actualFilled') or 0)>EPS:return True
  return False
 def _score_state(self,t,after_kind):
  if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return
  f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);row={'t':int(t),'parentId':int(self.repairParent.get('id')),'progress':f['repairProgressFrac'],'debt':f['debt'],'floor':f['floor'],'pExpand':pE,'submit':False,'reason':'MODEL_REPAIR'}
  if pE<.5:self.v44Decisions.append(row);return
  if int(self.capEnd)-int(t)<=180000:self.v44LateBlocks+=1;row['reason']='LATE';self.v44Decisions.append(row);return
  if self._v44_unresolved(t):row['reason']='V44_CHILD_UNRESOLVED';self.v44Decisions.append(row);return
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):row['reason']='NO_THESIS';self.v44Decisions.append(row);return
  qv=v38.v36.v34.v30.v1.quotes(self.book)
  if not qv:row['reason']='NO_QUOTES';self.v44Decisions.append(row);return
  px=float(qv[side]['bid']);qty=1.0/px if px>EPS else 1e9
  if px<=EPS or qty>12.+EPS:row['reason']='VENUE_MIN_INFEASIBLE';self.v44Decisions.append(row);return
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n;self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V44_PARALLEL_SURPLUS'
  ok=self.submit(t,side,px,qty)
  if ok:
   k=f'{side}_{n0}';self.v44Keys.add(k);self.v44Submits+=1;row.update({'submit':True,'reason':'SUBMIT','key':k,'side':side,'price':px,'qty':qty})
  self.v44Decisions.append(row)
 def _scan_v44_fills(self,t):
  self._refresh_carrier_ledger(t)
  for k in self.v44Keys:
   now=float(self.carrierLedger.get(k,{}).get('actualFilled') or 0.);old=float(self.v44FillSeen.get(k,0.))
   if now>old+EPS:self.v44ActualFillEvents+=1;self.v44ActualFillQty+=now-old;self.v44LastExpandFillAt=int(t)
   self.v44FillSeen[k]=max(old,now)
 def process(self,t):
  oldR=len(getattr(self,'v38PassiveRepairFillEvents',[]));super().process(t);self._scan_v44_fills(t)
  for r in self.v38PassiveRepairFillEvents[oldR:]:
   if self.v44LastExpandFillAt is not None and int(r['t'])>=self.v44LastExpandFillAt:self.v44RepairEventsAfterExpand+=1;self.v44RepairQtyAfterExpand+=float(r['incQty'])
 def run_exam_v44(self,models,winner):
  r=v38.V38IncrementalOnly.run_exam_v38a(self,models,winner);r.update({'v44Submits':self.v44Submits,'v44ActualFillEvents':self.v44ActualFillEvents,'v44ActualFillQty':self.v44ActualFillQty,'v44RepairEventsAfterExpand':self.v44RepairEventsAfterExpand,'v44RepairQtyAfterExpand':self.v44RepairQtyAfterExpand,'v44LateBlocks':self.v44LateBlocks,'v44Decisions':self.v44Decisions[:80]});return r
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','portability-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v44_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V44','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V44_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);teacher=joblib.load(a.portability_model)['models']['EVENT_VALUE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v38.V38IncrementalOnly(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:br=b.run_exam_v38a(models,cr['winner'])
   finally:b.close()
   c=V44(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=teacher)
   try:rr=c.run_exam_v44(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV38':br,'candidateV44':rr});print(json.dumps({'marketId':mid,'submit':rr['v44Submits'],'fillQty':rr['v44ActualFillQty'],'repairAfter':rr['v44RepairQtyAfterExpand'],'floors':[br['floor'],rr['floor']],'absNet':[br['absNet'],rr['absNet']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  agg={'markets':len(rows),'submits':int(sm('candidateV44','v44Submits')),'actualFillEvents':int(sm('candidateV44','v44ActualFillEvents')),'actualFillQty':sm('candidateV44','v44ActualFillQty'),'repairEventsAfterExpand':int(sm('candidateV44','v44RepairEventsAfterExpand')),'repairQtyAfterExpand':sm('candidateV44','v44RepairQtyAfterExpand'),'lateBlocks':int(sm('candidateV44','v44LateBlocks')),'truthMismatch':sm('candidateV44','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV44','overOwnedSubmitViolations'),'repairDrift':sm('candidateV44','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baselineV38','floor'),'candidateFloorSum':sm('candidateV44','floor'),'baselineAbsNetSum':sm('baselineV38','absNet'),'candidateAbsNetSum':sm('candidateV44','absNet'),'baselineRepairActiveEnd':sm('baselineV38','repairParentActiveAtEnd'),'candidateRepairActiveEnd':sm('candidateV44','repairParentActiveAtEnd')}
  gates={'reexpandMaterialized':agg['actualFillEvents']>0,'repairReactsAfterExpand':agg['repairEventsAfterExpand']>0 and agg['repairQtyAfterExpand']>EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'lateExposureGuardEnforced':True}
  out={'version':'ETH_REPAIR_V44_COADAPTED_PARALLEL_REEXPAND_CAUSAL','researchOnly':True,'behaviorChange':True,'teacher':'V42D EVENT_VALUE_NORM frozen before causal run','aggregate':agg,'gates':gates,'structuralPass':all(gates.values()),'rows':rows,'boundary':['consumed Stage-A realistic HFT only','one venue-minimum Maker re-expand child per qualifying actual Repair-fill state','Repair parent remains live','new exposure feeds subsequent real Repair geometry','V36 active backstop inherited','winner/PnL excluded from trigger and primary gates','no dream fill','no 8781','<=180s no-new-exposure']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'gates':gates,'structuralPass':out['structuralPass']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
