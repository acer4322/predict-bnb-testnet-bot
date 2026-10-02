from __future__ import annotations
import argparse,json,math,sqlite3
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
SEED=20260831
TASKS=('repair','switch','reentry')
GROUPS={
 'BASE':['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_pnl_ratio','prior_taker_frac','avg_cost_gap','gross_log'],
 'OBJECTIVE_IDENTITY':['prev_maker_rel','objective_streak_norm','objective_age_log','transition_count_norm'],
 'PROGRESS_LEDGER':['cum_repair_absnet_progress','cum_repair_pair_gain','cum_repair_floor_gain','segment_absnet_progress','segment_pair_gain','segment_floor_gain']
}
VARIANTS={
 'BASE':GROUPS['BASE'],
 'BASE_PLUS_IDENTITY':GROUPS['BASE']+GROUPS['OBJECTIVE_IDENTITY'],
 'BASE_PLUS_PROGRESS':GROUPS['BASE']+GROUPS['PROGRESS_LEDGER'],
 'FULL':GROUPS['BASE']+GROUPS['OBJECTIVE_IDENTITY']+GROUPS['PROGRESS_LEDGER']
}

def rel_for(side,up,dn):
 if up+dn<=1e-9 or abs(up-dn)<=1e-9:return 0
 return 1 if ((side=='UP' and up<dn) or (side=='DOWN' and dn<up)) else -1

def build(db):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 mend={(str(r['asset']),int(r['market_id'])):int(r['window_end_ms']) for r in c.execute("select asset,market_id,window_end_ms from target_markets where asset in ('BTC','ETH') and window_end_ms is not null")}
 common=sorted({v for (a,_),v in mend.items() if a=='BTC'} & {v for (a,_),v in mend.items() if a=='ETH'})
 cut=common[int(len(common)*.70)]
 rows=c.execute("select parent_id,asset,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id")
 out=[];cur=None
 for r in rows:
  k=(str(r['asset']),int(r['market_id']))
  if k!=cur:
   cur=k;up=dn=cost=upcost=dncost=0.;maker_n=taker_n=0;prev_rel=0;prev_time=None;streak=0;transitions=0
   cra=crp=crf=0.; seg_start_abs=seg_start_pc=seg_start_fr=None
  role=str(r['role']);side=str(r['side']);t=int(r['first_event_ms']);sh=float(r['shares']);px=float(r['average_price']);end=mend.get(k)
  gross=up+dn;pair=min(up,dn);gap=abs(up-dn);pc=2*pair/gross if gross>1e-9 else 1.;ab=gap/gross if gross>1e-9 else 0.;fr=(pair-cost)/max(cost,1.);br=(max(up,dn)-cost)/max(cost,1.);avu=upcost/up if up>1e-9 else 0.;avd=dncost/dn if dn>1e-9 else 0.;age=(t-prev_time) if prev_time is not None else 1e6
  rel=rel_for(side,up,dn) if role=='MAKER' else 0
  if role=='MAKER' and rel!=0:
   current_streak=streak if prev_rel!=0 else 0
   if seg_start_abs is not None and prev_rel!=0:
    seg_abs=(seg_start_abs-ab) if prev_rel==1 else (ab-seg_start_abs); seg_pc=pc-seg_start_pc; seg_fr=fr-seg_start_fr
   else:
    seg_abs=seg_pc=seg_fr=0.
   vals={
    'seconds_left_norm':max(-30,min(330,(end-t)/1000))/300 if end else 0.,'pair_coverage':pc,'absnet_ratio':ab,'floor_ratio':max(-5,min(5,fr)),'best_pnl_ratio':max(-5,min(5,br)),'prior_taker_frac':taker_n/max(maker_n+taker_n,1),'avg_cost_gap':max(-1,min(1,avu-avd)),'gross_log':math.log1p(gross)/math.log1p(500),
    'prev_maker_rel':float(prev_rel),'objective_streak_norm':min(current_streak,12)/12.,'objective_age_log':math.log1p(min(age,120000))/math.log1p(120000),'transition_count_norm':min(transitions,12)/12.,
    'cum_repair_absnet_progress':min(cra,3.),'cum_repair_pair_gain':max(-3,min(3,crp)),'cum_repair_floor_gain':max(-3,min(3,crf)),'segment_absnet_progress':max(-3,min(3,seg_abs)),'segment_pair_gain':max(-3,min(3,seg_pc)),'segment_floor_gain':max(-3,min(3,seg_fr))
   }
   out.append({'asset':k[0],'market':k[1],'end':end,'vals':vals,'repair':int(rel==1),'switch':None if prev_rel==0 else int(rel!=prev_rel),'reentry':None if prev_rel!=1 else int(rel==-1)})
  pre_ab,pre_pc,pre_fr=ab,pc,fr
  if side=='UP':up+=sh;upcost+=sh*px
  else:dn+=sh;dncost+=sh*px
  cost+=sh*px
  gross2=up+dn;pair2=min(up,dn);gap2=abs(up-dn);pc2=2*pair2/gross2 if gross2>1e-9 else 1.;ab2=gap2/gross2 if gross2>1e-9 else 0.;fr2=(pair2-cost)/max(cost,1.)
  if role=='MAKER' and rel!=0:
   if rel==1:
    cra+=max(0.,pre_ab-ab2);crp+=pc2-pre_pc;crf+=fr2-pre_fr
   if prev_rel==0 or rel!=prev_rel:
    if prev_rel!=0:transitions+=1
    seg_start_abs=pre_ab;seg_start_pc=pre_pc;seg_start_fr=pre_fr;streak=1
   else:streak+=1
   prev_rel=rel;prev_time=t;maker_n+=1
  elif role=='TAKER':taker_n+=1
 c.close();return out,cut,len(common)

