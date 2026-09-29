from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics,math
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2

EXTRA=['reserved_up','reserved_down','effective_up','effective_down','effective_gross','effective_net','effective_abs_net','effective_pair_coverage','effective_weak_gap']
FEATURES=v1.FEATURES+EXTRA

class ReservedAwareSim(v1.Sim):
 def features(self,t,qv,ca,end):
  base=v1.Sim.features(self,t,qv,ca,end)
  ru=self.reserved('UP'); rd=self.reserved('DOWN')
  eu=self.inv['UP']+ru; ed=self.inv['DOWN']+rd; eg=eu+ed; en=eu-ed; ea=abs(en); ep=2*min(eu,ed)/eg if eg>v1.EPS else 0.; ew=ea
  extra=np.asarray([ru,rd,eu,ed,eg,en,ea,ep,ew],np.float32)
  return np.concatenate([base,extra])

class ReservedAwareBootSim(ReservedAwareSim):
 def __init__(self,tape,mode,models):
  super().__init__(tape,None,'FORCE_UP'); self.mode=mode; self.models=models
 def seed_if_needed(self,t,qv):
  if self.seeded or self.firstValid is None or t-self.firstValid<2000:return
  if self.mode=='FORCE_UP': side='UP'
  elif self.mode=='FORCE_DOWN': side='DOWN'
  elif self.mode=='CHEAP_BID': side='UP' if float(qv['UP']['bid'])<=float(qv['DOWN']['bid']) else 'DOWN'
  elif self.mode=='BOOK_IMBALANCE': side='UP' if float(qv['imb'])>=0 else 'DOWN'
  elif self.mode=='MODEL_SIDE':
   end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs']);ca={'add':0.,'cut':0.,'bidadd':0.,'askadd':0.,'bidcut':0.,'askcut':0.};x=self.features(t,qv,ca,end).reshape(1,-1);ps=float(self.models['side'].predict_proba(x)[0,1]);side='UP' if ps>=self.models['sideTh'] else 'DOWN'
  else: raise ValueError(self.mode)
  p=float(qv[side]['bid']);qty=max(5.,1/p);self.submit(t,side,p,qty);self.seeded=True

 def run_student(self,models,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs']);maxEff=0.;maxRes=0.
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   ru=self.reserved('UP');rd=self.reserved('DOWN');maxRes=max(maxRes,ru+rd);maxEff=max(maxEff,abs((self.inv['UP']+ru)-(self.inv['DOWN']+rd)))
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
   if pa<models['actionTh']:continue
   ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.);self.submit(t,side,p,qty)
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN';oppP=self.inv[opp]-self.cost
  return {'pnl':pnl,'oppositePnl':oppP,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'up':self.inv['UP'],'down':self.inv['DOWN'],'maxEffectiveAbsNet':maxEff,'maxReservedShares':maxRes}

def fit_models(X,yA,yS,yQ,market): return v1.fit_models(X,yA,yS,yQ,market)

def train(tmp,cohort,traj):
 train=[r for r in cohort if r['split']=='TRAIN40'];X1=[];A1=[];S1=[];Q1=[];M1=[]
 for i,cr in enumerate(train,1):
  for seed in v1.SEEDS:
   sim=ReservedAwareSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
   try:x,b,c,d=sim.run_oracle_collect()
   finally:sim.close()
   X1.extend(x);A1.extend(b);S1.extend(c);Q1.extend(d);M1.extend([int(cr['marketId'])]*len(x))
  if i%10==0:print(json.dumps({'round1':i,'rows':len(X1),'actions':int(sum(A1))}),flush=True)
 models1,off1=fit_models(X1,A1,S1,Q1,M1)
 X2=[];A2=[];S2=[];Q2=[];M2=[]
 for i,cr in enumerate(train,1):
  for seed in v1.SEEDS:
   sim=ReservedAwareSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
   try:x,b,c,d=v2.student_collect(sim,models1)
   finally:sim.close()
   X2.extend(x);A2.extend(b);S2.extend(c);Q2.extend(d);M2.extend([int(cr['marketId'])]*len(x))
  if i%10==0:print(json.dumps({'round2':i,'rows':len(X2),'actions':int(sum(A2))}),flush=True)
 models2,off2=fit_models(X1+X2,A1+A2,S1+S2,Q1+Q2,M1+M2);return models2,off1,off2,len(X2),int(sum(A2))

def agg(rs):
 buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs);a=[r for r in rs if r['buyNotional']>v1.EPS]
 return {'markets':len(rs),'activeMarkets':len(a),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'activeWinRate':sum(r['pnl']>0 for r in a)/len(a) if a else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'worstCounterfactual':min(r['oppositePnl'] for r in rs),'meanMaxEffectiveAbsNet':statistics.mean(r['maxEffectiveAbsNet'] for r in rs),'meanMaxReservedShares':statistics.mean(r['maxReservedShares'] for r in rs)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_reserved_state_v1_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2,n2,a2=train(tmp,cohort,traj);test=[r for r in cohort if r['split']=='TEST20'];rows=[]
  for mode in ('BOOK_IMBALANCE',):
   for cr in test:
    sim=ReservedAwareBootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",mode,models)
    try:r=sim.run_student(models,cr['winner'])
    finally:sim.close()
    r.update({'marketId':int(cr['marketId']),'mode':mode,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']});rows.append(r)
  summary=agg(rows);summary['mode']='BOOK_IMBALANCE_RESERVED_AWARE'
  extreme=[r for r in rows if r['marketId'] in (1809466,1809459,1811393,1808859)]
  out={'version':'ETH_DAGGER60_RESERVED_STATE_V1','boundary':['same TRAIN40 two-round DAgger structure as bootstrap-v3','new state explicitly includes live reserved UP/DOWN and effective filled+reserved inventory','TEST20 BOOK_IMBALANCE bootstrap only','no Target trajectory/objective/outcome at runtime','Maker-only best-bid post-only 5s TTL; <=180s no new exposure'],'features':FEATURES,'round1Offline':off1,'round2Offline':off2,'round2Rows':n2,'round2Actions':a2,'summary':summary,'extremeRows':extreme,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary,'extremeRows':extreme},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
