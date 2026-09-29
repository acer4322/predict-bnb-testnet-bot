from __future__ import annotations
import json,math,sys
from pathlib import Path
import numpy as np, torch
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.predict_bot.execution_tape_archive_v1 import load_archive
from tools.build_hft_orderflow_sequence_dataset_v1 import tape_events,state_prices,sequence,TAPES
from tools.train_hft_orderflow_sequence_value_v1 import OrderFlowEncoder
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
ENC=D/'hft_orderflow_sequence_value_v1_encoder.pt'
SPLITS={'train':('r2_residual_multicheckpoint_train15_v0.json','r2_residual_episode_abort_values_train15_v0.json'),'validation':('r2_residual_multicheckpoint_validation15_v0.json','r2_residual_episode_abort_values_validation15_v0.json'),'forward':('r2_residual_multicheckpoint_forward20_v0.json','r2_residual_episode_abort_values_forward20_v0.json')}
priority={int(r['marketId']):float(r['priorityPnl']) for r in json.loads((D/'r2_residual_opposite_priority_clean_v0.json').read_text())['rows']}
art=torch.load(ENC,map_location='cpu',weights_only=False);enc=OrderFlowEncoder(channels=int(art['channels']),embedding_dim=int(art['embeddingDim']));enc.load_state_dict(art['stateDict']);enc.eval();mean=np.asarray(art['inputMean'],np.float32);std=np.asarray(art['inputStd'],np.float32)
def emb(mid,at,isup):
 tape=load_archive(TAPES/f'{mid}.json.xz');depth,dt,tr,tt=tape_events(tape);pr=state_prices(tape,[at]).get(at)
 if pr is None:return np.zeros(32,np.float32)
 bid,ask=pr; native='BUY' if isup else 'SELL';px=bid if native=='BUY' else ask
 x=sequence(depth,dt,tr,tt,at,native,px,future=False)[None]
 x=(x-mean)/std
 with torch.no_grad():e,_=enc(torch.from_numpy(x.astype(np.float32)))
 return e.numpy()[0]
def load(name):
 ff,af=SPLITS[name];fr=[r for r in json.loads((D/ff).read_text())['rowsData'] if int(r.get('candidateDelayMs') or 0)==0];ab={int(r['marketId']):float(r['abortPnl']) for r in json.loads((D/af).read_text())['rows']};rows=[]
 for r in fr:
  mid=int(r['marketId']);f=r.get('features') or {};a=ab[mid];p=priority[mid];e=emb(mid,int(f['atMs']),bool(float(f.get('recoverySideIsUp') or 0)))
  rows.append({'marketId':mid,'f':f,'e':e,'y':int(a>p+1e-9),'abort':a,'priority':p})
 return rows
R={k:load(k) for k in SPLITS};scalar=sorted(set().union(*(set(r['f']) for rs in R.values() for r in rs)));scalar=[k for k in scalar if k not in {'atMs'}]
def X(rows,mode):
 out=[]
 for r in rows:
  z=[]
  if mode in {'EMBED','BOTH'}:z.extend(r['e'].tolist())
  if mode=='BOTH':
   for k in scalar:
    v=r['f'].get(k);z.append(float(v) if isinstance(v,(int,float)) and v is not None and math.isfinite(float(v)) else np.nan)
  out.append(z)
 return np.asarray(out,float)
def ev(model,rows,mode):
 x=X(rows,mode);y=np.asarray([r['y'] for r in rows]);p=model.predict_proba(x)[:,1];pred=p>=.5;base=sum(r['priority'] for r in rows);pol=sum(r['abort'] if q else r['priority'] for r,q in zip(rows,pred));orc=sum(max(r['abort'],r['priority']) for r in rows)
 return {'n':len(rows),'positive':int(y.sum()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'basePnl':base,'policyPnl':pol,'oraclePnl':orc,'lift':pol-base,'capture':(pol-base)/(orc-base) if abs(orc-base)>1e-9 else None,'predAbort':int(pred.sum())}
def stage(train_names,test):
 tr=sum((R[n] for n in train_names),[]);out={}
 for mode in ['EMBED','BOTH']:
  m=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=1,class_weight='balanced',max_iter=2000,random_state=1));m.fit(X(tr,mode),[r['y'] for r in tr]);out[mode]={'train':ev(m,tr,mode),'test':ev(m,R[test],mode)}
 return out
rep={'version':'R2_RESIDUAL_EPISODE_ABORT_SEQUENCE_V1','researchOnly':True,'encoder':'frozen HFT_ORDERFLOW_SEQUENCE_VALUE_V1_ENCODER; no finetune','sequence':'strict-past 5s, 100ms bins, 10 channels','stage1':stage(['train'],'validation'),'stage2':stage(['train','validation'],'forward'),'guards':['fresh20 untouched','natural 0.5 only','no threshold/hyperparameter sweep']}
(D/'r2_residual_episode_abort_sequence_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
