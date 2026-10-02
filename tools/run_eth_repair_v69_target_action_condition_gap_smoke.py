from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v65=sib('eth_v65_for_v69','run_eth_repair_v65_global_expand_ownership_dedup_smoke.py')
v53=v65.v53;v38=v65.v38;v1=v65.v64.v1;EPS=1e-9
ROLES={'PASSIVE_REPAIR','ACTIVE_REPAIR','PASSIVE_EXPAND','ACTIVE_EXPAND'}

def weak_side(inv):
 u=float(inv.get('UP',0.0));d=float(inv.get('DOWN',0.0))
 return 'UP' if u<d-EPS else 'DOWN' if d<u-EPS else None

def scalar(d):
 return {k:v for k,v in d.items() if v is None or isinstance(v,(str,bool,int,float))}

class V69TargetClockAudit(v65.V65GlobalExpandDedup):
 def __init__(self,*a,target_events=None,**kw):
  super().__init__(*a,**kw);self.v69Targets=[{'eventIndex':i,**dict(e)} for i,e in enumerate(target_events or []) if str(e.get('role')) in ROLES];self.v69Next=0;self.v69LastSnapshot=None;self.v69Rows=[]
 def _predict_scores(self,t):
  try:
   f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);p44=float(self.teacher['model'].predict_proba(x)[0,1])
  except Exception:p44=None;f={}
  p47=None
  if getattr(self,'genTeacher',None) is not None and f:
   try:
    f=dict(f);f['lastRepairWasTaker']=float(any(e.get('event')=='V36_ACTIVE_SHARED_FILL' and int(e.get('t',-1))==int(getattr(self,'_coordLastRepairT',-2) or -2) for e in getattr(self,'activeEvents',[])))
    x=np.asarray([[float(f[c]) for c in self.genTeacher['features']]],np.float32);p47=float(self.genTeacher['model'].predict_proba(x)[0,1])
   except Exception:p47=None
  return p44,p47,scalar(f)
 def _capture(self,t):
  self._refresh_carrier_ledger(t);q=v1.quotes(self.book) or {};pay=self._current_payoffs();rp=self.repairParent;pid=None if rp is None else rp.get('id');ws=weak_side(self.truthInv);th=getattr(self,'thesis',None);thside=None if not th else th.get('side')
  carriers=[]
  for k,e in getattr(self,'carrierLedger',{}).items():
   rem=max(0.0,float(e.get('submittedQty') or 0.0)-float(e.get('actualFilled') or 0.0))
   if rem<=EPS or bool(e.get('terminalConfirmed')):continue
   o=self.orders.get(k,{});lane=str(e.get('lane') or o.get('execution_role') or '');active=('TAKER' in lane.upper()) or lane.startswith('ACTIVE_')
   carriers.append({'key':k,'side':e.get('side') or o.get('side'),'role':e.get('objectiveRole') or o.get('objective_role'),'lane':lane,'active':active,'remaining':rem,'objectiveId':e.get('objectiveId')})
  failed=[]
  for k in getattr(self,'v44Keys',set()):
   e=self.carrierLedger.get(k,{});o=self.orders.get(k,{})
   if bool(e.get('terminalConfirmed')) and float(e.get('actualFilled') or 0.0)<=EPS:
    rem=max(0.0,float(e.get('submittedQty') or o.get('qty') or 0.0));failed.append({'key':k,'side':e.get('side') or o.get('side'),'remaining':rem,'submittedAt':e.get('submittedAt'),'objectiveId':e.get('objectiveId')})
  g=self._latest_gen();grem=0.0;gid=None
  if g is not None:gid=int(g['id']);grem=max(0.0,float(g.get('debt') or 0.0)-float(g.get('paid') or 0.0))
  ctx=None if gid is None else getattr(self,'v52EpochCtx',{}).get(gid);rearm=None if gid is None else getattr(self,'v49RearmContexts',{}).get(gid)
  opp=[x for x in getattr(self,'postArmCarrierOpportunities',[]) if pid is not None and int(x.get('parentId',-1))==int(pid)]
  if ctx is not None:
   opp=[x for x in opp[int(ctx.get('opportunityStartIndex') or 0):] if int(x.get('t',-1))>int(ctx.get('bornAt') or 0)]
  fresh2=bool(len(opp)>=2 and not any(bool(x.get('connected')) for x in opp[:2]))
  p44,p47,f=self._predict_scores(t)
  return {'snapshotT':int(t),'remainingSec':(int(self.capEnd)-int(t))/1000.0,'truthUP':float(self.truthInv['UP']),'truthDOWN':float(self.truthInv['DOWN']),'weakSide':ws,'expandSide':thside,'floor':float(pay.get('floor') or 0.0),'repairGap':float(pay.get('gap') or 0.0),'coordDebt':float(getattr(self,'_coordDebt',0.0) or 0.0),'repairParentId':pid,'repairParentSide':None if rp is None else rp.get('side'),'generationId':gid,'generationRemaining':grem,'paymentEpochPresent':ctx is not None,'rearmContextPresent':rearm is not None,'parentArmed':bool(pid is not None and int(pid) in getattr(self,'_armedParents',set())),'freshFailureOpportunityCount':len(opp),'freshFirstTwoDisconnected':fresh2,'pV44':p44,'pV47':p47,'coordFeature':f,'quotes':{s:{'bid':None if not q.get(s) else q[s].get('bid'),'ask':None if not q.get(s) else q[s].get('ask')} for s in ('UP','DOWN')},'carriers':carriers,'failedPassiveExpandSources':failed,'lastRepairT':getattr(self,'_coordLastRepairT',None),'lastExpandT':getattr(self,'_coordLastExpandT',None)}
 def _eval(self,e,s):
  role=str(e['role']);target_side=str(e.get('side') or '');isrep=role.endswith('REPAIR');active=role.startswith('ACTIVE_');isexpand=not isrep;desired=s['weakSide'] if isrep else s['expandSide'];side=desired
  q=s['quotes'].get(side,{}) if side in ('UP','DOWN') else {};px=q.get('ask' if active else 'bid');legal=(1.0/float(px)) if px is not None and float(px)>EPS else math.inf
  same_lane=[c for c in s['carriers'] if c['role']==('REPAIR' if isrep else 'EXPAND') and bool(c['active'])==active and c['side']==side]
  passive_rep=[c for c in s['carriers'] if c['role']=='REPAIR' and not c['active'] and c['side']==side]
  expand_any=[c for c in s['carriers'] if c['role']=='EXPAND']
  common={'STRICT_PAST_SNAPSHOT':int(s['snapshotT'])<int(e['t']),'OUR_ROLE_SIDE_AVAILABLE':side in ('UP','DOWN'),'NEW_EXPOSURE_TIME_ALLOWED':(not isexpand) or float(s['remainingSec'])>180.0}
  path={}
  if role=='PASSIVE_REPAIR':
   path={'REPAIR_RESPONSIBILITY':s['repairParentId'] is not None and s['repairGap']>EPS,'PASSIVE_VENUE_MIN_LEGAL':math.isfinite(legal) and legal<=12.0+EPS,'REPAIR_CAPACITY_LEGAL':math.isfinite(legal) and s['repairGap']+EPS>=legal}
  elif role=='ACTIVE_REPAIR':
   budget=min(float(s['repairGap']),float(s['generationRemaining']) if s['generationRemaining']>EPS else float(s['repairGap']),max([c['remaining'] for c in passive_rep]+[0.0]))
   path={'REPAIR_RESPONSIBILITY':s['repairParentId'] is not None and s['repairGap']>EPS,'SHARED_PASSIVE_REPAIR_CARRIER':bool(passive_rep),'PAYMENT_SCOPE_PRESENT':bool(s['paymentEpochPresent'] or s['rearmContextPresent'] or s['parentArmed']),'FRESH_FAILURE_EVIDENCE':bool(s['freshFirstTwoDisconnected']),'ACTIVE_REPAIR_BUDGET_LEGAL':math.isfinite(legal) and budget+EPS>=legal}
  elif role=='PASSIVE_EXPAND':
   model_auth=bool(s['pV44'] is not None and s['pV44']>=.5 and (s['pV47'] is None or s['pV47']>=.5))
   path={'THESIS_PRESENT':s['expandSide'] in ('UP','DOWN'),'RECENT_REPAIR_INTERACTION':s['lastRepairT'] is not None and int(s['lastRepairT'])<int(e['t']),'EXPAND_MODEL_AUTHORIZED':model_auth,'PASSIVE_VENUE_MIN_LEGAL':math.isfinite(legal) and legal<=12.0+EPS,'GLOBAL_EXPAND_FREE':not bool(expand_any)}
  elif role=='ACTIVE_EXPAND':
   src=[z for z in s['failedPassiveExpandSources'] if z['side']==side and math.isfinite(legal) and float(z['remaining'])+EPS>=legal]
   path={'THESIS_PRESENT':s['expandSide'] in ('UP','DOWN'),'AUTHORIZED_FAILED_PASSIVE_SOURCE':bool(src),'NO_NEWER_EXPAND_OCCUPANCY':not bool(expand_any),'ACTIVE_EXPAND_SHARED_BUDGET_LEGAL':math.isfinite(legal) and bool(src)}
  base_ok=all(common.values());new_path_ok=all(path.values());existing=bool(same_lane);ready=bool(base_ok and (existing or new_path_ok))
  missing=[k for k,v in common.items() if not v]
  if not existing:missing.extend(k for k,v in path.items() if not v)
  semantic=bool(base_ok and (path.get('REPAIR_RESPONSIBILITY',True)) and path.get('THESIS_PRESENT',True))
  exact_align=desired==target_side
  return {'targetRole':role,'targetSide':target_side,'targetQty':float(e.get('qty') or 0.0),'targetPrice':float(e.get('price') or 0.0),'desiredOurSideForRole':desired,'targetSideRoleInOur':'REPAIR' if s['weakSide']==target_side else 'EXPAND' if target_side in ('UP','DOWN') else None,'conditions':{**common,**path},'missingConditions':missing,'semanticReady':semantic,'sameRoleReadiness':ready,'exactTargetSideAlignment':exact_align,'exactTargetActionReadiness':bool(ready and exact_align),'existingSameLaneCarrierCount':len(same_lane),'existingCarrierPath':existing,'venuePrice':px,'venueLegalMinQty':None if not math.isfinite(legal) else legal}
 def _flush(self,t):
  while self.v69Next<len(self.v69Targets) and int(self.v69Targets[self.v69Next]['t'])<=int(t):
   e=self.v69Targets[self.v69Next];self.v69Next+=1;s=self.v69LastSnapshot
   if s is None or int(s['snapshotT'])>=int(e['t']):
    self.v69Rows.append({'eventIndex':e['eventIndex'],'targetT':int(e['t']),'targetRole':e['role'],'targetSide':e.get('side'),'covered':False,'reason':'NO_STRICT_PAST_OUR_SNAPSHOT'});continue
   z={'eventIndex':e['eventIndex'],'targetT':int(e['t']),'covered':True,'snapshotLagMs':int(e['t'])-int(s['snapshotT']),'ourState':s};z.update(self._eval(e,s));self.v69Rows.append(z)
 def process(self,t):
  if hasattr(self,'v69Targets'):self._flush(t)
  out=super().process(t)
  if hasattr(self,'v69Targets'):self.v69LastSnapshot=self._capture(t)
  return out
 def cancel_expired(self,t):
  out=super().cancel_expired(t)
  if hasattr(self,'v69Targets'):self.v69LastSnapshot=self._capture(t)
  return out
 def submit(self,t,side,p,q):
  ok=super().submit(t,side,p,q)
  if ok and hasattr(self,'v69Targets'):self.v69LastSnapshot=self._capture(t)
  return ok
 def run_exam_v69(self,models,winner):
  r=super().run_exam_v65(models,winner);self._flush(int(self.capEnd)+1);ev=sorted(self.v53Fills,key=lambda x:(int(x['t']),x['key']))
  for row in self.v69Rows:
   if not row.get('covered'):continue
   tr=int(row['targetT']);role=row['targetRole'];side=row['targetSide'];cand=[x for x in ev if x['role']==role]
   row['nearestOurSameRoleDeltaMs']=None if not cand else min(abs(int(x['t'])-tr) for x in cand)
   same=[x for x in cand if x.get('side')==side];row['nearestOurSameRoleSideDeltaMs']=None if not same else min(abs(int(x['t'])-tr) for x in same)
  r.update({'v69TargetConditionRows':self.v69Rows});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','target-teacher-json','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v69_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V69','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V69_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};target=json.load(open(a.target_teacher_json,encoding='utf-8'))['markets'];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[];allz=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];te=target.get(str(mid),[]);c=V69TargetClockAudit(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,target_events=te)
   try:r=c.run_exam_v69(models,cr['winner'])
   finally:c.close()
   z=r['v69TargetConditionRows'];allz.extend([{'marketId':mid,**x} for x in z]);rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'targetEvents':len(z),'covered':sum(x.get('covered',False) for x in z),'semanticReady':sum(x.get('semanticReady',False) for x in z),'sameRoleReady':sum(x.get('sameRoleReadiness',False) for x in z)},ensure_ascii=False),flush=True)
  byrole={};missing={}
  for x in allz:
   role=x['targetRole'];z=byrole.setdefault(role,{'events':0,'covered':0,'semanticReady':0,'sameRoleReadiness':0,'exactSideAlignment':0,'exactTargetActionReadiness':0,'sameRoleWithin1s':0,'sameRoleWithin3s':0});z['events']+=1;z['covered']+=int(x.get('covered',False));z['semanticReady']+=int(x.get('semanticReady',False));z['sameRoleReadiness']+=int(x.get('sameRoleReadiness',False));z['exactSideAlignment']+=int(x.get('exactTargetSideAlignment',False));z['exactTargetActionReadiness']+=int(x.get('exactTargetActionReadiness',False));dlt=x.get('nearestOurSameRoleDeltaMs');z['sameRoleWithin1s']+=int(dlt is not None and dlt<=1000);z['sameRoleWithin3s']+=int(dlt is not None and dlt<=3000)
   for m in x.get('missingConditions',[]):missing[m]=missing.get(m,0)+1
  covered=sum(x.get('covered',False) for x in allz);roles=set(x['targetRole'] for x in allz);lags=[x['snapshotLagMs'] for x in allz if x.get('covered')];ready=sum(x.get('sameRoleReadiness',False) for x in allz);exact=sum(x.get('exactTargetActionReadiness',False) for x in allz)
  agg={'markets':len(rows),'targetEvents':len(allz),'covered':covered,'coverageRate':covered/max(1,len(allz)),'sameRoleReadiness':ready,'exactTargetActionReadiness':exact,'roles':byrole,'missingConditionCounts':dict(sorted(missing.items(),key=lambda z:(-z[1],z[0]))),'medianSnapshotLagMs':None if not lags else float(np.median(lags)),'maxSnapshotLagMs':None if not lags else max(lags),'truthMismatch':sum(float(x['functional'].get('authorizedSubmitWithTruthRoleMismatch') or 0) for x in rows),'overOwned':sum(float(x['functional'].get('overOwnedSubmitViolations') or 0) for x in rows),'repairDrift':sum(float(x['functional'].get('repairToExpandAtFirstFill') or 0) for x in rows),'responsibilityOverfill':sum(float(x['functional'].get('v51ResponsibilityOverfill') or 0) for x in rows)}
  gates={'materialStrictPastCoverage':agg['coverageRate']>=.9,'allFourRolesRepresented':ROLES.issubset(roles),'conditionVariation':len(missing)>=4 and ready>0 and ready<covered,'behaviorAccountingSafe':agg['truthMismatch']==0 and agg['overOwned']==0 and agg['repairDrift']==0 and agg['responsibilityOverfill']<=EPS}
  out={'version':'ETH_REPAIR_V69_TARGET_ACTION_CONDITION_GAP_SMOKE_V2','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'trainingDataOnly':True,'aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'conditionRows':allz,'rows':rows,'boundary':['Target fill clock is diagnostic indexing only and never forces OUR action.','Every row uses the last strictly earlier OUR snapshot.','Target side mismatch under OUR inventory is recorded, not imitated.','Conditions follow execution-certainty then objective/responsibility then routing then lane feasibility.','No winner/PnL trigger, no threshold/qty/delay tuning, no dream fill, no 8781.']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
