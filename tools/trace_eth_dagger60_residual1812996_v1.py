from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,math
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_pending_responsibility_conservation_v1 as pr

MID=1812996

class TraceConserved(pr.ConservedBootSim):
 def run_trace(self,models,winner):
  trace=[];fills=[];last_inv=(0.,0.);ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t)
   if (self.inv['UP'],self.inv['DOWN'])!=last_inv:
    gross=self.inv['UP']+self.inv['DOWN'];base=min(self.inv.values());fills.append({'t':t,'secondsLeft':(end-t)/1000.,'up':self.inv['UP'],'down':self.inv['DOWN'],'cost':self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'pairCoverage':2*base/gross if gross>v1.EPS else 0.,'floor':base-self.cost,'bestPnl':max(self.inv.values())-self.cost,'pairReserve':self.pairReserve,'pairedQty':self.pairedQty,'reservedUp':self.reserved('UP'),'reservedDown':self.reserved('DOWN')});last_inv=(self.inv['UP'],self.inv['DOWN'])
   self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   before_seed=self.seeded;self.seed_if_needed(t,qv)
   if self.seeded and not before_seed:trace.append({'kind':'SEED','t':t,'side':'UP' if qv['imb']>=0 else 'DOWN','bookImbalance':qv['imb'],'reservedUp':self.reserved('UP'),'reservedDown':self.reserved('DOWN')})
   if not self.seeded:continue
   arr=self.features(t,qv,ca,end);fd={k:float(arr[i]) for i,k in enumerate(v1.FEATURES)};x=arr.reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
   if pa<models['actionTh']:continue
   ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.);ru=self.reserved('UP');rd=self.reserved('DOWN');blocked=(ru>v1.EPS if side=='UP' else rd>v1.EPS)
   trace.append({'kind':'ACTION_SIGNAL','t':t,'secondsLeft':(end-t)/1000.,'pa':pa,'actionTh':float(models['actionTh']),'psUp':ps,'side':side,'price':p,'qty':qty,'blockedDuplicate':blocked,'reservedUp':ru,'reservedDown':rd,'state':fd})
   self.submit(t,side,p,qty)
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;opp='UP' if str(winner).upper()=='DOWN' else 'DOWN';opp_pnl=self.inv[opp]-self.cost;gross=sum(self.inv.values());base=min(self.inv.values());return {'pnl':pnl,'oppositePnl':opp_pnl,'buyNotional':self.cost,'up':self.inv['UP'],'down':self.inv['DOWN'],'absNet':abs(self.inv['UP']-self.inv['DOWN']),'pairCoverage':2*base/gross if gross>v1.EPS else 0.,'floor':base-self.cost,'submits':self.submits,'fills':self.fills,'duplicateBlocked':self.duplicateBlocked,'trace':trace,'fillTrace':fills}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_residual_trace_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=pr.train_models(tmp,cohort,traj);test={int(r['marketId']):r for r in cohort if r['split']=='TEST20'};cr=test[MID];sim=TraceConserved(tmp/'tapes'/f'{MID}.json.xz','BOOK_IMBALANCE',models)
  try:r=sim.run_trace(models,cr['winner'])
  finally:sim.close()
  r.update({'marketId':MID,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']});out={'version':'ETH_DAGGER60_RESIDUAL_1812996_TRACE_V1','boundary':['exact pending-responsibility-conservation runtime','exact bootstrap-v3 two-round DAgger retrain','instrumentation only'],'round1Offline':off1,'round2Offline':off2,'row':r};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'result':{k:v for k,v in r.items() if k not in ('trace','fillTrace')},'traceEvents':len(r['trace']),'fillEventsStored':len(r['fillTrace'])},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
