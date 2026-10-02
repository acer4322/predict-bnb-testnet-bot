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
class ScopeCreditCoreExecution(r1.QueueAwareRepairRoutingSim):
 def __init__(self,tape,max_slots=4):
  super().__init__(tape,max_slots);self.coreRiskPerShare={};self.pendingCoreRiskPerShare=None;self.coreRoute=Counter();self.coreRiskConsumed=0.0
 def _reserved_core_pair_damage(self):
  z=0.0
  for _,key,o,role in self._live_role_rows(role='ECONOMIC_CORE'):
   if self.key_scope_gen.get(key)!=self.scopeGeneration:continue
   per=float(self.coreRiskPerShare.get(key,0.0));rq=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)));z+=per*rq
  return z
 def _reserved_current_expand_risk(self):return float(super()._reserved_current_expand_risk()+self._reserved_core_pair_damage())
 def _candidate_from_levels_v8(self,side,role,require_pair):
  if role!='ECONOMIC_CORE':return super()._candidate_from_levels_v8(side,role,require_pair)
  self.pendingCoreRiskPerShare=None
  pair_cand=super()._candidate_from_levels_v8(side,role,True);exec_cand=super()._candidate_from_levels_v8(side,role,False)
  if exec_cand is None:return pair_cand
  ep,eq,eproj,esp=exec_cand
  if self._pair_ok(side,ep):self.coreRoute['BEST_PAIR_COMPATIBLE']+=1;return exec_cand
  opp='DOWN' if side=='UP' else 'UP';avg=self.unmatched_avg(opp)
  if avg is None:return pair_cand
  per=max(0.0,float(avg)+float(ep)-1.0);rq=float((esp or {}).get('repairQty') or 0.0);damage=per*rq;credit=self._available_expand_risk_credit()
  if damage<=credit+EPS:
   self.pendingCoreRiskPerShare=per;self.coreRoute['EXECUTION_CORE_SCOPE_CREDIT_ALLOW']+=1;return exec_cand
  self.coreRoute['SCOPE_CREDIT_INSUFFICIENT_KEEP_PAIR_CORE']+=1;return pair_cand
 def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
  before=self.n;per=self.pendingCoreRiskPerShare if role=='ECONOMIC_CORE' else None
  ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
  if ok and role=='ECONOMIC_CORE' and per is not None and per>EPS:self.coreRiskPerShare[f'{side}_{before}']=float(per)
  self.pendingCoreRiskPerShare=None;return ok
 def process(self,t):
  pre={k:max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k in self.coreRiskPerShare};old_gen=int(self.scopeGeneration);super().process(t)
  if int(self.scopeGeneration)!=old_gen:return
  spent=0.0
  for k,b in pre.items():
   a=max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0)));alloc=max(0.0,b-a);spent+=alloc*float(self.coreRiskPerShare.get(k,0.0))
  if spent>EPS:self.scopeRiskCreditConsumed+=spent;self.coreRiskConsumed+=spent;self.coreRoute['CONFIRMED_CORE_EXECUTION_PREMIUM_CONSUMED']+=1
 def run_d9(self,winner):
  r=super().run_v88(winner);r['coreExecutionRouting']=dict(self.coreRoute);r['coreExecutionRiskConsumed']=float(self.coreRiskConsumed);return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d9_scope_core_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
  for mid in mids:
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';c=r1.QueueAwareRepairRoutingSim(tape,4)
   try:r0=c.run_v88(cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0});s=ScopeCreditCoreExecution(tape,4)
   try:r=s.run_d9(cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'cell':'MS4_D9_SCOPE_CREDIT_CORE_EXECUTION','winnerPostHocOnly':cr['winner'],**r})
   print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d9Sub':r['submits'],'r1Fill':r0['fillEvents'],'d9Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d9Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d9Floor':r['floor'],'route':r['coreExecutionRouting'],'riskSpent':r['coreExecutionRiskConsumed'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
  c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_D9_SCOPE_CREDIT_CORE_EXECUTION'};cmp=[{'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'coreRiskConsumed':n[m]['coreExecutionRiskConsumed']} for m in mids]
  out={'version':'MS4_D9_SCOPE_CREDIT_CORE_EXECUTION_20260905','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapsePass':all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['Pair-compatible Core remains default','best-price nonpair Core requires exact pair damage <= existing available scope risk credit','pair-damage premium reserved while carrier live and consumed only on confirmed Repair allocation','same credit cannot be double-spent by Expand','Repair/Overflow/debt correctness unchanged','no future credit','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
