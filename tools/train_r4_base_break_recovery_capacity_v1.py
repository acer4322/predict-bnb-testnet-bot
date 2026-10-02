from __future__ import annotations
import json, importlib.util, sys, math
from collections import deque, Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_marginal_pair_quality_replication_v2.py'
spec=importlib.util.spec_from_file_location('r4_mpq_rep_v2_bbr',P); m=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=m; spec.loader.exec_module(m)
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_BASE_BREAK_RECOVERY_CAPACITY_V1'
N_MARKETS=4500
FEATURES=[
 'event_index_norm','pre_floor_per_gross','pre_edge','pre_coverage','pre_absnet_ratio','pre_cost_per_gross','pre_upside_per_gross',
 'reserve_spend_ratio','reserve_spend_per_gross','post_floor_per_gross','post_edge','post_absnet_ratio','post_coverage',
 'candidate_price','candidate_shares_per_gross','candidate_role_taker','relation_surplus_side','relation_weak_side_cross',
 'events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_shares_15s_per_gross','opp_side_shares_15s_per_gross',
 'floor_change_15s_per_gross','edge_change_15s'
]

def apply(state,z):
 up,down,cu,cd=state; sh=float(z['sh']); px=float(z['px'])
 if z['side']=='UP': up+=sh; cu+=sh*px
 else: down+=sh; cd+=sh*px
 return up,down,cu,cd

def geom(s): return m.geom(*s)
def div(a,b): return float(a/b) if abs(b)>1e-12 else 0.0

def build():
 meta,ev=m.load(N_MARKETS); rows=[]
 for mid,wend,w in meta:
  events=ev.get(mid,[]); state=(0.,0.,0.,0.); recent=deque()
  for i,z in enumerate(events):
   t=int(z['t'])
   while recent and t-int(recent[0]['t'])>15000: recent.popleft()
   pre=geom(state); post_state=apply(state,z); post=geom(post_state)
   if pre['floor']>0 and post['floor']<=0:
    gross=max(pre['gross'],1e-9); surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
    relation='SURPLUS_SIDE' if z['side']==surplus else 'WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT'
    q15=list(recent); q5=[a for a in q15 if t-int(a['t'])<=5000]
    old=geom(q15[0]['preState']) if q15 else pre
    same=sum(float(a['sh']) for a in q15 if surplus!='FLAT' and a['side']==surplus)
    opp=sum(float(a['sh']) for a in q15 if surplus!='FLAT' and a['side']!=surplus)
    reserve=max(0.,pre['floor']-post['floor'])
    pre_upside=max(pre['up']-pre['cost'],pre['down']-pre['cost'])
    x={
      'event_index_norm':i/max(1,len(events)-1),'pre_floor_per_gross':div(pre['floor'],gross),'pre_edge':pre['edge'],
      'pre_coverage':pre['coverage'],'pre_absnet_ratio':div(pre['absnet'],gross),'pre_cost_per_gross':div(pre['cost'],gross),
      'pre_upside_per_gross':div(pre_upside,gross),'reserve_spend_ratio':div(reserve,max(pre['floor'],1e-9)),
      'reserve_spend_per_gross':div(reserve,gross),'post_floor_per_gross':div(post['floor'],max(post['gross'],1e-9)),'post_edge':post['edge'],
      'post_absnet_ratio':div(post['absnet'],max(post['gross'],1e-9)),'post_coverage':post['coverage'],
      'candidate_price':float(z['px']),'candidate_shares_per_gross':div(float(z['sh']),gross),'candidate_role_taker':float(z['role']=='TAKER'),
      'relation_surplus_side':float(relation=='SURPLUS_SIDE'),'relation_weak_side_cross':float(relation=='WEAK_SIDE_CROSS'),
      'events_5s':float(len(q5)),'events_15s':float(len(q15)),'maker_events_15s':float(sum(a['role']=='MAKER' for a in q15)),
      'taker_events_15s':float(sum(a['role']=='TAKER' for a in q15)),'same_side_shares_15s_per_gross':div(same,gross),
      'opp_side_shares_15s_per_gross':div(opp,gross),'floor_change_15s_per_gross':div(pre['floor']-old['floor'],gross),'edge_change_15s':pre['edge']-old['edge']
    }
    s=post_state; recover=False
    for zz in events[i+1:]:
      if int(zz['t'])-t>60000: break
      s=apply(s,zz)
      if geom(s)['floor']>0: recover=True; break
    rows.append({'marketId':mid,'windowEndMs':wend,'x':[float(x[f]) for f in FEATURES],'y':int(recover),'mpq':int(post['edge']<0),'role':z['role'],'relation':relation})
   recent.append({'t':t,'role':z['role'],'side':z['side'],'sh':float(z['sh']),'preState':state}); state=post_state
 return meta,rows

