from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
EPS=1e-9;MID=1946784;pe=base.pe
class PostActiveResidualProposalAudit(mod.EconomicHandoffLeaseLockHFT):
 def __init__(self,*a,**kw):self.proposalAudit=[];super().__init__(*a,**kw)
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  rp=getattr(self,'repairParent',None);pid=int(rp.get('id')) if isinstance(rp,dict) and rp.get('id') is not None else None
  a=getattr(self,'activeByParent',{}).get(pid) if pid is not None else None
  if pid is not None and a is not None and z is not None:
   ak=str(a.get('key'));e=getattr(self,'carrierLedger',{}).get(ak,{});terminal=bool(e.get('terminalConfirmed'));debt=float(self._parent_debt_now(pid))
   if terminal and debt>EPS:
    side,qty,oldp,role,oid=z
    self.proposalAudit.append({'t':int(t),'parentId':pid,'activeKey':ak,'activeTerminal':terminal,'activeFilled':float(e.get('actualFilled') or 0.0),'residualDebt':debt,'proposal':{'side':side,'qty':float(qty),'oldp':oldp,'role':str(role),'objectiveId':oid},'rolesThisTick':sorted(str(x) for x in roles_this_tick),'hardConfirmed':pid in getattr(self,'hardConfirmed',set()),'epochActiveOwned':bool(getattr(getattr(self,'generationEpochByParent',{}).get(pid),'active_owned',False))})
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_audit(self,models,winner):r=self.run_locked(models,winner);r['postActiveResidualProposalAudit']=self.proposalAudit[:300];return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='post_active_residual_proposal_1946784_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'POST_ACTIVE_RESIDUAL_PROPOSAL_AUDIT_1946784','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'POST_ACTIVE_RESIDUAL_PROPOSAL_AUDIT_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(PostActiveResidualProposalAudit,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_audit(models,cr['winner'])
  finally:s.close()
  rows=r.get('postActiveResidualProposalAudit') or [];repair=[x for x in rows if str(x['proposal']['role']).upper()=='REPAIR'];out={'version':'ETH_MULTISLOT_POST_ACTIVE_RESIDUAL_PROPOSAL_AUDIT_1946784_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'candidate':base.slim(r),'rows':rows,'repairProposalRows':len(repair),'decision':'MANAGER_REPAIR_PROPOSAL_EXISTS_AFTER_TERMINAL_ACTIVE' if repair else 'NO_MANAGER_REPAIR_PROPOSAL_AFTER_TERMINAL_ACTIVE','boundary':['behavior-inert trace only','same EconomicHandoffLeaseLock action path','no rearm/action changes','strict-past OUR state','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'repairProposalRows':len(repair),'firstRows':rows[:20]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
