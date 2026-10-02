from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,os,sys,math,threading,time,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_v30_path=Path(__file__).with_name('run_eth_repair_functional_exam_v30_directional_thesis_cycle.py')
_spec=importlib.util.spec_from_file_location('eth_v30_staged',_v30_path)
if _spec is None or _spec.loader is None: raise ImportError(f'cannot load V30 from {_v30_path}')
v30=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=v30;_spec.loader.exec_module(v30)
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class V34ReadinessShadow(v30.DirectionalThesisCycleSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.repairChurn=[];self._churnKeys=set();self.softArms=[];self._armedParents=set();self.recoveredParents=set();self._lastParentId=None;self.parentLedger={}
 def _maybe_birth_parent(self,t,weak,cap):
  before=int(self.repairParent.get('id')) if self.repairParent is not None else None
  out=super()._maybe_birth_parent(t,weak,cap)
  rp=self.repairParent
  if rp is not None:
   pid=int(rp.get('id'))
   if pid!=before and pid not in self.parentLedger:
    rb=self.reserveBuilder
    self.parentLedger[pid]={'parentId':pid,'bornAt':int(rp.get('bornAt') or t),'side':rp.get('side'),'thesisId':(rb.get('thesisId') if rb else None),'reserveFirstFillAt':(rb.get('firstFillAt') if rb else None),'completed':False,'completedAt':None,'completionKind':None,'armed':False,'armAt':None,'churnCount':0}
  return out
 def _complete_parent_if_structural(self,t,ai):
  before=int(self.repairParent.get('id')) if self.repairParent is not None else None
  payoff_before=int(self.payoffRepairCompletions)
  base_complete_before=int(self.repairParentCompletions)
  out=super()._complete_parent_if_structural(t,ai)
  if before is not None and (self.repairParent is None or int(self.repairParent.get('id'))!=before):
   z=self.parentLedger.setdefault(before,{'parentId':before,'bornAt':None,'side':None,'completed':False,'armed':False,'churnCount':0})
   z['completed']=True;z['completedAt']=int(t);z['completionKind']='PAYOFF_RECOVERED' if int(self.payoffRepairCompletions)>payoff_before else ('STRUCTURAL_COMPLETION' if int(self.repairParentCompletions)>base_complete_before else 'OTHER_DISAPPEAR')
  return out
 def _cancel_key(self,t,key):
  e=self.carrierLedger.get(key,{}) if hasattr(self,'carrierLedger') else {}
  role=e.get('objectiveRole') or self.orders.get(key,{}).get('objective_role');pid=e.get('parentId')
  try:floor=float(self._raw_floor()[0])
  except Exception:floor=0.0
  ok=super()._cancel_key(t,key)
  if ok and role=='REPAIR' and pid is not None and floor<-EPS and key not in self._churnKeys:
   self._churnKeys.add(key);self.repairChurn.append({'t':int(t),'key':key,'parentId':int(pid),'side':e.get('side') or self.orders.get(key,{}).get('side'),'floor':floor});self.parentLedger.setdefault(int(pid),{'parentId':int(pid),'bornAt':None,'side':e.get('side'),'completed':False,'armed':False,'churnCount':0})['churnCount']+=1
  return ok
 def _arm_diag(self,t):
  rp=self.repairParent
  if rp is None:return None
  pid=int(rp.get('id')); side=rp.get('side')
  if pid in self._armedParents or side not in ('UP','DOWN'):return None
  churn=[x for x in self.repairChurn if int(x['parentId'])==pid]
  if not churn:return None
  floor,u,d,cost=self._raw_floor();floor=float(floor);u=float(u);d=float(d);cost=float(cost)
  if floor>=-EPS:return None
  gap=abs(u-d)
  if gap<=EPS:return None
  strong=max(u,d);cap=(strong-cost)/gap
  qv=v1.quotes(self.book)
  if not qv or side not in qv or qv[side].get('ask') is None:return None
  ask=float(qv[side]['ask']);bid=float(qv[side]['bid']) if qv[side].get('bid') is not None else None;askgap=ask-cap;frontierGap=(bid-cap) if bid is not None else None
  if askgap<=EPS:return None
  unresolved=sum(float(rem) for _,_,rem in self.lane_unresolved('REPAIR'))
  legal=1.0/ask if ask>EPS else math.inf
  q=min(legal,gap,unresolved)
  legalSlice=bool(q>EPS and q+EPS>=legal)
  floorAfter=None
  if legalSlice:
   uu=u+q if side=='UP' else u;dd=d+q if side=='DOWN' else d;cc=cost+ask*q;floorAfter=min(uu,dd)-cc
  wend=int(self.payload.get('market',{}).get('window_end_ms') or 0)
  ev={'event':'SOFT_ARM_SHADOW','t':int(t),'parentId':pid,'side':side,'churnCount':len(churn),'firstChurnAt':int(churn[0]['t']),'floor':floor,'upShares':u,'downShares':d,'cost':cost,'absNet':gap,'economicRepairCeiling':cap,'liveBid':bid,'liveAsk':ask,'askGap':askgap,'ceilingFrontierGap':frontierGap,'ceilingBehindBestBid':bool(frontierGap is not None and frontierGap>EPS),'unresolvedRepairQty':unresolved,'minSliceLegalQty':legal,'shadowQty':q if legalSlice else 0.0,'legalMinSlice':legalSlice,'counterfactualFloorAfterMinSlice':floorAfter,'counterfactualFloorImprovement':(floorAfter-floor) if floorAfter is not None else None,'secondsLeft':(wend-int(t))/1000.0 if wend else None}
  self.softArms.append(ev);self._armedParents.add(pid);z=self.parentLedger.setdefault(pid,{'parentId':pid,'bornAt':None,'side':side,'completed':False,'armed':False,'churnCount':len(churn)});z['armed']=True;z['armAt']=int(t);z['armAskGap']=askgap;z['armFloor']=floor;z['armSecondsLeft']=ev['secondsLeft'];return ev
 def process(self,t):
  pid_before=int(self.repairParent.get('id')) if self.repairParent is not None else None; rec_before=self.payoffRepairCompletions
  super().process(t)
  if self.payoffRepairCompletions>rec_before and pid_before is not None:self.recoveredParents.add(pid_before)
  self._arm_diag(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._arm_diag(t)
 def run_exam_v34(self,models,winner):
  r=super().run_exam_v30(models,winner)
  arms=[]
  for a in self.softArms:
   z=dict(a);z['eventualPayoffRecovered']=int(a['parentId']) in self.recoveredParents;arms.append(z)
  for pid,z in self.parentLedger.items():
   z['terminalUnresolved']=bool(not z.get('completed'))
   z['passiveCompleted']=bool(z.get('completed'))
   z['armedAndPassiveCompleted']=bool(z.get('armed') and z.get('completed'))
   z['armedAndTerminalUnresolved']=bool(z.get('armed') and not z.get('completed'))
  led=sorted(self.parentLedger.values(),key=lambda x:int(x.get('parentId') or 0))
  r.update({'v34RepairChurnEvents':self.repairChurn[:100],'v34SoftArmEvents':arms[:100],'v34SoftArmCount':len(arms),'v34ArmedRecovered':sum(x['eventualPayoffRecovered'] for x in arms),'v34ArmedFailed':sum(not x['eventualPayoffRecovered'] for x in arms),'v34ParentLedger':led,'v34ParentBirthsLogged':len(led),'v34ParentPassiveCompleted':sum(bool(x.get('completed')) for x in led),'v34ParentTerminalUnresolved':sum(not bool(x.get('completed')) for x in led),'v34ArmedPassiveCompleted':sum(bool(x.get('armedAndPassiveCompleted')) for x in led),'v34ArmedTerminalUnresolved':sum(bool(x.get('armedAndTerminalUnresolved')) for x in led)})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v34_shadow_'))
 stop=threading.Event()
 def heartbeat():
  while not stop.wait(10): print(json.dumps({'heartbeat':'V34_LOAD_OR_REPLAY','ts':time.time()},ensure_ascii=False),flush=True)
 threading.Thread(target=heartbeat,daemon=True).start();print(json.dumps({'heartbeat':'V34_START'},ensure_ascii=False),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v30.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V34ReadinessShadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v34(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'arms':r['v34SoftArmCount'],'armedRecovered':r['v34ArmedRecovered'],'armedFailed':r['v34ArmedFailed'],'recoveries':r['payoffRepairCompletions'],'floor':r['floor'],'armEvents':r['v34SoftArmEvents'][:3]},ensure_ascii=False),flush=True)
  agg={'markets':len(rows),'softArms':sum(x['functional']['v34SoftArmCount'] for x in rows),'armedRecovered':sum(x['functional']['v34ArmedRecovered'] for x in rows),'armedFailed':sum(x['functional']['v34ArmedFailed'] for x in rows),'legalMinSliceArms':sum(sum(bool(e['legalMinSlice']) for e in x['functional']['v34SoftArmEvents']) for x in rows)}
  out={'version':'ETH_REPAIR_V34C_CEILING_FRONTIER_READINESS_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'aggregate':agg,'rows':rows,'boundary':['frozen V30 behavior unchanged','event-based ETH carrier replacement; no BTC time threshold','askGap zero boundary from OUR payoff geometry','SOFT_ARM only; no Taker submit','winner/PnL excluded','no 8781']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False))
 finally:
  stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
