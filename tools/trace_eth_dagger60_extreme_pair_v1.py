from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,math
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2
import run_eth_dagger60_bootstrap_v3 as v3

TRACE_MIDS={1809466,1808859}

class TraceBootSim(v3.BootSim):
 def run_student_trace(self,models,winner):
  trace=[]; fills=[]; thresholds={20:False,30:False,40:False,60:False,100:False}
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
  first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first)
  end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  last_inv=(0.,0.)
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t)
   if (self.inv['UP'],self.inv['DOWN'])!=last_inv:
    gross=self.inv['UP']+self.inv['DOWN'];base=min(self.inv.values())
    fills.append({'t':t,'secondsLeft':(end-t)/1000.,'up':self.inv['UP'],'down':self.inv['DOWN'],'cost':self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'pairCoverage':2*base/gross if gross>v1.EPS else 0.,'floor':base-self.cost,'pairReserve':self.pairReserve,'pairedQty':self.pairedQty})
    last_inv=(self.inv['UP'],self.inv['DOWN'])
   self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   arr=self.features(t,qv,ca,end); fd={k:float(arr[i]) for i,k in enumerate(v1.FEATURES)}
   x=arr.reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
   # record threshold crossings even without action signal
   for th in thresholds:
    if not thresholds[th] and fd['abs_net']>=th:
     thresholds[th]=True;trace.append({'kind':'ABSNET_CROSS','threshold':th,'t':t,'state':fd.copy()})
   if pa<models['actionTh']:continue
   ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
   evt={'kind':'SUBMIT_SIGNAL','t':t,'secondsLeft':(end-t)/1000.,'pa':pa,'actionTh':float(models['actionTh']),'psUp':ps,'sideTh':float(models['sideTh']),'side':side,'price':p,'qty':qty,'state':fd.copy(),'reservedUp':self.reserved('UP'),'reservedDown':self.reserved('DOWN')}
   trace.append(evt);self.submit(t,side,p,qty)
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
  pnl=self.inv.get(str(winner).upper(),0.)-self.cost;opp='UP' if str(winner).upper()=='DOWN' else 'DOWN';opp_pnl=self.inv[opp]-self.cost;gross=sum(self.inv.values());base=min(self.inv.values())
  return {'pnl':pnl,'oppositePnl':opp_pnl,'buyNotional':self.cost,'up':self.inv['UP'],'down':self.inv['DOWN'],'absNet':abs(self.inv['UP']-self.inv['DOWN']),'pairCoverage':2*base/gross if gross>v1.EPS else 0.,'floor':base-self.cost,'submits':self.submits,'fills':self.fills,'trace':trace,'fillTrace':fills}

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

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_extreme_trace_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=train_models(tmp,cohort,traj);test={int(r['marketId']):r for r in cohort if r['split']=='TEST20'};rows=[]
  for mid in sorted(TRACE_MIDS):
   cr=test[mid];sim=TraceBootSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models)
   try:r=sim.run_student_trace(models,cr['winner'])
   finally:sim.close()
   r.update({'marketId':mid,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']});rows.append(r);print(json.dumps({'traced':mid,'pnl':r['pnl'],'oppositePnl':r['oppositePnl'],'events':len(r['trace'])}),flush=True)
  out={'version':'ETH_DAGGER60_EXTREME_PAIR_TRACE_V1','boundary':['exact bootstrap-v3 two-round DAgger retrain','TEST tracing only for 1809466 and 1808859','BOOK_IMBALANCE bootstrap frozen','instrumentation only; no strategy change'],'round1Offline':off1,'round2Offline':off2,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'rows':[{k:v for k,v in r.items() if k not in ('trace','fillTrace')} for r in rows]},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
