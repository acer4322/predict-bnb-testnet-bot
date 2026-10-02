from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v57=sib('eth_v57_for_v58','run_eth_repair_v57_one_shot_passive_remainder_active_child.py');v56=v57.v56;v38=v57.v38;v1=v57.v1;EPS=1e-9

class V58ParentGlobalLedger(v57.V57OneShotActive):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.v58Parents={};self.v58Events=[];self.v58ActiveBlocks=0;self.v58ActiveAllowed=0;self.v58SameParentSecondActiveBlocks=0;self.v58ParentWorstCaseOverRoot=0.;self.v58LateFillDoublePayViolation=0.;self.v58ParentActiveEver=set()
 def _parent_realized(self,pid):
  s=0.
  for e in getattr(self,'carrierLedger',{}).values():
   try:
    if int(e.get('parentId'))==int(pid) and e.get('objectiveRole')=='REPAIR':s+=float(e.get('actualFilled') or 0.)
   except Exception:pass
  return s
 def _parent_inflight(self,pid):
  passive=active=0.;rows=[]
  for k,e in getattr(self,'carrierLedger',{}).items():
   try:
    if int(e.get('parentId'))!=int(pid) or e.get('objectiveRole')!='REPAIR':continue
   except Exception:continue
   rem=float(self._ledger_remaining(e)) if hasattr(self,'_ledger_remaining') else (0. if e.get('terminalConfirmed') else max(0.,float(e.get('submittedQty') or 0.)-float(e.get('actualFilled') or 0.)))
   if rem<=EPS:continue
   lane=str(e.get('lane') or '')
   is_active=lane.startswith('ACTIVE_') or 'TAKER_ACTIVE' in str(self.orders.get(k,{}).get('execution_role') or '')
   if is_active:active+=rem
   else:passive+=rem
   rows.append({'key':k,'lane':lane,'remaining':rem,'cancelRequested':bool(e.get('cancelRequested')),'terminalConfirmed':bool(e.get('terminalConfirmed'))})
  return passive,active,rows
 def _global_state(self,t,pid):
  self._refresh_carrier_ledger(t);pay=self._current_payoffs();real=self._parent_realized(pid);root_now=max(0.,float(pay.get('gap') or 0.))+real
  st=self.v58Parents.get(pid)
  if st is None:
   st={'parentId':int(pid),'rootResponsibilityQty':root_now,'bornObservedAt':int(t),'maxWorstCaseOwned':0.,'activeSubmits':0};self.v58Parents[pid]=st
  elif root_now>float(st['rootResponsibilityQty'])+EPS:
   old=float(st['rootResponsibilityQty']);st['rootResponsibilityQty']=root_now;self.v58Events.append({'t':int(t),'event':'PARENT_GLOBAL_ROOT_INCREASE','parentId':int(pid),'oldRoot':old,'newRoot':root_now})
  passive,active,carriers=self._parent_inflight(pid);root=float(st['rootResponsibilityQty']);owned=real+passive+active;over=max(0.,owned-root);st['maxWorstCaseOwned']=max(float(st.get('maxWorstCaseOwned') or 0.),owned);self.v58ParentWorstCaseOverRoot=max(self.v58ParentWorstCaseOverRoot,over)
  return {'parentId':int(pid),'rootResponsibilityQty':root,'realizedPaidQty':real,'passiveInFlightQty':passive,'activeInFlightQty':active,'worstCaseOwnedQty':owned,'remainingAuthorizableQty':max(0.,root-owned),'overRootQty':over,'carriers':carriers,'payoffGap':float(pay.get('gap') or 0.)}
 def _submit_active(self,t,pid,passive_key,side,ask,q,objective_id):
  st=self._global_state(t,pid);legal=1.0/float(ask) if float(ask)>EPS else math.inf
  if int(pid) in self.v58ParentActiveEver:
   self.v58SameParentSecondActiveBlocks+=1;self.v58ActiveBlocks+=1;self.v58Events.append({'t':int(t),'event':'PARENT_GLOBAL_SECOND_ACTIVE_BLOCK','parentId':int(pid),'requestedQty':float(q),**{k:st[k] for k in ['rootResponsibilityQty','realizedPaidQty','passiveInFlightQty','activeInFlightQty','remainingAuthorizableQty']}});return False
  available=float(st['remainingAuthorizableQty'])
  if not math.isfinite(legal) or available+EPS<legal:
   self.v58ActiveBlocks+=1;self.v58Events.append({'t':int(t),'event':'PARENT_GLOBAL_INFLIGHT_BLOCK','parentId':int(pid),'requestedQty':float(q),'activeLegalMin':legal,**{k:st[k] for k in ['rootResponsibilityQty','realizedPaidQty','passiveInFlightQty','activeInFlightQty','worstCaseOwnedQty','remainingAuthorizableQty']},'passiveKey':passive_key});return False
  q2=min(float(q),available)
  if q2+EPS<legal:
   self.v58ActiveBlocks+=1;return False
  ok=super()._submit_active(t,pid,passive_key,side,ask,q2,objective_id)
  if ok:
   self.v58ParentActiveEver.add(int(pid));self.v58ActiveAllowed+=1;self.v58Parents[int(pid)]['activeSubmits']=int(self.v58Parents[int(pid)].get('activeSubmits') or 0)+1;self.v58Events.append({'t':int(t),'event':'PARENT_GLOBAL_ACTIVE_RESERVED','parentId':int(pid),'qty':q2,'activeLegalMin':legal,**{k:st[k] for k in ['rootResponsibilityQty','realizedPaidQty','passiveInFlightQty','activeInFlightQty','remainingAuthorizableQty']},'passiveKey':passive_key})
  return ok
 def _reconcile_active(self,t):
  super()._reconcile_active(t)
  self._refresh_carrier_ledger(t)
  for pid in list(self.v58Parents):
   st=self._global_state(t,pid)
   if st['overRootQty']>EPS:
    self.v58LateFillDoublePayViolation=max(self.v58LateFillDoublePayViolation,float(st['overRootQty']))
    self.v58Events.append({'t':int(t),'event':'PARENT_GLOBAL_REALIZED_OR_INFLIGHT_OVER_ROOT','parentId':int(pid),**{k:st[k] for k in ['rootResponsibilityQty','realizedPaidQty','passiveInFlightQty','activeInFlightQty','worstCaseOwnedQty','overRootQty']}})
 def run_exam_v58(self,models,winner):
  r=super().run_exam_v57(models,winner)
  for pid in list(self.v58Parents):self._global_state(int(self.capEnd),pid)
  led=[{**{k:v for k,v in st.items() if k!='carriers'}} for _,st in sorted(self.v58Parents.items())]
  r.update({'v58ParentLedgers':led,'v58ActiveBlocks':self.v58ActiveBlocks,'v58ActiveAllowed':self.v58ActiveAllowed,'v58SameParentSecondActiveBlocks':self.v58SameParentSecondActiveBlocks,'v58ParentWorstCaseOverRoot':self.v58ParentWorstCaseOverRoot,'v58LateFillDoublePayViolation':self.v58LateFillDoublePayViolation,'v58Events':self.v58Events[:240]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','hazard-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v58_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V58','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V58_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];haz=joblib.load(a.hazard_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v57.V57OneShotActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,hazard_art=haz)
   try:br=b.run_exam_v57(models,cr['winner'])
   finally:b.close()
   c=V58ParentGlobalLedger(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,hazard_art=haz)
   try:rr=c.run_exam_v58(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV57':br,'candidateV58':rr});print(json.dumps({'marketId':mid,'v57Submit':br['v57Submits'],'v58Submit':rr['v57Submits'],'globalAllowed':rr['v58ActiveAllowed'],'globalBlocks':rr['v58ActiveBlocks'],'secondBlocks':rr['v58SameParentSecondActiveBlocks'],'overRoot':rr['v58ParentWorstCaseOverRoot'],'respOverfill':[br['v51ResponsibilityOverfill'],rr['v51ResponsibilityOverfill']],'sharedOverfill':[br['v36SharedRealizedOverfill'],rr['v36SharedRealizedOverfill'],'terminal',br['v34ParentTerminalUnresolved'],rr['v34ParentTerminalUnresolved'],'floor',br['floor'],rr['floor']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
  agg={'markets':len(rows),'baselineV57Submits':int(sm('baselineV57','v57Submits')),'candidateV57Submits':int(sm('candidateV58','v57Submits')),'parentGlobalAllowed':int(sm('candidateV58','v58ActiveAllowed')),'parentGlobalBlocks':int(sm('candidateV58','v58ActiveBlocks')),'sameParentSecondActiveBlocks':int(sm('candidateV58','v58SameParentSecondActiveBlocks')),'parentGlobalWorstCaseOverRoot':max((float(x['candidateV58'].get('v58ParentWorstCaseOverRoot') or 0.) for x in rows),default=0.),'lateFillDoublePayViolation':max((float(x['candidateV58'].get('v58LateFillDoublePayViolation') or 0.) for x in rows),default=0.),'responsibilityOverfill':sm('candidateV58','v51ResponsibilityOverfill'),'sharedRealizedOverfill':sm('candidateV58','v36SharedRealizedOverfill'),'truthMismatch':sm('candidateV58','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV58','overOwnedSubmitViolations'),'repairDrift':sm('candidateV58','repairToExpandAtFirstFill'),'passiveCancelBeforeActiveFill':sm('candidateV58','v36PassiveCancelBeforeActiveFill'),'baselineTerminalParents':int(sm('baselineV57','v34ParentTerminalUnresolved')),'candidateTerminalParents':int(sm('candidateV58','v34ParentTerminalUnresolved')),'baselineFloorSum':sm('baselineV57','floor'),'candidateFloorSum':sm('candidateV58','floor')}
  race=next((x for x in rows if x['marketId']==1831891),None);race_ok=(race is None or (float(race['candidateV58'].get('v51ResponsibilityOverfill') or 0.)<=EPS and float(race['candidateV58'].get('v36SharedRealizedOverfill') or 0.)<=EPS and float(race['candidateV58'].get('v58ParentWorstCaseOverRoot') or 0.)<=EPS))
  gates={'zeroParentGlobalWorstCaseOverRoot':agg['parentGlobalWorstCaseOverRoot']<=EPS,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroSharedRealizedOverfill':agg['sharedRealizedOverfill']<=EPS,'zeroLateFillDoublePay':agg['lateFillDoublePayViolation']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroPassiveCancelBeforeActiveFill':agg['passiveCancelBeforeActiveFill']==0,'race1831891Fixed':race_ok,'globalLedgerExercised':agg['parentGlobalBlocks']>0}
  out={'version':'ETH_REPAIR_V58_PARENT_GLOBAL_INFLIGHT_RESPONSIBILITY_LEDGER','researchOnly':True,'mechanicsOnly':True,'aggregate':agg,'gates':gates,'mechanicalPass':all(gates.values()),'rows':rows,'boundary':['V57 timing gate retained only to exercise mechanics; V57 broad authority remains rejected','cancel-requested passive remains owned until terminalConfirmed','all Active paths pass parent-global worst-case in-flight budget','one Active reservation per parent in mechanics exam','no threshold/qty/delay/price tuning','no winner/PnL trigger','consumed realistic HFT only','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'mechanicalPass':out['mechanicalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