def metrics(y,p):
 y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=float)
 return {'rows':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(set(y))>1 else None,'balancedAccuracyAt05':float(balanced_accuracy_score(y,p>=.5)) if len(set(y))>1 else None}

def main():
 meta,rows=build(); markets=sorted((wend,mid) for mid,wend,_ in meta); cut=int(.60*len(markets)); train_ids=set(mid for _,mid in markets[:cut]); test_ids=set(mid for _,mid in markets[cut:])
 tr=[r for r in rows if r['marketId'] in train_ids]; te=[r for r in rows if r['marketId'] in test_ids]
 X=np.asarray([r['x'] for r in tr],dtype=float); y=np.asarray([r['y'] for r in tr],dtype=int)
 model=HistGradientBoostingClassifier(max_iter=160,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=25,l2_regularization=3.,random_state=20260826).fit(X,y)
 ptr=model.predict_proba(X)[:,1]; Xte=np.asarray([r['x'] for r in te],dtype=float); yte=np.asarray([r['y'] for r in te],dtype=int); pte=model.predict_proba(Xte)[:,1]
 mpq=[(r,p) for r,p in zip(te,pte) if r['mpq']==1];
 mpqy=[r['y'] for r,p in mpq]; mpqp=[p for r,p in mpq]
 byrel={}
 for rel in ['SURPLUS_SIDE','WEAK_SIDE_CROSS','FLAT']:
  a=[(r,p) for r,p in zip(te,pte) if r['relation']==rel]; byrel[rel]=metrics([r['y'] for r,p in a],[p for r,p in a]) if a else {'rows':0}
 rep={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'cohort':{'ordinaryMarkets':len(meta),'baseBreakRows':len(rows),'trainMarkets':len(train_ids),'testMarkets':len(test_ids),'trainRows':len(tr),'testRows':len(te),'mpqRowsTotal':sum(r['mpq'] for r in rows),'mpqRowsTest':len(mpq),'sealed20260816':True},'features':FEATURES,'label':'post-fill floor<=0 after pre-floor>0; y=Target floor returns >0 within next 60s','train':metrics(y,ptr),'test':metrics(yte,pte),'mpqTransferTest':metrics(mpqy,mpqp) if mpq else {'rows':0},'byRelationTest':byrel,'gate':{'overallTestAucRequired':0.70,'mpqTransferAucRequired':0.65,'mpqMinimumRows':15},'guards':{'strictPastRuntimeFeatures':True,'noWinnerLabel':True,'noEchtgeldTraining':True,'noThresholdSweep':True,'mpqRuleFrozen':True}}
 rep['gate']['pass']=bool(rep['test']['auc'] is not None and rep['test']['auc']>=.70 and len(mpq)>=15 and rep['mpqTransferTest']['auc'] is not None and rep['mpqTransferTest']['auc']>=.65)
 out=ROOT/'data'/'research'/'r4_v0'/'hourly'/f"r4_base_break_recovery_capacity_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(rep,indent=2),encoding='utf-8')
 joblib.dump({'version':VERSION,'features':FEATURES,'model':model,'label':rep['label'],'researchOnly':True},ROOT/'data'/'research'/'r4_v0'/'r4_base_break_recovery_capacity_v1.joblib')
 print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'train':rep['train'],'test':rep['test'],'mpqTransfer':rep['mpqTransferTest'],'byRelation':rep['byRelationTest'],'gate':rep['gate']},ensure_ascii=False))

if __name__=='__main__': main()