def metric(y,p):
 y=np.asarray(y,int);pred=(p>=.5).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def fit_eval(rows,asset,cut,features,task):
 tr=[r for r in rows if r['asset']==asset and r['end']<cut and r[task] is not None];te=[r for r in rows if r['asset']==asset and r['end']>=cut and r[task] is not None]
 def X(z):return np.asarray([[r['vals'][f] for f in features] for r in z],np.float32)
 Xtr,Xte=X(tr),X(te);ytr=np.asarray([r[task] for r in tr],int);yte=np.asarray([r[task] for r in te],int)
 if len(Xtr)>70000:
  ii=np.linspace(0,len(Xtr)-1,70000).astype(int);Xtr=Xtr[ii];ytr=ytr[ii]
 m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.045,max_leaf_nodes=27,min_samples_leaf=40,l2_regularization=5.,class_weight='balanced',random_state=SEED)
 m.fit(Xtr,ytr);return metric(yte,m.predict_proba(Xte)[:,1])

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows,cut,nwin=build(a.db)
 res={}
 for asset in ('BTC','ETH'):
  res[asset]={}
  for v,fs in VARIANTS.items():res[asset][v]={t:fit_eval(rows,asset,cut,fs,t) for t in TASKS}
 deltas={}
 for asset in ('BTC','ETH'):
  deltas[asset]={}
  for v in ('BASE_PLUS_IDENTITY','BASE_PLUS_PROGRESS','FULL'):
   deltas[asset][v]={t:res[asset][v][t]['auc']-res[asset]['BASE'][t]['auc'] for t in TASKS}
 portable={}
 for comp,var in [('OBJECTIVE_IDENTITY','BASE_PLUS_IDENTITY'),('PROGRESS_LEDGER','BASE_PLUS_PROGRESS'),('FULL','FULL')]:
  bd=deltas['BTC'][var];ed=deltas['ETH'][var];allvals=list(bd.values())+list(ed.values())
  portable[comp]={'btcDelta':bd,'ethDelta':ed,'consistentPositiveTasks':[t for t in TASKS if bd[t]>0 and ed[t]>0],'portableEvidence':bool(sum(1 for t in TASKS if bd[t]>=.005 and ed[t]>=.005)>=2 and min(allvals)>=-.01)}
 out={'version':'ETH_BTC_PASSIVE_REPAIR_ARCHITECTURE_DISTILL_V1B_ANTI_TAUTOLOGY','researchOnly':True,'chronologyCutoff':cut,'commonWindows':nwin,'rows':len(rows),'variants':res,'deltasVsBase':deltas,'portableArchitecture':portable,'boundary':['BTC and ETH trained/evaluated independently','No shared encoder, pooled labels, or cross-asset gradients','Strict-past actual-filled Target parent chronology only','No winner/future PnL','No TARGET_UNIT=18 or expected_parent_shares','All features are strict-past relative to current Maker label','Portable evidence nominates architecture components only; thresholds/actions remain asset-specific']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'portableArchitecture':portable},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
