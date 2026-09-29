from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_bootstrap_v3 as v3
import run_eth_dagger60_pending_responsibility_conservation_v1 as pr

def agg(rs):
 buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs);active=[r for r in rs if r['buyNotional']>v1.EPS]
 return {'markets':len(rs),'activeMarkets':len(active),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs) if rs else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'maxCounterfactualAdverseOnWinner':min((r['oppositePnl'] for r in rs if r['pnl']>0),default=None)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_fresh101_ab_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=pr.train_models(tmp,cohort,traj);test=[r for r in cohort if r.get('split')!='TRAIN40'];allrows=[]
  for mode in ('BASELINE','ONE_LIVE_CARRIER_PER_SIDE'):
   rows=[]
   for i,cr in enumerate(test,1):
    if mode=='BASELINE': sim=v3.BootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
    else: sim=pr.ConservedBootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
    try:r=sim.run_student(models,cr['winner'])
    finally:sim.close()
    r.update({'marketId':int(cr['marketId']),'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy'],'mode':mode});opp='UP' if cr['winner']=='DOWN' else 'DOWN';r['oppositePnl']=r[opp.lower()]-r['buyNotional'];rows.append(r);allrows.append(r)
    if i%20==0 or i==len(test):print(json.dumps({'mode':mode,'testProgress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
   print(json.dumps({'modeDone':mode,'summary':agg(rows)}),flush=True)
  sums=[]
  for mode in ('BASELINE','ONE_LIVE_CARRIER_PER_SIDE'):
   s=agg([r for r in allrows if r['mode']==mode]);s['mode']=mode;sums.append(s)
  out={'version':'ETH_DAGGER_FRESH101_RESERVATION_AB_V1','boundary':['same frozen DAgger model and Fresh101 cohort','BASELINE versus venue-live one-carrier only','no local-pending candidate modification','no tuning on Fresh101'],'round1Offline':off1,'round2Offline':off2,'summaries':sums,'rows':allrows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summaries':sums},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
