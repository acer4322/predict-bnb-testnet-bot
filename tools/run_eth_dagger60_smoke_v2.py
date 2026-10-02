from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1

def student_collect(sim,models):
 X=[];ya=[];ys=[];yq=[];ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
 for u in ups:
  t=int(u[1]);v1.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);ca=v1.apply(sim.book,u);sim.advance_target(t);qv=v1.quotes(sim.book)
  if not qv:continue
  if sim.firstValid is None:sim.firstValid=t
  if (end-t)/1000.<=180:continue
  sim.seed_if_needed(t,qv)
  if not sim.seeded:continue
  x=sim.features(t,qv,ca,end);act,oside,oqty=sim.oracle_action(qv);X.append(x);ya.append(act);ys.append(1 if oside=='UP' else 0);yq.append(np.log1p(oqty) if act else 0.)
  xx=x.reshape(1,-1);pa=float(models['action'].predict_proba(xx)[0,1])
  if pa<models['actionTh']:continue
  ps=float(models['side'].predict_proba(xx)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(xx)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.);sim.submit(t,side,p,qty)
 return X,ya,ys,yq

def eval_models(tmp,test,models):
 rows=[]
 for seed in v1.SEEDS:
  for cr in test:
   sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",None,seed)
   try:r=sim.run_student(models,cr['winner'])
   finally:sim.close()
   r.update({'marketId':int(cr['marketId']),'seed':seed,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']});rows.append(r)
 sums=[]
 for seed in v1.SEEDS:
  s=v1.agg([r for r in rows if r['seed']==seed]);s['seed']=seed;sums.append(s)
 return rows,sums

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger60v2_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));train=[r for r in cohort if r['split']=='TRAIN40'];test=[r for r in cohort if r['split']=='TEST20']
  X1=[];A1=[];S1=[];Q1=[];M1=[]
  for i,cr in enumerate(train,1):
   for seed in v1.SEEDS:
    sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
    try:x,b,c,d=sim.run_oracle_collect()
    finally:sim.close()
    X1.extend(x);A1.extend(b);S1.extend(c);Q1.extend(d);M1.extend([int(cr['marketId'])]*len(x))
  models1,off1=v1.fit_models(X1,A1,S1,Q1,M1);rows1,sum1=eval_models(tmp,test,models1);print(json.dumps({'round1Done':True,'rows':len(X1),'actions':sum(A1),'test':sum1}),flush=True)
  X2=[];A2=[];S2=[];Q2=[];M2=[]
  for i,cr in enumerate(train,1):
   for seed in v1.SEEDS:
    sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
    try:x,b,c,d=student_collect(sim,models1)
    finally:sim.close()
    X2.extend(x);A2.extend(b);S2.extend(c);Q2.extend(d);M2.extend([int(cr['marketId'])]*len(x))
   if i%10==0:print(json.dumps({'round2CollectProgress':i,'rows':len(X2),'actions':sum(A2)}),flush=True)
  XA=X1+X2;AA=A1+A2;SA=S1+S2;QA=Q1+Q2;MA=M1+M2;models2,off2=v1.fit_models(XA,AA,SA,QA,MA);rows2,sum2=eval_models(tmp,test,models2)
  targetBuy=sum(r['targetBuy'] for r in test);targetP=sum(r['targetPnl'] for r in test);target={'markets':len(test),'pnl':targetP,'buyNotional':targetBuy,'roi':targetP/targetBuy if targetBuy else None,'winRate':sum(r['targetPnl']>0 for r in test)/len(test)}
  out={'version':'ETH_DAGGER60_SMOKE_V2','boundary':['development-only true second DAgger iteration','round1 oracle-controlled states; round2 student-controlled TRAIN40 states relabeled by strict-past Target Maker objective + hard pair<=1 oracle','TEST20 decisions never access Target trajectory/objective; direct FORCE_UP/FORCE_DOWN seed only','<=180s no new exposure; Maker-only best-bid post-only, 5s TTL'],'features':v1.FEATURES,'round1':{'rows':len(X1),'actions':int(sum(A1)),'offline':off1,'summaries':sum1},'round2Collection':{'rows':len(X2),'actions':int(sum(A2))},'aggregated':{'rows':len(XA),'actions':int(sum(AA)),'offline':off2,'summaries':sum2},'targetTest20':target,'rowsRound1':rows1,'rowsRound2':rows2};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'round1':out['round1'],'round2Collection':out['round2Collection'],'aggregated':out['aggregated'],'target':target},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
