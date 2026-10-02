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
class PairReserveFundedCore(r1.QueueAwareRepairRoutingSim):
 def __init__(self,tape,max_slots=4):super().__init__(tape,max_slots);self.coreReserve=Counter();self.coreDecisions=[]
 def _candidate_from_levels_v8(self,side,role,require_pair):
  if role!='ECONOMIC_CORE':return super()._candidate_from_levels_v8(side,role,require_pair)
  # Safe economic candidate remains available exactly as MS4-R1.
  pair_cand=super()._candidate_from_levels_v8(side,role,True)
  exec_cand=super()._candidate_from_levels_v8(side,role,False)
  if exec_cand is None:return pair_cand
  ep,eq,eproj,esp=exec_cand
  if self._pair_ok(side,ep):self.coreReserve['BEST_ALREADY_PAIR_COMPATIBLE']+=1;return exec_cand
  opp='DOWN' if side=='UP' else 'UP';avg=self.unmatched_avg(opp)
  if avg is None:return pair_cand
  repair_qty=float((esp or {}).get('repairQty') or 0.0);damage=max(0.0,float(avg)+float(ep)-1.0)*repair_qty
  reserve=max(0.0,float(self.pairReserve))
  if damage<=reserve+EPS:
   self.coreReserve['EXECUTION_CORE_FUNDED_BY_REALIZED_PAIR_RESERVE']+=1
   self.coreDecisions.append({'side':side,'executionPrice':ep,'pairPrice':pair_cand[0] if pair_cand else None,'repairQty':repair_qty,'pairDamage':damage,'pairReserveBefore':reserve,'chosen':'EXECUTION'})
   return exec_cand
  self.coreReserve['PAIR_RESERVE_INSUFFICIENT_KEEP_ECONOMIC_CORE']+=1
  self.coreDecisions.append({'side':side,'executionPrice':ep,'pairPrice':pair_cand[0] if pair_cand else None,'repairQty':repair_qty,'pairDamage':damage,'pairReserveBefore':reserve,'chosen':'PAIR'})
  return pair_cand
 def run_d8(self,winner):
  r=super().run_v88(winner);r['pairReserveCoreRouting']=dict(self.coreReserve);r['pairReserveCoreDecisions']=self.coreDecisions[:500];r['finalPairReserve']=float(self.pairReserve);return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d8_pair_reserve_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
  for mid in mids:
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';c=r1.QueueAwareRepairRoutingSim(tape,4)
   try:r0=c.run_v88(cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0});s=PairReserveFundedCore(tape,4)
   try:r=s.run_d8(cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'cell':'MS4_D8_PAIR_RESERVE_FUNDED_CORE','winnerPostHocOnly':cr['winner'],**r})
   print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d8Sub':r['submits'],'r1Fill':r0['fillEvents'],'d8Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d8Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d8Floor':r['floor'],'route':r['pairReserveCoreRouting'],'pairReserve':r['finalPairReserve'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
  c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_D8_PAIR_RESERVE_FUNDED_CORE'};cmp=[{'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor']} for m in mids]
  out={'version':'MS4_D8_PAIR_RESERVE_FUNDED_CORE_20260905','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapsePass':all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['Pair-compatible ECONOMIC_CORE remains default','best-price non-pair Core allowed only when its realized pair damage is covered by already-realized positive pairReserve','no future/pending Repair credit','Repair/Overflow split and debt reservation unchanged','no new hard safety','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
