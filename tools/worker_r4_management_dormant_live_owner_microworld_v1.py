from __future__ import annotations
import argparse,json,math,random,time

def run(n,seed=20260829):
 rng=random.Random(seed); bins={(a,l):[0,0,0] for a in ('YOUNG','MID','OLD') for l in (0,1)}
 # Mechanistic stress world only: age lowers NEW-opportunity hazard, but a still-live carrier keeps independent fill hazard.
 # This tests whether age can be a safe proxy for inactivity when ownership continuity is omitted. It is not calibrated to Target.
 for _ in range(n):
  age=rng.uniform(0,140); live=1 if rng.random()<0.45 else 0; reserved=rng.choice([0,6,12,18,24,36]); quiet=rng.uniform(0,40)
  new_h=max(.01,.38*math.exp(-age/35.0)*math.exp(-quiet/20.0))
  carrier_h=(.0 if not live or reserved<=0 else min(.72,.12+.012*reserved))
  event=1 if rng.random()<1-(1-new_h)*(1-carrier_h) else 0
  b='YOUNG' if age<20 else 'MID' if age<60 else 'OLD';z=bins[(b,live)];z[0]+=1;z[1]+=event;z[2]+=reserved
 out=[]
 for (age,live),(cnt,ev,res) in bins.items():out.append({'ageBin':age,'liveCarrier':live,'n':cnt,'eventRate':ev/cnt if cnt else 0,'meanReservedQty':res/cnt if cnt else 0})
 return {'version':'R4_MANAGEMENT_DORMANT_LIVE_OWNER_MICROWORLD_V1','researchOnly':True,'promotionEvidence':False,'episodes':n,'bins':out,'guard':'Synthetic mechanistic stress only. Hazards are deliberately assumed, not fit. Purpose: test architecture failure modes and whether objective age is unsafe without live-carrier continuity; never promotion evidence.'}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--episodes',type=int,default=300000);a=ap.parse_args();t=time.perf_counter();r=run(a.episodes);r['elapsedSec']=time.perf_counter()-t;print(json.dumps(r,indent=2))
