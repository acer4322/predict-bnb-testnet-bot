from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2

MODES=('FORCE_UP','FORCE_DOWN','CHEAP_BID','BOOK_IMBALANCE','MODEL_SIDE')

class BootSim(v1.Sim):
 def __init__(self,tape,mode,models):
  super().__init__(tape,None,'FORCE_UP');self.mode=mode;self.models=models
 def seed_if_needed(self,t,qv):
  if self.seeded or self.firstValid is None or t-self.firstValid<2000:return
  if self.mode=='FORCE_UP':side='UP'
  elif self.mode=='FORCE_DOWN':side='DOWN'
  elif self.mode=='CHEAP_BID':side='UP' if float(qv['UP']['bid'])<=float(qv['DOWN']['bid']) else 'DOWN'
  elif self.mode=='BOOK_IMBALANCE':side='UP' if float(qv['imb'])>=0 else 'DOWN'
  elif self.mode=='MODEL_SIDE':
   end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs']);ca={'add':0.,'cut':0.,'bidadd':0.,'askadd':0.,'bidcut':0.,'askcut':0.};x=self.features(t,qv,ca,end).reshape(1,-1);ps=float(self.models['side'].predict_proba(x)[0,1]);side='UP' if ps>=self.models['sideTh'] else 'DOWN'
  else:raise ValueError(self.mode)
  p=float(qv[side]['bid']);qty=max(5.,1/p);self.submit(t,side,p,qty);self.seeded=True

def eval_modes(tmp,test,models):
 rows=[]
 for mode in MODES:
  for cr in test:
   sim=BootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",mode,models)
   try:r=sim.run_student(models,cr['winner'])
   finally:sim.close()
   r.update({'marketId':int(cr['marketId']),'mode':mode,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']});rows.append(r)
 sums=[]
 for mode in MODES:
  s=v1.agg([r for r in rows if r['mode']==mode]);s['mode']=mode;sums.append(s)
 return rows,sums

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_bootv3_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));train=[r for r in cohort if r['split']=='TRAIN40'];test=[r for r in cohort if r['split']=='TEST20']
  X1=[];A1=[];S1=[];Q1=[];M1=[]
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
   if i%10==0:print(json.dumps({'collect':i,'rows2':len(X2),'actions2':int(sum(A2))}),flush=True)
  models2,off2=v1.fit_models(X1+X2,A1+A2,S1+S2,Q1+Q2,M1+M2);rows,sums=eval_modes(tmp,test,models2)
  tb=sum(r['targetBuy'] for r in test);tp=sum(r['targetPnl'] for r in test);target={'markets':len(test),'pnl':tp,'buyNotional':tb,'roi':tp/tb if tb else None,'winRate':sum(r['targetPnl']>0 for r in test)/len(test)}
  out={'version':'ETH_DAGGER60_BOOTSTRAP_V3','boundary':['TRAIN40 two-round DAgger as V2','TEST20 student decisions never access Target objective/trajectory','bootstrap modes use only fixed direction, public book, or frozen student side model; no Target future/outcome','<=180s no new exposure; Maker-only best-bid post-only, 5s TTL'],'round1Offline':off1,'round2Offline':off2,'round2Rows':len(X2),'round2Actions':int(sum(A2)),'targetTest20':target,'summaries':sums,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'target':target,'summaries':sums},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
