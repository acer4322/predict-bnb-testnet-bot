from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sibling(name,path):
 p=Path(path);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
base=sibling('v83_epoch_for_child_materialization_shadow',Path(__file__).resolve().with_name('run_eth_v83_existing_parent_responsibility_generation_epoch_hft_1916869.py'))
EPS=1e-9;FIXED=1916869;birth=base.birth;v38=base.v38;v80=base.v80

class PostEpochChildMaterializationShadow(base.ExistingParentResponsibilityGenerationEpochHFT):
 def __init__(self,*a,**kw):
  self.childMaterializationEvents=[];self._diagCommit=None;self._diagSubmitCalls=[];self._diagPackageCalls=[]
  super().__init__(*a,**kw)
 def _num_snapshot(self):
  out={}
  for k,v in self.__dict__.items():
   if isinstance(v,(int,float,bool)) and not k.startswith('_diag'):
    try:out[k]=float(v)
    except Exception:pass
  return out
 def _changed(self,b,a):
  return {k:{'before':b.get(k),'after':a.get(k),'delta':a.get(k,0)-b.get(k,0)} for k in sorted(set(b)|set(a)) if abs(float(a.get(k,0))-float(b.get(k,0)))>EPS}
 def _state(self,t,side=None):
  rp=getattr(self,'repairParent',None);pid=int(rp.get('id')) if isinstance(rp,dict) and rp.get('id') is not None else None
  qv=birth.base.v1.quotes(self.book) if hasattr(birth.base,'v1') else None
  px=None
  if qv and side in ('UP','DOWN') and side in qv:px={k:qv[side].get(k) for k in ('bid','ask')}
  def safe_lane(role):
   try:return [{'key':str(k),'remaining':float(rem),'side':e.get('side'),'role':e.get('objectiveRole'),'state':e.get('state'),'cancelRequested':e.get('cancelRequested'),'price':e.get('price'),'submittedQty':e.get('submittedQty'),'actualFilled':e.get('actualFilled')} for k,e,rem in self.lane_unresolved(role)]
   except Exception as ex:return [{'error':type(ex).__name__+':'+str(ex)}]
  return {'t':int(t),'parent':dict(rp) if isinstance(rp,dict) else None,'parentId':pid,'epochAttached':pid in getattr(self,'generationEpochByParent',{} ) if pid is not None else False,'quotes':px,'repairUnresolved':safe_lane('REPAIR'),'expandUnresolved':safe_lane('EXPAND'),'outstandingTotal':float(self.outstanding_total()) if hasattr(self,'outstanding_total') else None,'reserveBuilder':dict(getattr(self,'reserveBuilder')) if isinstance(getattr(self,'reserveBuilder',None),dict) else None,'activeOwned':pid in getattr(self,'activeByParent',{}) if pid is not None else False}
 def choose_authorized(self,t,end,proposed_side,proposed_qty):
  before=int(getattr(self,'repairChildCommits',0) or 0);z=super().choose_authorized(t,end,proposed_side,proposed_qty);after=int(getattr(self,'repairChildCommits',0) or 0)
  if after>before and z is not None and str(z[3])=='REPAIR':
   side,qty,oldp,role,oid=z;self._diagCommit={'t':int(t),'side':str(side),'qty':float(qty),'oldp':oldp,'role':str(role),'objectiveId':oid,'repairChildCommitsBefore':before,'repairChildCommitsAfter':after,'stateAtCommit':self._state(t,str(side))}
   self.childMaterializationEvents.append({'event':'POST_EPOCH_REPAIR_CHILD_COMMIT',**self._diagCommit})
  return z
 def submit(self,t,side,p,q):
  b=self._num_snapshot();r=super().submit(t,side,p,q);a=self._num_snapshot();ev={'event':'PHYSICAL_SUBMIT_CALL','t':int(t),'side':str(side),'price':float(p),'qty':float(q),'return':bool(r),'changedCounters':self._changed(b,a)}
  self._diagSubmitCalls.append(ev);self.childMaterializationEvents.append(ev);return r
 def _submit_package_at_price(self,t,qv,z,roles_this_tick,p):
  if z is None:return super()._submit_package_at_price(t,qv,z,roles_this_tick,p)
  b=self._num_snapshot();side,qty,oldp,role,oid=z
  ev={'event':'SUBMIT_PACKAGE_ENTER','t':int(t),'side':str(side),'qty':float(qty),'role':str(role),'objectiveId':oid,'price':float(p),'state':self._state(t,str(side))};self.childMaterializationEvents.append(ev)
  r=super()._submit_package_at_price(t,qv,z,roles_this_tick,p);a=self._num_snapshot();ev2={'event':'SUBMIT_PACKAGE_EXIT','t':int(t),'side':str(side),'role':str(role),'objectiveId':oid,'return':bool(r),'changedCounters':self._changed(b,a)};self._diagPackageCalls.append(ev2);self.childMaterializationEvents.append(ev2);return r
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is None:return super()._submit_authorized(t,qv,z,roles_this_tick)
  side,qty,oldp,role,oid=z;b=self._num_snapshot();submit0=len(self._diagSubmitCalls);package0=len(self._diagPackageCalls);state0=self._state(t,str(side))
  r=super()._submit_authorized(t,qv,z,roles_this_tick);a=self._num_snapshot();submit_calls=self._diagSubmitCalls[submit0:];package_calls=self._diagPackageCalls[package0:];changed=self._changed(b,a)
  commit=self._diagCommit if self._diagCommit and int(self._diagCommit.get('t',-1))==int(t) and str(role)=='REPAIR' else None
  classification='MATERIALIZED' if r else ('SUBMIT_CALLED_BUT_REJECTED' if submit_calls else ('PACKAGE_PATH_REJECTED_PRE_SUBMIT' if package_calls else 'AUTHORIZED_CHILD_UNREACHABLE_TO_PHYSICAL_SUBMIT'))
  ev={'event':'AUTHORIZED_TO_MATERIALIZATION_EXIT','t':int(t),'side':str(side),'qty':float(qty),'oldp':oldp,'role':str(role),'objectiveId':oid,'return':bool(r),'classification':classification,'isNewRepairChildCommit':bool(commit),'commit':commit,'stateBefore':state0,'changedCounters':changed,'packageCalls':package_calls,'physicalSubmitCalls':submit_calls}
  self.childMaterializationEvents.append(ev)
  return r
 def run_shadow(self,models,winner):
  r=super().run_candidate(models,winner)
  post=[e for e in self.childMaterializationEvents if e.get('event')=='AUTHORIZED_TO_MATERIALIZATION_EXIT' and e.get('role')=='REPAIR' and e.get('isNewRepairChildCommit')]
  r.update({'childMaterializationEvents':self.childMaterializationEvents[:300],'postEpochCommittedRepairMaterialization':post[:40]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=FIXED:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='v83_child_materialization_shadow_1916869_'));stop=threading.Event()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'V83_CHILD_MATERIALIZATION_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V83_CHILD_MATERIALIZATION_SHADOW_START','market':FIXED}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
  c=PostEpochChildMaterializationShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:rr=c.run_shadow(models,cr['winner'])
  finally:c.close()
  ss=birth.base.front.safety(rr);cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  post=rr.get('postEpochCommittedRepairMaterialization',[]);classes={}
  for e in post:classes[e.get('classification')]=classes.get(e.get('classification'),0)+1
  safety_zero=all(float(v)<=EPS for v in ss.values())
  gates={'postExpandRepairChildCommitObserved':len(post)>0,'commitToSubmitDeltaLocalized':len(post)>0,'singleTerminalClassObserved':len(classes)==1,'allocationConservation':cons,'safetyZero':safety_zero}
  decision='PASS_CHILD_MATERIALIZATION_BLOCKER_LOCALIZED_'+(next(iter(classes)) if len(classes)==1 else 'MULTIPLE_OR_NONE') if all(gates.values()) else 'DIAGNOSE_CHILD_MATERIALIZATION_SHADOW'
  out={'version':'ETH_V83_EXISTING_PARENT_POST_EPOCH_CHILD_MATERIALIZATION_SHADOW_1916869','date':'2026-09-04','researchOnly':True,'marketId':FIXED,'decision':decision,'gates':gates,'classCounts':classes,'candidate':birth.slim(rr),'safety':ss,'postEpochCommittedRepairMaterialization':post,'events':rr.get('childMaterializationEvents',[]),'epochEvents':rr.get('generationEpochEvents',[]),'birthEvents':rr.get('expandFillResponsibilityEvents',[]),'boundary':['behavior-inert instrumentation only','single market 1916869','no decision override','ResponsibilityTransition frozen','AllocationLedger V2 frozen','RepairExecutionRouter V2 frozen','threshold/qty/price/delay frozen','<=180s fence preserved','realistic HFT only','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'classes':classes,'candidate':out['candidate'],'post':post[:6],'safety':ss},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
