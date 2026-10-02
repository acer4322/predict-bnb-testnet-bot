from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(file)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v39=sib('eth_v39_for_v40','run_eth_repair_v39_persistent_thesis_surplus_recycle.py')
v38=v39.v38; v36=v38.v36; v1=v39.v1; EPS=1e-9

class V40JointExecutable(v39.V39PersistentThesisRecycle):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v40JointEligible=0;self.v40JointCapEvents=0;self.v40JointFullNeedFallback=0;self.v40JointRows=[]
 def _repair_payoff_budget(self,p):
  b=v36.V36EventConfirmedActive._repair_payoff_budget(self,p)
  if not b or not b.get('feasible'):return b
  deficit=float(b.get('deficit') or 0.0)
  if deficit<=EPS:return b
  old=float(b.get('qty') or 0.0);rlegal=float(b.get('legal') or 0.0);room=float(b.get('room') or 0.0)
  if old<=EPS or rlegal<=EPS or rlegal>room+EPS:return b
  target=rlegal;ep=None;pair=None;joint=False
  try:
   qv=v1.quotes(self.book);side=self.thesis.get('side') if self.thesis else None
   if qv and side in ('UP','DOWN') and qv.get(side,{}).get('bid') is not None:
    ep=float(qv[side]['bid'])
    if ep>EPS and ep<1.0-EPS:
     pair=float(p)+ep
     if pair<1.0-EPS:
      joint=True;self.v40JointEligible+=1;target=max(rlegal,1.0/ep)
  except Exception:pass
  qty=min(old,target)
  z=dict(b);z['fullPayoffQtyBeforeIncrementalCap']=old;z['jointExecutable']=joint;z['jointThesisBid']=ep;z['jointPairSum']=pair;z['jointTargetQty']=target
  if qty<old-EPS:
   z['qty']=qty;z['incrementalVenueMinTranche']=True;self.v38IncrementalQtyCapEvents+=1;self.v38FullNeedQtySaved+=old-qty;self.v40JointCapEvents+=1
  else:
   z['qty']=old
   if joint and target>old+EPS:self.v40JointFullNeedFallback+=1
  if joint and len(self.v40JointRows)<120:self.v40JointRows.append({'repairPrice':float(p),'thesisBid':ep,'pairSum':pair,'repairLegal':rlegal,'thesisLegal':1.0/ep if ep else None,'fullNeedQty':old,'chosenQty':float(z['qty']),'room':room})
  return z
 def run_exam_v40(self,models,winner):
  r=super().run_exam_v39(models,winner);r.update({'v40JointEligible':self.v40JointEligible,'v40JointCapEvents':self.v40JointCapEvents,'v40JointFullNeedFallback':self.v40JointFullNeedFallback,'v40JointRows':self.v40JointRows});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v40_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V40_RUN','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V40_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort}
  models,life,cap,tim,econ,price,sur=v36.v34.v30.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v39.V39PersistentThesisRecycle(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:br=b.run_exam_v39(models,cr['winner'])
   finally:b.close()
   s=V40JointExecutable(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:ar=s.run_exam_v40(models,cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'v39':br,'v40':ar})
   print(json.dumps({'marketId':mid,'jointEligible':ar['v40JointEligible'],'jointCaps':ar['v40JointCapEvents'],'partial':ar['v38PartialProgressEvents'],'mint':ar['v37CreditMintedQty'],'submits':ar['v37SurplusSubmitCount'],'fills':ar['v37SurplusFillQty'],'gain':ar['v37SurplusMatchedGain'],'floor39':br['floor'],'floor40':ar['floor'],'abs39':br['absNet'],'abs40':ar['absNet']},ensure_ascii=False),flush=True)
  def sm(side,key):return sum(float(x[side].get(key) or 0) for x in rows)
  agg={'markets':len(rows),'jointEligible':int(sm('v40','v40JointEligible')),'jointCapEvents':int(sm('v40','v40JointCapEvents')),'partialProgressEvents':int(sm('v40','v38PartialProgressEvents')),'creditMintedQty':sm('v40','v37CreditMintedQty'),'surplusSubmits':int(sm('v40','v37SurplusSubmitCount')),'surplusFillQty':sm('v40','v37SurplusFillQty'),'surplusMatchedGain':sm('v40','v37SurplusMatchedGain'),'nonPositivePairFillQty':sm('v40','v37NonPositivePairFillQty'),'creditOverspend':sm('v40','v37CreditOverspend'),'crossParentCreditLeak':int(sm('v40','v37CrossParentCreditLeak')),'lateSurplusFillAfterParentCompletion':sm('v40','v37LateSurplusFillAfterParentCompletion'),'truthMismatch':sm('v40','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('v40','overOwnedSubmitViolations'),'repairDrift':sm('v40','repairToExpandAtFirstFill'),'v39FloorSum':sm('v39','floor'),'v40FloorSum':sm('v40','floor'),'v39AbsNetSum':sm('v39','absNet'),'v40AbsNetSum':sm('v40','absNet')}
  gates={'cycleExercised':agg['surplusFillQty']>EPS,'positiveMatchedEconomics':agg['nonPositivePairFillQty']<=EPS and agg['surplusMatchedGain']>EPS,'partialProgressRetained':agg['partialProgressEvents']>0,'zeroCreditOverspend':agg['creditOverspend']<=1e-7,'zeroCrossParentLeak':agg['crossParentCreditLeak']==0,'zeroLateFill':agg['lateSurplusFillAfterParentCompletion']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'floorNotWorseVsV39':agg['v40FloorSum']>=agg['v39FloorSum']-1e-7}
  out={'version':'ETH_REPAIR_V40_JOINT_EXECUTABLE_REPAIR_SURPLUS_TRANCHE','researchOnly':True,'aggregate':agg,'gates':gates,'stageAPass':all(gates.values()),'rows':rows,'boundary':['joint Repair tranche=max(repair legal min, current thesis-side legal min) only when repairPrice+thesisBid<1, clipped to full payoff need','never exceeds full payoff need/responsibility room','persistent thesis recycle from V39','actual Repair fills mint credit','no threshold sweep','no winner/PnL gate','realistic HFT only','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':all(gates.values()),'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
