from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v44=sib('eth_v44_for_v46','run_eth_repair_v44_coadapted_parallel_reexpand_causal.py');v38=v44.v38;EPS=1e-9
class V46(v44.V44):
 def _score_state(self,t,after_kind):
  if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return
  f=self._coord_feature(t);is_taker=any(e.get('event')=='V36_ACTIVE_SHARED_FILL' and int(e.get('t',-1))==int(t) for e in getattr(self,'activeEvents',[]));f['lastRepairWasTaker']=1.0 if is_taker else 0.0
  x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);row={'t':int(t),'parentId':int(self.repairParent.get('id')),'progress':f['repairProgressFrac'],'debt':f['debt'],'floor':f['floor'],'lastRepairWasTaker':bool(is_taker),'pExpand':pE,'submit':False,'reason':'MODEL_REPAIR'}
  if pE<.5:self.v44Decisions.append(row);return
  if int(self.capEnd)-int(t)<=180000:self.v44LateBlocks+=1;row['reason']='LATE';self.v44Decisions.append(row);return
  if self._v44_unresolved(t):row['reason']='V44_CHILD_UNRESOLVED';self.v44Decisions.append(row);return
  th=getattr(self,'thesis',None);side=th.get('side') if th else None
  if side not in ('UP','DOWN'):row['reason']='NO_THESIS';self.v44Decisions.append(row);return
  qv=v38.v36.v34.v30.v1.quotes(self.book)
  if not qv:row['reason']='NO_QUOTES';self.v44Decisions.append(row);return
  px=float(qv[side]['bid']);qty=1.0/px if px>EPS else 1e9
  if px<=EPS or qty>12.+EPS:row['reason']='VENUE_MIN_INFEASIBLE';self.v44Decisions.append(row);return
  oid=self._new_objective('EXPAND',side)['id'];n0=self.n;self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V46_ROLE_AWARE_PARALLEL_SURPLUS';ok=self.submit(t,side,px,qty)
  if ok:
   k=f'{side}_{n0}';self.v44Keys.add(k);self.v44Submits+=1;row.update({'submit':True,'reason':'SUBMIT','key':k,'side':side,'price':px,'qty':qty})
  self.v44Decisions.append(row)
 def run_exam_v46(self,models,winner):
  r=super().run_exam_v44(models,winner);r['v46RoleAwareDecisions']=r.pop('v44Decisions',[]);return r
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','role-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v46_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V46','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V46_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);teacher=joblib.load(a.role_model)['models']['ROLE_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v38.V38IncrementalOnly(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:br=b.run_exam_v38a(models,cr['winner'])
   finally:b.close()
   c=V46(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=teacher)
   try:rr=c.run_exam_v46(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV38':br,'candidateV46':rr});print(json.dumps({'marketId':mid,'submit':rr['v44Submits'],'fillQty':rr['v44ActualFillQty'],'repairAfter':rr['v44RepairQtyAfterExpand'],'floors':[br['floor'],rr['floor']],'absNet':[br['absNet'],rr['absNet']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  harmful=beneficial=unchanged=0
  for x in rows:
   d=float(x['candidateV46']['floor'])-float(x['baselineV38']['floor'])
   if d<-EPS:harmful+=1
   elif d>EPS:beneficial+=1
   else:unchanged+=1
  agg={'markets':len(rows),'submits':int(sm('candidateV46','v44Submits')),'actualFillEvents':int(sm('candidateV46','v44ActualFillEvents')),'actualFillQty':sm('candidateV46','v44ActualFillQty'),'repairEventsAfterExpand':int(sm('candidateV46','v44RepairEventsAfterExpand')),'repairQtyAfterExpand':sm('candidateV46','v44RepairQtyAfterExpand'),'truthMismatch':sm('candidateV46','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV46','overOwnedSubmitViolations'),'repairDrift':sm('candidateV46','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baselineV38','floor'),'candidateFloorSum':sm('candidateV46','floor'),'floorDelta':sm('candidateV46','floor')-sm('baselineV38','floor'),'baselineAbsNetSum':sm('baselineV38','absNet'),'candidateAbsNetSum':sm('candidateV46','absNet'),'beneficialMarkets':beneficial,'harmfulMarkets':harmful,'unchangedMarkets':unchanged}
  gates={'reexpandMaterialized':agg['actualFillEvents']>0,'repairReactsAfterExpand':agg['repairEventsAfterExpand']>0 and agg['repairQtyAfterExpand']>EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
  out={'version':'ETH_REPAIR_V46_ROLE_AWARE_COADAPTED_REEXPAND_CAUSAL','researchOnly':True,'behaviorChange':True,'teacher':'V45 ROLE_AWARE_NORM frozen before causal run','aggregate':agg,'gates':gates,'structuralPass':all(gates.values()),'rows':rows,'boundary':['same consumed Stage-A realistic HFT','single change from V44 is role-aware teacher','actual V36 active fill defines lastRepairWasTaker','venue-minimum Maker re-expand child','Repair parent remains live','no threshold tuning','no winner/PnL trigger','no dream fill','no 8781','<=180s no-new-exposure']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'gates':gates,'structuralPass':out['structuralPass']},ensure_ascii=False),flush=True)
 finally:
  stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
