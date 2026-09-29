from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics,math
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2
import run_eth_dagger60_bootstrap_v3 as v3

class CarrierBootSim(v3.BootSim):
 def __init__(self,tape,mode,models,carrier_gate=False):
  super().__init__(tape,mode,models);self.carrier_gate=carrier_gate;self.blockedDuplicate=0;self.maxReserved=0.;self.maxEffectiveAbsNet=0.
 def submit(self,t,side,p,q):
  if self.carrier_gate and self.reserved(side)>v1.EPS:
   self.blockedDuplicate+=1;return False
  v1.Sim.submit(self,t,side,p,q);return True
 def run_student_carrier(self,models,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   ru=self.reserved('UP');rd=self.reserved('DOWN');self.maxReserved=max(self.maxReserved,ru+rd);self.maxEffectiveAbsNet=max(self.maxEffectiveAbsNet,abs((self.inv['UP']+ru)-(self.inv['DOWN']+rd)))
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
   if pa<models['actionTh']:continue
   ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.);self.submit(t,side,p,qty)
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;opp='UP' if str(winner).upper()=='DOWN' else 'DOWN';oppP=self.inv[opp]-self.cost;gross=sum(self.inv.values());base=min(self.inv.values())
  return {'pnl':pnl,'oppositePnl':oppP,'buyNotional':self.cost,'pairCoverage':2*base/gross if gross>v1.EPS else 0.,'floor':base-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'up':self.inv['UP'],'down':self.inv['DOWN'],'blockedDuplicate':self.blockedDuplicate,'maxReservedShares':self.maxReserved,'maxEffectiveAbsNet':self.maxEffectiveAbsNet}

def train_models(tmp,cohort,traj):
 train=[r for r in cohort if r['split']=='TRAIN40'];X1=[];A1=[];S1=[];Q1=[];M1=[]
 for cr in train:
  for seed in v1.SEEDS:
   sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
   try:x,b,c,d=sim.run_oracle_collect()
   finally:sim.close()
   X1.extend(x);A1.extend(b);S1.extend(c);Q1.extend(d);M1.extend([int(cr['marketId'])]*len(x))
 models1,off1=v1.fit_models(X1,A1,S1,Q1,M1)
 X2=[];A2=[];S2=[];Q2=[];M2=[]
 for i,cr in enumerate(train,1):
  for seed in v1.SEEDS:
   sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
   try:x,b,c,d=v2.student_collect(sim,models1)
   finally:sim.close()
   X2.extend(x);A2.extend(b);S2.extend(c);Q2.extend(d);M2.extend([int(cr['marketId'])]*len(x))
  if i%10==0:print(json.dumps({'trainProgress':i,'rows2':len(X2),'actions2':int(sum(A2))}),flush=True)
 models2,off2=v1.fit_models(X1+X2,A1+A2,S1+S2,Q1+Q2,M1+M2);return models2,off1,off2

def agg(rs):
 buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs);a=[r for r in rs if r['buyNotional']>v1.EPS]
 wins=sum(r['pnl']>0 for r in a);sortedp=sorted((r['pnl'] for r in rs),reverse=True);lob=p-(sortedp[0] if sortedp else 0.)
 return {'markets':len(rs),'activeMarkets':len(a),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'activeWinRate':wins/len(a) if a else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'worstCounterfactual':min(r['oppositePnl'] for r in rs),'leaveOneBestOutPnl':lob,'meanBlockedDuplicate':statistics.mean(r['blockedDuplicate'] for r in rs),'meanMaxReservedShares':statistics.mean(r['maxReservedShares'] for r in rs),'meanMaxEffectiveAbsNet':statistics.mean(r['maxEffectiveAbsNet'] for r in rs)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_carrier_cons_v1_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']=='TEST20'];rows=[]
  for name,gate in [('BASELINE',False),('ONE_LIVE_CARRIER_PER_SIDE',True)]:
   for cr in test:
    sim=CarrierBootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,carrier_gate=gate)
    try:r=sim.run_student_carrier(models,cr['winner'])
    finally:sim.close()
    r.update({'marketId':int(cr['marketId']),'mode':name,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']});rows.append(r)
  sums=[]
  for name in ('BASELINE','ONE_LIVE_CARRIER_PER_SIDE'):
   s=agg([r for r in rows if r['mode']==name]);s['mode']=name;sums.append(s)
  extreme=[r for r in rows if r['mode']=='ONE_LIVE_CARRIER_PER_SIDE' and r['marketId'] in (1809466,1809459,1811393,1808859)]
  out={'version':'ETH_DAGGER60_CARRIER_CONSERVATION_V1','boundary':['exact bootstrap-v3 TRAIN40 two-round DAgger model','TEST20 BOOK_IMBALANCE bootstrap; no Target runtime data','candidate does not alter model/features/thresholds','candidate only prevents a second live Maker carrier on the same side until prior carrier fills/cancels/expires','Maker-only best-bid post-only 5s TTL; <=180s no new exposure'],'round1Offline':off1,'round2Offline':off2,'summaries':sums,'extremeRows':extreme,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summaries':sums,'extremeRows':extreme},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
