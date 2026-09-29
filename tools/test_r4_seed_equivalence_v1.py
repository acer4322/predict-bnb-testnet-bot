from __future__ import annotations
import json, math, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from predict_bot.r3s_active_stack_v110 import R3SActiveStackV110
from predict_bot.r4_seed_active_stack_v0 import R4SeedActiveStackV0
from predict_bot.r4_economic_safe_base_stack_v0 import R4EconomicSafeBaseStackV0
from predict_bot.r3s_wtp_v1 import R3SWillingnessToPayV1
from predict_bot.r4_seed_wtp_v0 import R4SeedWillingnessToPayV0
from predict_bot.r4_economic_safe_base_wtp_v0 import R4EconomicSafeBaseWillingnessToPayV0

class Inv:
 def __init__(self):
  self.events=[
   {'event_ms':1000,'role':'MAKER','side':'UP','price':.42,'shares':10},
   {'event_ms':4000,'role':'MAKER','side':'DOWN','price':.48,'shares':10},
   {'event_ms':9000,'role':'MAKER','side':'UP','price':.38,'shares':10},
   {'event_ms':12000,'role':'TAKER','side':'DOWN','price':.44,'shares':7.5},
  ]
 def features(self,at_ms):
  up=down=cost=0.0
  for e in self.events:
   if e['event_ms']>=at_ms: continue
   if e['side']=='UP':up+=e['shares']
   else:down+=e['shares']
   cost+=e['price']*e['shares']
  gross=up+down; ab=abs(up-down); floor=min(up,down)-cost; upside=max(up,down)-cost
  return {'combined_gross':gross,'combined_abs_net':ab,'combined_paired_coverage':2*min(up,down)/gross if gross else 0,
          'worst_case_floor':floor,'best_case_pnl':upside,'combined_net':up-down}

def close(a,b,tol=1e-10):
 if isinstance(a,dict) and isinstance(b,dict):
  ka=set(a)-{'version'}; kb=set(b)-{'version'}
  return ka==kb and all(close(a[k],b[k],tol) for k in ka)
 if isinstance(a,(int,float)) and isinstance(b,(int,float)):
  if math.isnan(float(a)) and math.isnan(float(b)): return True
  return abs(float(a)-float(b))<=tol
 return a==b

def main():
 d=ROOT/'data/research/r3_v0'; r3=R3SActiveStackV110(d); seed=R4SeedActiveStackV0(d); champ=R4EconomicSafeBaseStackV0(d)
 inv=Inv(); snap={'secondsLeft':250,'marketId':1}; now=15000
 a=r3.pre_add_evidence(inv,now,snap); b=seed.pre_add_evidence(inv,now,snap); c=champ.pre_add_evidence(inv,now,snap)
 w=[]
 for cls in [R3SWillingnessToPayV1,R4SeedWillingnessToPayV0,R4EconomicSafeBaseWillingnessToPayV0]:
  x=cls(); q1=x.quote(structural_effect='ADD_EFFECT',side='UP',observed_ask=.37,now_ms=1000,port=inv.features(now)); q2=x.quote(structural_effect='ADD_EFFECT',side='UP',observed_ask=.45,now_ms=5000,port=inv.features(now)); q3=x.quote(structural_effect='REPAIR_EFFECT',side='DOWN',observed_ask=.44,now_ms=6000,port=inv.features(now)); w.append((q1,q2,q3))
 checks={'preAddSeedEqualsR3':close(a,b),'preAddChampionEqualsSeed':close(b,c),'structuralEffectEquals':r3.structural_effect('DOWN',10)==seed.structural_effect('DOWN',10)==champ.structural_effect('DOWN',10),'wtpSeedEqualsR3':all(close(x,y) for x,y in zip(w[0],w[1])),'wtpChampionEqualsSeed':all(close(x,y) for x,y in zip(w[1],w[2]))}
 out={'version':'R4_SEED_EQUIVALENCE_V1','checks':checks,'allPass':all(checks.values())}; print(json.dumps(out))
 if not out['allPass']: raise SystemExit(2)
if __name__=='__main__':main()
