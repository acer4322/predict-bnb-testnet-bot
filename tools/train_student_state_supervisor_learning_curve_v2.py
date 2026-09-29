from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,f1_score
import train_student_state_supervisor_v0 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
REPORT=OUT/'student_state_supervisor_learning_curve_v2_report.json'
SEED=20260820
SCENARIOS=[
 {'name':'EARLY_ACTIVE_HOLDOUT_25_34','trainSizes':[10,15,20,25],'holdStart':25,'holdEnd':35},
 {'name':'LATE_ACTIVE_HOLDOUT_45_59','trainSizes':[15,25,35,45],'holdStart':45,'holdEnd':60},
]

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
 m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=90,max_leaf_nodes=15,min_samples_leaf=18,l2_regularization=1.0,early_stopping=True,validation_fraction=.15,n_iter_no_change=15,random_state=SEED)
 m.fit(num(tr,fs),y,sample_weight=weights(y));return m
def metric(m,x,fs,label,pos):
 if m is None or len(x)==0:return None
 y=x[label].astype(str);cl=list(map(str,m.classes_))
 if pos not in cl:return None
 p=m.predict_proba(num(x,fs))[:,cl.index(pos)];yy=y.eq(pos).astype(int);pred=pd.Series(m.predict(num(x,fs)),index=x.index).eq(pos).astype(int)
 return {'n':len(x),'positiveRate':float(yy.mean()),'predRate':float(pred.mean()),'auc':float(roc_auc_score(yy,p)) if yy.nunique()>1 else None,'ap':float(average_precision_score(yy,p)) if yy.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pred)),'f1':float(f1_score(yy,pred,zero_division=0))}
def trend(vals):
 z=[v for v in vals if v is not None]
 return {'first':z[0] if z else None,'last':z[-1] if z else None,'delta':z[-1]-z[0] if len(z)>=2 else None,'improvingSteps':sum(z[i]>z[i-1] for i in range(1,len(z))) if len(z)>=2 else None,'steps':len(z)-1 if len(z)>=1 else 0}
def run_scenario(d,ids,features_all,sc):
 hold_ids=set(ids[sc['holdStart']:sc['holdEnd']]);hold=d[d.market_id.astype(int).isin(hold_ids)].copy();small_ids=set(ids[:sc['trainSizes'][0]]);features=usable(d[d.market_id.astype(int).isin(small_ids)],features_all);curve=[]
 for n in sc['trainSizes']:
  tr_ids=set(ids[:n]);tr=d[d.market_id.astype(int).isin(tr_ids)].copy();acttr=tr[tr.gate_act.eq('ACT')];makertr=tr[tr.teacher_mode.eq('MAKER')];takertr=tr[tr.teacher_mode.eq('TAKER')]
  m1=fit(tr,features,'gate_act');m2=fit(acttr,features,'gate_channel');m3=fit(makertr,features,'student_maker_gate_label');m4=fit(takertr,features,'student_taker_gate_label')
  acth=hold[hold.gate_act.eq('ACT')];mh=hold[hold.teacher_mode.eq('MAKER')];th=hold[hold.teacher_mode.eq('TAKER')]
  row={'trainMarkets':n,'trainRows':len(tr),'trainCounts':tr.teacher_mode.value_counts().to_dict(),'act':metric(m1,hold,features,'gate_act','ACT'),'channelTaker':metric(m2,acth,features,'gate_channel','TAKER'),'makerRepair':metric(m3,mh,features,'student_maker_gate_label','PASSIVE_REPAIR'),'takerRepair':metric(m4,th,features,'student_taker_gate_label','ACTIVE_REPAIR')};curve.append(row)
  print(json.dumps({'scenario':sc['name'],'trainMarkets':n,'actAuc':row['act']['auc'] if row['act'] else None,'channelTakerAuc':row['channelTaker']['auc'] if row['channelTaker'] else None,'makerRepairAuc':row['makerRepair']['auc'] if row['makerRepair'] else None,'takerRepairAuc':row['takerRepair']['auc'] if row['takerRepair'] else None}),flush=True)
 trends={k:trend([(r.get(k) or {}).get('auc') for r in curve]) for k in ('act','channelTaker','makerRepair','takerRepair')}
 return {'name':sc['name'],'holdoutMarketIndices':[sc['holdStart'],sc['holdEnd']-1],'holdoutIds':sorted(hold_ids),'holdoutRows':len(hold),'holdoutTeacherCounts':hold.teacher_mode.value_counts().to_dict(),'featuresFrozenFromSmallestTrain':len(features),'curve':curve,'aucTrends':trends}
def main():
 d,mem,files=base.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);d['gate_act']=np.where(d.teacher_mode.eq('HOLD'),'HOLD','ACT');d['gate_channel']=np.where(d.teacher_mode.eq('TAKER'),'TAKER','MAKER');markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=markets.market_id.astype(int).tolist();features_all=base.CURRENT+mem
 results=[run_scenario(d,ids,features_all,sc) for sc in SCENARIOS]
 rep={'reportVersion':'STUDENT_STATE_SUPERVISOR_LEARNING_CURVE_V2','researchOnly':True,'question':'Across two chronological active-market holdouts, does more consistent-R2 ordinary experience improve hierarchical Supervisor discrimination?','source':{'markets':len(ids),'rows':len(d),'files':[Path(x).name for x in files],'final75_99Used':False,'special20260816Used':False},'scenarios':results,'largeTrainingGate':'Proceed only if multiple gates show positive scaling on both holdouts or if a clearly identified gate shows robust monotonic gains. Mixed/negative curves mean option semantics/modeling need work before large training.','guards':['No winner/PnL.','Same HGB capacity within scenario.','Teacher future action is label only.','No runtime/Echtgeld changes.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
