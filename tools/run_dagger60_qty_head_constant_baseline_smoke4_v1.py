from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics,math,hashlib
from pathlib import Path
import numpy as np

STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2
import run_eth_dagger60_local_pending_reservation_v1 as lp

SMOKE_IDS=[1815263,1815269,1816088,1816097]

def sha256(p:Path):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()

def train_models_and_constant(tmp,cohort,traj):
 train=[r for r in cohort if r['split']=='TRAIN40']
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
  if i%10==0: print(json.dumps({'trainProgress':i,'rows2':len(X2),'actions2':int(sum(A2))}),flush=True)
 XA=X1+X2;AA=A1+A2;SA=S1+S2;QA=Q1+Q2;MA=M1+M2
 models2,off2=v1.fit_models(XA,AA,SA,QA,MA)
 ums=sorted(set(int(x) for x in MA));fit=set(ums[:30])
 q=[math.expm1(float(yq)) for ya,yq,m in zip(AA,QA,MA) if int(ya)==1 and int(m) in fit]
 if not q: raise RuntimeError('no positive training qty labels')
 const=float(statistics.median(q))
 return models2,off1,off2,const,{'fitMarkets':sorted(fit),'positiveN':len(q),'medianTeacherQty':const,'meanTeacherQty':float(statistics.mean(q))}

def run_constant(sim,models,winner,const_qty):
 ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
 qty_attempts=[]
 for u in ups:
  t=int(u[1]);v1.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);ca=v1.apply(sim.book,u);qv=v1.quotes(sim.book)
  if not qv:continue
  if sim.firstValid is None:sim.firstValid=t
  if (end-t)/1000.<=180:continue
  sim.seed_if_needed(t,qv)
  if not sim.seeded:continue
  x=sim.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
  if pa<models['actionTh']:continue
  ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';p=float(qv[side]['bid'])
  learned=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));learned=max(learned,1/p);learned=min(learned,12.)
  qty=max(float(const_qty),1/p);qty=min(qty,12.)
  accepted=sim.submit(t,side,p,qty)
  qty_attempts.append({'t':t,'side':side,'price':p,'learnedEffectiveQty':learned,'constantEffectiveQty':qty,'deltaQty':qty-learned,'accepted':bool(accepted)})
 end2=int(sim.meta['lastReceivedMs']);v1.ex.advance_to(sim.bt,end2);sim.process(end2)
 pnl=sim.inv.get(str(winner).upper(),0.)-sim.cost;gross=sum(sim.inv.values())
 return {'pnl':pnl,'buyNotional':sim.cost,'pairCoverage':2*min(sim.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(sim.inv.values())-sim.cost,'bestPnl':max(sim.inv.values())-sim.cost,'absNet':abs(sim.inv['UP']-sim.inv['DOWN']),'submits':sim.submits,'fills':sim.fills,'up':sim.inv['UP'],'down':sim.inv['DOWN'],'qtyAttempts':qty_attempts}

def enrich(r,cr,sim):
 r.update({'marketId':int(cr['marketId']),'winner':cr['winner'],'duplicateBlocked':sim.duplicateBlocked,'localPendingBlocked':sim.localPendingBlocked})
 opp='UP' if cr['winner']=='DOWN' else 'DOWN';r['oppositePnl']=r[opp.lower()]-r['buyNotional']
 if 'bestPnl' not in r:r['bestPnl']=max(r['up'],r['down'])-r['buyNotional']
 return r

def cell_summary(rows):
 p=sum(r['pnl'] for r in rows);wins=[r for r in rows if r['pnl']>0];loss=[r for r in rows if r['pnl']<0];best=max(rows,key=lambda r:r['pnl'])
 eq=0.;peak=0.;mdd=0.
 for r in rows:
  eq+=r['pnl'];peak=max(peak,eq);mdd=max(mdd,peak-eq)
 return {'markets':len(rows),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rows),'pnl':p,'winRate':len(wins)/len(rows),'buyNotional':sum(r['buyNotional'] for r in rows),'fills':sum(r['fills'] for r in rows),'submits':sum(r['submits'] for r in rows),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rows),'meanAbsNet':statistics.mean(r['absNet'] for r in rows),'aggregateFloor':sum(r['floor'] for r in rows),'aggregateBest':sum(r['bestPnl'] for r in rows),'worstLoss':min(r['pnl'] for r in rows),'maxWin':max(r['pnl'] for r in rows),'leaveOneBestOut':p-best['pnl'],'maxSequentialDrawdown':mdd}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();bp=Path(a.bundle);tmp=Path(tempfile.mkdtemp(prefix='dagger_qty_const_smoke4_'))
 try:
  zipfile.ZipFile(bp).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
  models,off1,off2,const,qtymeta=train_models_and_constant(tmp,cohort,traj)
  evalrows=[r for r in cohort if r.get('split')!='TRAIN40' and int(r['marketId']) in SMOKE_IDS]
  evalrows=sorted(evalrows,key=lambda r:SMOKE_IDS.index(int(r['marketId'])))
  if [int(r['marketId']) for r in evalrows]!=SMOKE_IDS:raise RuntimeError(f'eval ids mismatch {[r["marketId"] for r in evalrows]}')
  A=[];B=[]
  for cr in evalrows:
   sim=lp.LocalReservedBootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
   try:r=sim.run_student(models,cr['winner'])
   finally:sim.close()
   A.append(enrich(r,cr,sim))
   sim2=lp.LocalReservedBootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
   try:r2=run_constant(sim2,models,cr['winner'],const)
   finally:sim2.close()
   B.append(enrich(r2,cr,sim2))
  sa=cell_summary(A);sb=cell_summary(B)
  comp=[]
  for x,y in zip(A,B):
   comp.append({'marketId':x['marketId'],'A_pnl':x['pnl'],'B_pnl':y['pnl'],'deltaPnl':y['pnl']-x['pnl'],'A_floor':x['floor'],'B_floor':y['floor'],'deltaFloor':y['floor']-x['floor'],'A_best':x['bestPnl'],'B_best':y['bestPnl'],'deltaBest':y['bestPnl']-x['bestPnl'],'A_fills':x['fills'],'B_fills':y['fills'],'A_submits':x['submits'],'B_submits':y['submits'],'A_absNet':x['absNet'],'B_absNet':y['absNet']})
  out={'version':'DAGGER60_QTY_HEAD_CONSTANT_BASELINE_SMOKE4_V1','bundleSha256':sha256(bp),'smokeIds':SMOKE_IDS,'qtyComparator':qtymeta,'round1Offline':off1,'round2Offline':off2,'A':{'summary':sa,'rows':A},'B':{'summary':sb,'rows':B},'comparison':comp,'exercise':{'marketsChanged':sum(abs(c['deltaPnl'])>1e-9 or c['A_fills']!=c['B_fills'] or abs(c['A_absNet']-c['B_absNet'])>1e-9 for c in comp),'totalQtyAttemptsB':sum(len(r['qtyAttempts']) for r in B),'materialQtyDifferencesB':sum(sum(abs(q['deltaQty'])>1e-6 for q in r['qtyAttempts']) for r in B)},'antiCollapse':{'activeParity':sb['activeMarkets']==sa['activeMarkets'],'fillRetention':sb['fills']/sa['fills'] if sa['fills'] else None}}
  Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'constantQty':const,'A':sa,'B':sb,'comparison':comp,'exercise':out['exercise'],'antiCollapse':out['antiCollapse']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
