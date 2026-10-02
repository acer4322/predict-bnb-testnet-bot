from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
 sp=importlib.util.spec_from_file_location('ms4r1',_STAGED);r1=importlib.util.module_from_spec(sp);sp.loader.exec_module(r1)
else:import tools.run_eth_ms4_r1_queue_aware_repair as r1
EPS=1e-9

class ConcurrentRoleScheduler(r1.QueueAwareRepairRoutingSim):
 def __init__(self,tape,max_slots=4):
  super().__init__(tape,max_slots);self.scheduler=Counter()
 def _live_current(self,role,side=None):
  return [(sid,k,o,r) for sid,k,o,r in self._live_role_rows(role=role,side=side) if self.key_scope_gen.get(k)==self.scopeGeneration]
 def _proposal_list(self,qv):
  state=self._state();sig=self._direction(qv)
  if state=='EMPTY':return [] if self._live_role_rows(role='PROBE_CORE') else [(sig,'PROBE_CORE',False,False)]
  if state=='PAIRED':return [] if self._live_role_rows(role='SCOPE_BIRTH') else [(sig,'SCOPE_BIRTH',False,False)]
  rs='DOWN' if self.scopeSide=='UP' else 'UP';out=[]
  if self._core_for_side(rs) is None:out.append((rs,'ECONOMIC_CORE',True,True))
  # Persistent Repair option: independent of instantaneous signal; only one execution Repair lane live.
  if not self._live_current('SATELLITE_REPAIR',rs):out.append((rs,'SATELLITE_REPAIR',False,True))
  # Concurrent Expand option: independent of Repair lane occupancy, but exact existing monetary credit still required.
  if self._available_expand_risk_credit()>EPS and not self._live_current('SATELLITE_EXPAND',self.scopeSide):out.append((self.scopeSide,'SATELLITE_EXPAND',False,False))
  # Use instantaneous signal only as a tie-break preference, never as exclusive role authority.
  out.sort(key=lambda z: (0 if z[1]=='ECONOMIC_CORE' else 1 if (z[1]=='SATELLITE_EXPAND' and sig==self.scopeSide) or (z[1]=='SATELLITE_REPAIR' and sig==rs) else 2, z[1]))
  return out
 def _open_one_option(self,t,qv,end):
  if int(end)-int(t)<=r1.v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
  if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
  props=self._proposal_list(qv)
  if not props:self.veto['ROLE_WAIT']+=1;return
  if len(self.slot_key)>=self.max_slots:self.veto['GLOBAL_SLOT_CAP_FULL']+=1;return
  for side,role,require_pair,require_budget in props:
   if len(self._live_role_rows(side=side))>=self.max_slots:continue
   cand=self._candidate_from_levels_v8(side,role,require_pair)
   if cand is None:self.scheduler[f'{role}:NO_CANDIDATE']+=1;continue
   p,q,proj,split=cand
   if role=='SATELLITE_EXPAND':
    risk=max(0.0,self._physical_floor()-self._candidate_alone_floor(side,p,q))
    if self._available_expand_risk_credit()+EPS<risk:self.scheduler['EXPAND:CREDIT_BLOCK']+=1;continue
   if self._submit_role_v8(t,side,role,p,q,proj,split):self.scheduler[f'{role}:SUBMIT']+=1;return
  self.veto['ROLE_WAIT']+=1
 def run_d6(self,winner):
  r=super().run_v88(winner);r['concurrentScheduler']=dict(self.scheduler);return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d6_concurrent_roles_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
  for mid in mids:
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';c=r1.QueueAwareRepairRoutingSim(tape,4)
   try:r0=c.run_v88(cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0});s=ConcurrentRoleScheduler(tape,4)
   try:r=s.run_d6(cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'cell':'MS4_D6_CONCURRENT_ROLE_SCHEDULER','winnerPostHocOnly':cr['winner'],**r})
   print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d6Sub':r['submits'],'r1Fill':r0['fillEvents'],'d6Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d6Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d6Floor':r['floor'],'sched':r['concurrentScheduler'],'roles':r['roleSubmits'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
  c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_D6_CONCURRENT_ROLE_SCHEDULER'};cmp=[{'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor']} for m in mids]
  out={'version':'MS4_D6_CONCURRENT_ROLE_SCHEDULER_20260905','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapsePass':all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['same Pair/Core/Repair/Overflow/credit correctness as MS4-R1','Repair and Expand role proposals may coexist; instantaneous signal is tie-break only','max one live execution Repair satellite and max one live Expand satellite per current scope','one new physical option per receipt retained','no new risk authority','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
