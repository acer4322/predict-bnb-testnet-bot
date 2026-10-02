from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss
FOLDS=[(0,40,40,60),(0,60,60,80),(0,80,80,100)]
def maxdd(x):
 s=0.;peak=0.;dd=0.
 for v in x:s+=float(v);peak=max(peak,s);dd=max(dd,peak-s)
 return float(dd)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--action-corpus',required=True);ap.add_argument('--belief',required=True);ap.add_argument('--closed-loop',nargs='+',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 ac=json.loads(Path(a.action_corpus).read_text(encoding='utf-8')); ar={int(r['marketId']):r for r in ac['rows']}
 bd=json.loads(Path(a.belief).read_text(encoding='utf-8')); br={int(r['marketId']):r for r in bd['rows']}
 cr=[]
 for p in a.closed_loop:cr += json.loads(Path(p).read_text(encoding='utf-8'))['rows']
 cr=sorted(cr,key=lambda r:int(r['marketId'])); rows=[]
 for r in cr:
  m=int(r['marketId']);
  if m not in ar or m not in br:continue
  rp=r['policies']['ALWAYS_REPAIR']['metrics']; ep=r['policies']['ALWAYS_REEXPAND']['metrics']; npol=r['policies']['NATIVE']['metrics']
  f={('A_'+k):float(v) for k,v in ar[m]['features'].items() if v is not None and isinstance(v,(int,float))}
  f.update({('B_'+k):float(v) for k,v in br[m]['features'].items() if v is not None and isinstance(v,(int,float))})
  rows.append({'marketId':m,'t':int(ar[m]['t']),'features':f,'repairPnl':float(rp['pnl']),'reexpandPnl':float(ep['pnl']),'nativePnl':float(npol['pnl']),'repairFills':float(rp['fills']),'reexpandFills':float(ep['fills']),'nativeFills':float(npol['fills']),'delta':float(ep['pnl'])-float(rp['pnl'])})
 rows=sorted(rows,key=lambda r:(r['t'],r['marketId'])); names=sorted({k for r in rows for k in r['features']})
 sets={'ACTION':[k for k in names if k.startswith('A_')],'BELIEF':[k for k in names if k.startswith('B_')],'COMBINED':names}
 def X(rr,nn):return np.array([[float(r['features'].get(k,np.nan)) for k in nn] for r in rr],float)
 def y(rr):return np.array([1 if r['delta']>0 else 0 for r in rr],int)
 models={}
 for fs in sets:
  models[fs]={
   'LOGIT':lambda:make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=1,max_iter=2000,random_state=20260907)),
   'EXTRATREES':lambda:make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=300,min_samples_leaf=4,max_features='sqrt',random_state=20260907,n_jobs=1,class_weight='balanced'))}
 preds={(fs,mo):[] for fs in sets for mo in models[fs]}; yy=[]; meta=[]; foldout=[]
 for fi,(a0,a1,b0,b1) in enumerate(FOLDS,1):
  tr,te=rows[a0:a1],rows[b0:b1]; yt=y(tr);yv=y(te);fo={'fold':fi,'train':[a0,a1],'test':[b0,b1],'trainReexpandBetter':int(yt.sum()),'testReexpandBetter':int(yv.sum()),'models':{}}
  for fs,nn in sets.items():
   for mo,fac in models[fs].items():
    m=fac();m.fit(X(tr,nn),yt);p=m.predict_proba(X(te,nn))[:,1];preds[(fs,mo)].extend(p.tolist());fo['models'][fs+'_'+mo]={'auc':float(roc_auc_score(yv,p)) if len(set(yv.tolist()))>1 else None,'logloss':float(log_loss(yv,p,labels=[0,1])),'brier':float(brier_score_loss(yv,p))}
  yy.extend(yv.tolist());meta.extend(te);foldout.append(fo)
 yall=np.array(yy,int);summary={}
 for key,pv in preds.items():
  p=np.array(pv,float);chosen=[];fills=[];base=[];basefills=[];deltas=[]
  for r,pp in zip(meta,p):
   cp=r['reexpandPnl'] if pp>=.5 else r['repairPnl'];cf=r['reexpandFills'] if pp>=.5 else r['repairFills'];chosen.append(cp);fills.append(cf);base.append(r['nativePnl']);basefills.append(r['nativeFills']);deltas.append(cp-r['nativePnl'])
  chosen=np.array(chosen);base=np.array(base);fills=np.array(fills);basefills=np.array(basefills);deltas=np.array(deltas);aff=np.abs(deltas)>1e-9; k=key[0]+'_'+key[1]
  summary[k]={'auc':float(roc_auc_score(yall,p)),'logloss':float(log_loss(yall,p,labels=[0,1])),'brier':float(brier_score_loss(yall,p)),'modeAccuracy':float(np.mean((p>=.5)==yall)),'affectedMarkets':int(aff.sum()),'improvedVsNative':int((deltas>1e-9).sum()),'worsenedVsNative':int((deltas<-1e-9).sum()),'affectedImprovementRate':float(((deltas>1e-9)&aff).sum()/aff.sum()) if aff.sum() else None,'baselineTotalPnl':float(base.sum()),'candidateTotalPnl':float(chosen.sum()),'deltaTotalPnl':float(deltas.sum()),'baselineWinRate':float(np.mean(base>0)),'candidateWinRate':float(np.mean(chosen>0)),'baselineWorst':float(base.min()),'candidateWorst':float(chosen.min()),'baselineWorst20Mean':float(np.mean(np.sort(base)[:12])),'candidateWorst20Mean':float(np.mean(np.sort(chosen)[:12])),'baselineMaxDD':maxdd(base),'candidateMaxDD':maxdd(chosen),'baselineFillsPerMarket':float(basefills.mean()),'candidateFillsPerMarket':float(fills.mean())}
 z=summary.get('COMBINED_EXTRATREES');gate={'affectedImprovementRate70':bool(z['affectedImprovementRate']>=.70),'totalPnlImproves':bool(z['candidateTotalPnl']>z['baselineTotalPnl']),'winRateNonWorse':bool(z['candidateWinRate']>=z['baselineWinRate']),'worstMarketWithin10Pct':bool(z['candidateWorst']>=z['baselineWorst']-max(1.0,abs(z['baselineWorst'])*.10)),'worst20MeanWithin10Pct':bool(z['candidateWorst20Mean']>=z['baselineWorst20Mean']-max(.5,abs(z['baselineWorst20Mean'])*.10)),'maxDDWithin110Pct':bool(z['candidateMaxDD']<=z['baselineMaxDD']*1.10),'fillActivityAtLeast80Pct':bool(z['candidateFillsPerMarket']>=z['baselineFillsPerMarket']*.80)};gate['allPass']=all(gate.values())
 out={'version':'MANAGEMENT_MAINLINE_V3B_CLOSED_LOOP_MODE_ROUTER_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':len(rows),'forwardRows':len(meta),'featureSets':{k:len(v) for k,v in sets.items()},'folds':foldout,'summary':summary,'combinedExtraTrees70_30Gate':gate,'boundary':['label is diagnostic whole-market ALWAYS_REEXPAND vs ALWAYS_REPAIR outcome','first eligible strict-past state only','expanding chronological 40/60/80 train, next20 test','no threshold/model sweep','winner only already embedded in post-hoc closed-loop PnL labels','diagnostic router only, not runtime/promotion authority']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary,'gate':gate},ensure_ascii=False))
if __name__=='__main__':main()
