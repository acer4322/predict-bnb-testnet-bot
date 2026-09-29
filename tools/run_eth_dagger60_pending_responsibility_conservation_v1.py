from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2
import run_eth_dagger60_bootstrap_v3 as v3

class ConservedBootSim(v3.BootSim):
 def __init__(self,tape,mode,models):
  super().__init__(tape,mode,models);self.duplicateBlocked=0
 def submit(self,t,side,p,q):
  # Structural pending-responsibility conservation: do not stack a new same-side
  # Maker responsibility while existing same-side leavesQty is still live.
  if self.reserved(side)>v1.EPS:
   self.duplicateBlocked+=1;return False
  super().submit(t,side,p,q);return True

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
 models2,off2=v1.fit_models(X1+X2,A1+A2,S1+S2,Q1+Q2,M1+M2)
 return models2,off1,off2

def agg(rs):
 buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs);active=[r for r in rs if r['buyNotional']>v1.EPS]
 return {'markets':len(rs),'activeMarkets':len(active),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs) if rs else None,'activeWinRate':sum(r['pnl']>0 for r in active)/len(active) if active else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'meanDuplicateBlocked':statistics.mean(r.get('duplicateBlocked',0) for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_pending_resp_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']=='TEST20'];rows=[]
  for i,cr in enumerate(test,1):
   sim=ConservedBootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
   try:r=sim.run_student(models,cr['winner'])
   finally:sim.close()
   r.update({'marketId':int(cr['marketId']),'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy'],'duplicateBlocked':sim.duplicateBlocked});opp='UP' if cr['winner']=='DOWN' else 'DOWN';r['oppositePnl']=r[opp.lower()]-r['buyNotional'];rows.append(r)
   if i%5==0:print(json.dumps({'testProgress':i,'pnlSoFar':sum(x['pnl'] for x in rows)}),flush=True)
  summary=agg(rows);out={'version':'ETH_DAGGER60_PENDING_RESPONSIBILITY_CONSERVATION_V1','boundary':['exact bootstrap-v3 two-round DAgger retrain','TEST20 BOOK_IMBALANCE seed','no Target objective/trajectory/outcome at runtime','only strategy change: block new same-side Maker submit while same-side live leavesQty exists','<=180s no new exposure; Maker-only realistic HFT'],'round1Offline':off1,'round2Offline':off2,'summary':summary,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
