from __future__ import annotations
import argparse,json,math,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.joint_multislot_recoverability import JointMultiSlotContext,JointMultiSlotCurrentCoordinateRecoverabilityPolicyV1
EPS=1e-9;MID=1946784;pe=base.pe

class JointRecoverabilityShadowHFT(mod.EconomicHandoffLeaseLockHFT):
 def __init__(self,*a,**kw):
  self.jointPolicy=JointMultiSlotCurrentCoordinateRecoverabilityPolicyV1();self.jointShadow=[]
  super().__init__(*a,**kw)
 def _request_cancel(self,t,row,reason):
  if str(reason)=='FRONTIER_REANCHOR':
   side=str(row.get('side') or '').upper();target,bid,ask,pdec=self._priority_target(side)
   if target is not None:
    pid=int(row['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));hyp_avail=avail+float(row.get('remaining') or 0.0);qty=min(float(row.get('remaining') or 0.0),hyp_avail);econ=self._econ(int(t),side,qty,float(target))
    qv=v1.quotes(self.book)
    slots=[]
    if qv and side in qv and bool(econ.get('allow')) and qty>EPS:
     for k,e in getattr(self,'carrierLedger',{}).items():
      if str(k)==str(row.get('key')):continue
      try:
       if int(e.get('parentId'))!=pid or str(e.get('objectiveRole') or '').upper()!='REPAIR' or bool(e.get('terminalConfirmed')):continue
      except Exception:continue
      rem=max(0.0,float(e.get('submittedQty') or 0.0)-float(e.get('actualFilled') or 0.0))
      if rem<=EPS:continue
      o=getattr(self,'orders',{}).get(str(k),{});p=float(o.get('price') or e.get('price') or 0.0)
      if p>EPS:slots.append({'key':str(k),'side':str(e.get('side') or side).upper(),'price':p,'qty':rem,'kind':'EXISTING_LIVE'})
     slots.append({'key':'PROPOSED_REPLACEMENT','side':side,'price':float(target),'qty':float(qty),'kind':'PROPOSED'})
     try:
      d=self.jointPolicy.evaluate(JointMultiSlotContext(float(self.inv.get('UP',0.0)),float(self.inv.get('DOWN',0.0)),float(self.cost),tuple({'side':z['side'],'price':z['price'],'qty':z['qty']} for z in slots),float(qv['UP']['bid']),float(qv['DOWN']['bid']),4,12.0))
      self.jointShadow.append({'t':int(t),'parentId':pid,'oldKey':str(row.get('key')),'v16Allow':True,'v16Reason':econ.get('reason'),'targetPrice':float(target),'qty':float(qty),'slots':slots,'upSharesBefore':float(self.inv.get('UP',0.0)),'downSharesBefore':float(self.inv.get('DOWN',0.0)),'costBefore':float(self.cost),'upBid':float(qv['UP']['bid']),'downBid':float(qv['DOWN']['bid']),'jointRecoverable':bool(d.recoverable),'jointReason':d.reason,'floorBefore':d.floor_before,'floorAfterJointFill':d.floor_after_joint_fill,'jointRepairDebt':d.joint_repair_debt,'debtSide':d.debt_side,'recursive':None if d.recursive is None else d.recursive.__dict__})
     except Exception as ex:self.jointShadow.append({'t':int(t),'parentId':pid,'oldKey':str(row.get('key')),'v16Allow':True,'error':repr(ex)})
  return super()._request_cancel(int(t),row,reason)
 def run_shadow(self,models,winner):
  r=self.run_locked(models,winner);r['jointRecoverabilityShadow']=self.jointShadow[:200];return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='joint_recoverability_1946784_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'JOINT_RECOVERABILITY_SHADOW_1946784','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'JOINT_RECOVERABILITY_SHADOW_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(JointRecoverabilityShadowHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_shadow(models,cr['winner']);cons,bound,parents=pe.alloc(s,r)
  finally:s.close()
  m=base.slim(r);ss=pe.safety(r);shadow=r.get('jointRecoverabilityShadow') or [];allowed=[x for x in shadow if x.get('v16Allow') and 'error' not in x];blocked=[x for x in allowed if not x.get('jointRecoverable')]
  out={'version':'ETH_MULTISLOT_JOINT_RECOVERABILITY_SHADOW_1946784_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'candidate':m,'safety':ss,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'v16AllowedShadowCount':len(allowed),'jointWouldBlockCount':len(blocked),'jointShadow':shadow,'decision':'JOINT_GATE_EXPLAINS_FLOOR_REGRESSION' if blocked else 'JOINT_GATE_DOES_NOT_EXPLAIN_FLOOR_REGRESSION','boundary':['shadow only; action path frozen','V16 economic handoff behavior unchanged','joint current-coordinate recoverability evaluated only at V16-allowed reanchor preflight','max carriers 4 inherited from existing joint policy test budget','no runtime authority','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'candidate':m,'allowed':len(allowed),'wouldBlock':len(blocked),'shadow':shadow},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
