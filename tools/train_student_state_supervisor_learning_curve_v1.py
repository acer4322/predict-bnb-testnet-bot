from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,f1_score
import train_student_state_supervisor_v0 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
REPORT=OUT/'student_state_supervisor_learning_curve_v1_report.json'
SEED=20260820
SIZES=[15,30,45,60]
HOLDOUT=15

def num(d,fs):return d[fs].apply(pd.to_numeric,errors='coerce')
def weights(y):
 c=y.value_counts();n=len(y);mp={k:np.sqrt(n/max(1,int(v))) for k,v in c.items()};w=y.map(mp).astype(float).to_numpy();return w/w.mean()
def usable(d,fs):
 out=[]
 for f in fs:
  z=pd.to_numeric(d[f],errors='coerce').dropna()
  if len(z)>=2 and z.nunique()>=2:out.append(f)
 return out
def fit(tr,fs,label):
 y=tr[label].astype(str)
 if y.nunique()<2:return None
 m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=90,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=1.0,early_stopping=True,validation_fraction=.15,n_iter_no_change=15,random_state=SEED)
 m.fit(num(tr,fs),y,sample_weight=weights(y));return m
def metric(m,x,fs,label,pos):
 if m is None or len(x)==0:return None
 y=x[label].astype(str);cl=list(map(str,m.classes_))
 if pos not in cl:return None
 pi=cl.index(pos);p=m.predict_proba(num(x,fs))[:,pi];pred=pd.Series(m.predict(num(x,fs)),index=x.index).eq(pos).astype(int);yy=y.eq(pos).astype(int)
 return {'n':int(len(x)),'positiveRate':float(yy.mean()),'predRate':float(pred.mean()),'auc':float(roc_auc_score(yy,p)) if yy.nunique()>1 else None,'ap':float(average_precision_score(yy,p)) if yy.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pred)),'f1':float(f1_score(yy,pred,zero_division=0))}
def extract(curve,key):
 z=[]
 for r in curve:
  m=r.get(key) or {}; v=m.get('auc'); z.append(v)
 return z
def trend(xs):
 ys=[x for x in xs if x is not None]
 if len(ys)<2:return {'first':ys[0] if ys else None,'last':ys[-1] if ys else None,'delta':None,'improvingSteps':None}
 return {'first':ys[0],'last':ys[-1],'delta':ys[-1]-ys[0],'improvingSteps':sum(ys[i]>ys[i-1] for i in range(1,len(ys))),'steps':len(ys)-1}
def main():
 d,mem,files=base.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
 d['gate_act']=np.where(d.teacher_mode.eq('HOLD'),'HOLD','ACT');d['gate_channel']=np.where(d.teacher_mode.eq('TAKER'),'TAKER','MAKER')
 markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=markets.market_id.astype(int).tolist()
 if len(ids)<75:raise RuntimeError(f'need >=75 consistent R2 markets, got {len(ids)}')
 hold_ids=set(ids[-HOLDOUT:]);hold=d[d.market_id.astype(int).isin(hold_ids)].copy();pool=ids[:-HOLDOUT]
 # Fixed feature set chosen only from earliest 15-market training subset, then frozen for the whole learning curve.
 first=d[d.market_id.astype(int).isin(set(pool[:SIZES[0]]))].copy();features=usable(first,base.CURRENT+mem)
 curve=[]
 for n in SIZES:
  train_ids=set(pool[:n]);tr=d[d.market_id.astype(int).isin(train_ids)].copy();acttr=tr[tr.gate_act.eq('ACT')];makertr=tr[tr.teacher_mode.eq('MAKER')];takertr=tr[tr.teacher_mode.eq('TAKER')]
  m_act=fit(tr,features,'gate_act');m_channel=fit(acttr,features,'gate_channel');m_maker=fit(makertr,features,'student_maker_gate_label');m_taker=fit(takertr,features,'student_taker_gate_label')
  acthold=hold[hold.gate_act.eq('ACT')];makerhold=hold[hold.teacher_mode.eq('MAKER')];takerhold=hold[hold.teacher_mode.eq('TAKER')]
  row={'trainMarkets':n,'trainRows':len(tr),'trainTeacherCounts':tr.teacher_mode.value_counts().to_dict(),'act':metric(m_act,hold,features,'gate_act','ACT'),'channelTaker':metric(m_channel,acthold,features,'gate_channel','TAKER'),'makerRepair':metric(m_maker,makerhold,features,'student_maker_gate_label','PASSIVE_REPAIR'),'takerRepair':metric(m_taker,takerhold,features,'student_taker_gate_label','ACTIVE_REPAIR')}
  curve.append(row);print(json.dumps({'progress':n,'actAuc':row['act']['auc'] if row['act'] else None,'channelTakerAuc':row['channelTaker']['auc'] if row['channelTaker'] else None,'makerRepairAuc':row['makerRepair']['auc'] if row['makerRepair'] else None,'takerRepairAuc':row['takerRepair']['auc'] if row['takerRepair'] else None}),flush=True)
 trends={k:trend(extract(curve,k)) for k in ('act','channelTaker','makerRepair','takerRepair')}
 rep={'reportVersion':'STUDENT_STATE_SUPERVISOR_LEARNING_CURVE_V1','researchOnly':True,'question':'Does additional consistent-R2 ordinary-market experience improve frozen hierarchical Supervisor generalization on the same 15-market chronological holdout?','source':{'markets':len(ids),'rows':len(d),'files':[Path(x).name for x in files],'ordinaryOnly':True,'final75_99Used':False},'protocol':{'trainSizesMarkets':SIZES,'holdoutMarkets':HOLDOUT,'holdoutIds':sorted(hold_ids),'fixedFeaturesFromFirst15':len(features),'memorySemantics':'~6/20/60s strict-past deltas','sameHyperparametersAllSizes':True,'noPnLSelection':True},'holdoutTeacherCounts':hold.teacher_mode.value_counts().to_dict(),'curve':curve,'aucTrends':trends,'decisionRule':'Large-training gate: require meaningful positive 15->60 AUC delta on multiple high-level gates, especially ACT/channel or repair, without collapse on the fixed chronological holdout. If curve is flat/mixed, improve option semantics before scaling data.','guards':['No winner/PnL labels or features.','No special 2026-08-16.','No final75-99.','All OUR trajectories use same frozen R2 raw policy.','No runtime/Echtgeld changes.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
