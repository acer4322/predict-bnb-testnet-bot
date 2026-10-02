import argparse,json,math,os,sqlite3
from collections import defaultdict

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
 import numpy as np
 from sklearn.ensemble import HistGradientBoostingClassifier
 from sklearn.metrics import roc_auc_score,balanced_accuracy_score,log_loss
 c=sqlite3.connect(a.db); c.row_factory=sqlite3.Row
 mend={(r['asset'],r['market_id']):r['window_end_ms'] for r in c.execute("select asset,market_id,window_end_ms from target_markets where asset in ('BTC','ETH')")}
 rows=list(c.execute("select parent_id,asset,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id"))
 rec=[]; cur=None; up=dn=cost=0.0; mn=tn=0
 for r in rows:
  k=(r['asset'],r['market_id'])
  if k!=cur: cur=k; up=dn=cost=0.0; mn=tn=0
  total=up+dn; paired=min(up,dn); gap=abs(up-dn); paircov=2*paired/total if total else 1.0; absnet=gap/total if total else 0.0; floor_ratio=(paired-cost)/max(cost,1.0); end=mend.get(k); sl=(end-r['first_event_ms'])/1000 if end else None
  if total<=1e-9 or gap<1e-9: rel='BAL'
  elif (r['side']=='UP' and up<dn) or (r['side']=='DOWN' and dn<up): rel='WEAK'
  else: rel='DOM'
  rec.append({'asset':r['asset'],'market':r['market_id'],'end':end,'role':r['role'],'rel':rel,'x':[max(-30,min(330,sl))/300.0 if sl is not None else 0.0,paircov,absnet,max(-5,min(5,floor_ratio)),tn/max(mn+tn,1)]})
  sh=float(r['shares']); px=float(r['average_price']);
  if r['side']=='UP': up+=sh
  else: dn+=sh
  cost+=px*sh
  if r['role']=='MAKER': mn+=1
  else: tn+=1
 common=sorted(set(mend[('BTC',m)] for aa,m in [(k[0],k[1]) for k in mend] if aa=='BTC' and mend.get(('ETH',next((em for ea,em in []),-1))) is not None))
 # use shared window_end directly
 btc_ends=set(v for (aa,_),v in mend.items() if aa=='BTC' and v is not None); eth_ends=set(v for (aa,_),v in mend.items() if aa=='ETH' and v is not None); common=sorted(btc_ends & eth_ends)
 cutoff=common[int(len(common)*0.70)]
 feats=['seconds_left_norm','paircov','absnet','floor_ratio','prior_taker_frac']
 def dataset(asset,task,period):
  z=[r for r in rec if r['asset']==asset and r['end'] is not None and ((r['end']<cutoff) if period=='early' else (r['end']>=cutoff))]
  if task=='weak': z=[r for r in z if r['rel'] in ('WEAK','DOM')]
  X=np.array([r['x'] for r in z],float); y=np.array([(1 if r['role']=='TAKER' else 0) if task=='role' else (1 if r['rel']=='WEAK' else 0) for r in z],int)
  if len(X)>180000:
   idx=np.linspace(0,len(X)-1,180000).astype(int); X=X[idx]; y=y[idx]
  return X,y
 def fit_eval(train_asset,test_asset,task):
  Xtr,ytr=dataset(train_asset,task,'early'); Xte,yte=dataset(test_asset,task,'late')
  m=HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=15,learning_rate=.08,l2_regularization=1.0,random_state=11); m.fit(Xtr,ytr); p=m.predict_proba(Xte)[:,1]; pred=(p>=.5).astype(int)
  return {'trainAsset':train_asset,'testAsset':test_asset,'task':task,'trainN':len(ytr),'testN':len(yte),'trainPositiveRate':float(ytr.mean()),'testPositiveRate':float(yte.mean()),'auc':float(roc_auc_score(yte,p)),'balancedAccuracy':float(balanced_accuracy_score(yte,pred)),'logLoss':float(log_loss(yte,p,labels=[0,1]))}
 out={'version':'TARGET_ETH_BTC_CROSS_ASSET_TRANSFER_V2_CHRONOLOGICAL','sourceDb':os.path.abspath(a.db),'commonWindows':len(common),'cutoffWindowEndMs':cutoff,'features':feats,'tasks':{}}
 for task in ('role','weak'):
  rr={}
  for tr,te in [('BTC','BTC'),('ETH','ETH'),('BTC','ETH'),('ETH','BTC')]: rr[f'{tr}_to_{te}']=fit_eval(tr,te,task)
  rr['retention']={'BTC_to_ETH_vs_ETH_within':rr['BTC_to_ETH']['auc']/rr['ETH_to_ETH']['auc'] if rr['ETH_to_ETH']['auc'] else None,'ETH_to_BTC_vs_BTC_within':rr['ETH_to_BTC']['auc']/rr['BTC_to_BTC']['auc'] if rr['BTC_to_BTC']['auc'] else None}
  out['tasks'][task]=rr
 os.makedirs(os.path.dirname(a.output),exist_ok=True); json.dump(out,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2); print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__': main()
