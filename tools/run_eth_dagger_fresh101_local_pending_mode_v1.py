from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--mode',required=True,choices=['FORCE_UP','FORCE_DOWN','CHEAP_BID','BOOK_IMBALANCE','MODEL_SIDE']);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_f101_mode_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r.get('split')!='TRAIN40'];rows=[]
  for i,cr in enumerate(test,1):
   sim=lp.LocalReservedBootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",a.mode,models)
   try:r=sim.run_student(models,cr['winner'])
   finally:sim.close()
   r.update({'marketId':int(cr['marketId']),'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy'],'mode':a.mode,'duplicateBlocked':sim.duplicateBlocked,'localPendingBlocked':sim.localPendingBlocked});opp='UP' if cr['winner']=='DOWN' else 'DOWN';r['oppositePnl']=r[opp.lower()]-r['buyNotional'];rows.append(r)
   if i%20==0 or i==len(test):print(json.dumps({'mode':a.mode,'testProgress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
  summary=lp.agg(rows);summary['mode']=a.mode;summary['testMarkets']=len(test)
  out={'version':'ETH_DAGGER_FRESH101_LOCAL_PENDING_MODE_V1','boundary':['Fresh101 consumed development diagnostic only','local pending reservation semantics fixed','only bootstrap mode varies','same frozen DAgger model/training','no threshold sweep'],'round1Offline':off1,'round2Offline':off2,'summary':summary,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
