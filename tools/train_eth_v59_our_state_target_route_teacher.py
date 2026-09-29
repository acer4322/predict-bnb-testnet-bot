from __future__ import annotations
import argparse,glob,importlib.util,json,math,sqlite3
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss,log_loss
HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('cmp',HERE/'compare_eth_v53_our_target_same_market.py');cmp=importlib.util.module_from_spec(sp);sp.loader.exec_module(cmp)

def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))>1
 return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,'meanPositiveScore':float(p[y==1].mean()) if y.sum()>0 else None,'meanNegativeScore':float(p[y==0].mean()) if (y==0).sum()>0 else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model',required=True);a=ap.parse_args()
 src=sorted(glob.glob(a.pattern));states=[];all_mids=[]
 for p in src:
  d=json.load(open(p,encoding='utf-8'))
  for rr in d.get('rows',[]):
   mid=int(rr['marketId']);all_mids.append(mid)
   for z in rr['functional'].get('v59Rows',[]):states.append({'marketId':mid,**z})
 if not states:raise SystemExit('no V59 states')
 mids=sorted(set(all_mids));assert len(mids)==100, f'expected 100 cohort markets, got {len(mids)}'
 tr=set(mids[:60]);va=set(mids[60:80]);te=set(mids[80:100]);assert len(tr)==60 and len(va)==20 and len(te)==20
 con=sqlite3.connect(a.target_db);ph=','.join('?'*len(mids));rr=con.execute(f"select market_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",mids).fetchall();con.close();by={m:[] for m in mids}
 for r in rr:by[int(r[0])].append(r)
 active={m:sorted(int(e['t']) for e in cmp.target_events(by[m]) if e['role']=='ACTIVE_REPAIR') for m in mids}
 for z in states:
  t=int(z['t']);z['label3']=int(any(t<x<=t+3000 for x in active[z['marketId']]))
 features=sorted([k for k in states[0] if k.startswith('p_') or k.startswith('m_')])
 # m_raw_hazard is included as one weak/transfer diagnostic feature, not authority.
 def mat(rows):
  X=[];y=[]
  for z in rows:
   rr=[]
   for k in features:
    v=z.get(k);rr.append(np.nan if v is None else float(v))
   X.append(rr);y.append(int(z['label3']))
  return np.asarray(X,np.float32),np.asarray(y,int)
 parts={'train':[z for z in states if z['marketId'] in tr],'validation':[z for z in states if z['marketId'] in va],'test':[z for z in states if z['marketId'] in te]}
 Xtr,ytr=mat(parts['train']);Xv,yv=mat(parts['validation']);Xt,yt=mat(parts['test'])
 model=HistGradientBoostingClassifier(max_iter=300,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=20,l2_regularization=3.0,class_weight='balanced',random_state=5903).fit(Xtr,ytr)
 out={'version':'TARGET_ETH_V59_OUR_STATE_TARGET_ROUTE_TEACHER','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'coverage':{'states':len(states),'cohortMarkets':len(mids),'marketsWithStates':len(set(x['marketId'] for x in states))},'split':{'trainMarkets':sorted(tr),'validationMarkets':sorted(va),'testMarkets':sorted(te)},'features':features,'label':'Target actual ACTIVE_REPAIR begins within next 3s in same market','metrics':{}}
 for name,X,y in [('train',Xtr,ytr),('validation',Xv,yv),('test',Xt,yt)]:
  p=model.predict_proba(X)[:,1]; raw=np.asarray([float(z.get('m_raw_hazard') or 0.0) for z in parts[name]],float);out['metrics'][name]={'student':metrics(y,p),'rawFrozenHazard':metrics(y,raw),'aucDeltaStudentMinusRaw':(metrics(y,p)['auc']-metrics(y,raw)['auc']) if metrics(y,p)['auc'] is not None and metrics(y,raw)['auc'] is not None else None}
 Path(a.model).parent.mkdir(parents=True,exist_ok=True);joblib.dump({'version':out['version'],'features':features,'model':model,'labelHorizonSec':3,'split':out['split']},a.model);out['model']=a.model;out['boundary']=['OUR strict-past state/public-book features only.','Target future Active-Repair timing is label only.','Chronological 60/20/20 market split.','One fixed model; no threshold/feature/hyperparameter sweep.','Consumed development cohort only; no runtime authority/no 8781.'];Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
