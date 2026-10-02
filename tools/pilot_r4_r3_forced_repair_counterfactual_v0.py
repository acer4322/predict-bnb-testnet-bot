from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path.cwd();sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl

def first_candidate(decisions,age_ms=5000):
 risk_since=None
 for d in decisions:
  p=d.get('portfolio') or {};t=int(d.get('decisionMs') or 0);absn=float(p.get('maker_abs_net') or 0);pc=float(p.get('maker_paired_coverage') or 0);risky=(d.get('episode') is None and absn>=18-1e-9 and pc<.80)
  if not risky:risk_since=None;continue
  if risk_since is None:risk_since=t
  if t-risk_since>=age_ms:return {'decisionMs':t,'riskSinceMs':risk_since,'riskAgeMs':t-risk_since,'makerAbsNet':absn,'makerPairedCoverage':pc}
 return None

def run_forced(mid,force_after_ms):
 orig=base.new_controller;applied=[]
 def injected_new(a):
  c=orig(a);os=c._step
  def step(s):
   now=int(s.get('sampledAtMs') or 0)
   if not applied and now>int(force_after_ms) and c.episode is None:
    net=float(c.inventory.maker_up-c.inventory.maker_down);gross=float(c.inventory.maker_up+c.inventory.maker_down);pc=2*min(c.inventory.maker_up,c.inventory.maker_down)/gross if gross>1e-9 else 0.;ab=abs(net)
    if ab>1:
     side='UP' if net>0 else 'DOWN';c.episode={'kind':'RESIDUAL','side':side,'risk_start_ms':now,'risk_pre_abs':ab,'start_ms':now,'pre_abs':ab,'start_abs':ab,'expansion':0.,'pre_pc':pc,'unresolved':False};c.readiness=True;applied.append({'atMs':now,'side':side,'makerNet':net,'makerAbs':ab,'makerPairedCoverage':pc,'requestedAfterMs':int(force_after_ms)})
   return os(s)
  c._step=step;return c
 base.new_controller=injected_new
 try:r=r3ctl.run_market(mid,True)
 finally:base.new_controller=orig
 r['forcedRepairWakePilot']=applied;return r

def compact(r):
 s=r['studentRollout'];p=s['finalPortfolio'];return {'makerFillEvents':s['makerFillEvents'],'makerFilledShares':s['makerFilledShares'],'takerFills':s['takerFills'],'finalFloor':p.get('worst_case_floor'),'finalAbsNet':p.get('combined_abs_net'),'makerCostUsdt':s.get('makerCostUsdt'),'takerCostUsdt':s.get('takerCostUsdt'),'forced':r.get('forcedRepairWakePilot') or []}
def main():
 import argparse;ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);a=ap.parse_args();b=r3ctl.run_market(a.market_id,True);cand=first_candidate(b.get('decisionRows') or []);out={'version':'R4_R3_FORCED_REPAIR_COUNTERFACTUAL_PILOT_V0','researchOnly':True,'actionAuthority':False,'marketId':a.market_id,'candidate':cand,'baseline':compact(b),'counterfactual':None,'trajectoryChanged':False,'boundary':'One-snapshot-delayed forced RESIDUAL wake injected before the first snapshot strictly after the baseline persistent-risk candidate. Infrastructure pilot only; not causal promotion evidence.'}
 if cand:
  c=run_forced(a.market_id,cand['decisionMs']);out['counterfactual']=compact(c);out['trajectoryChanged']=out['baseline']!=out['counterfactual']
 p=ROOT/'data/research/r4_v0/p0_provenance_v1'/f'r4_r3_forced_repair_counterfactual_pilot_market{a.market_id}_v0.json';p.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
