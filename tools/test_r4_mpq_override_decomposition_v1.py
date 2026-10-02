from __future__ import annotations
import json, importlib.util, sys, math
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_marginal_pair_quality_replication_v2.py'
spec=importlib.util.spec_from_file_location('r4_mpq_rep_v2_override',P); m=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=m; spec.loader.exec_module(m)
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_MPQ_OVERRIDE_DECOMPOSITION_V1'
N_MARKETS=4500
FEATURES=[
 'event_index_norm','pre_floor_per_gross','pre_edge','pre_coverage','pre_absnet_ratio','pre_cost_per_gross',
 'pre_upside_per_gross','reserve_spend_ratio','reserve_spend_per_gross','post_floor_per_gross','post_edge',
 'candidate_price','candidate_shares_per_gross','candidate_role_taker','relation_surplus_side','relation_weak_side_cross',
 'events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_shares_15s_per_gross','opp_side_shares_15s_per_gross',
 'floor_change_15s_per_gross','edge_change_15s'
]

def apply(state,z):
 up,down,cu,cd=state; sh=float(z['sh']); px=float(z['px'])
 if z['side']=='UP': up+=sh; cu+=sh*px
 else: down+=sh; cd+=sh*px
 return up,down,cu,cd

def geom(state): return m.geom(*state)

def safe_div(a,b): return float(a/b) if abs(b)>1e-12 else 0.0

def build_rows():
 meta,ev=m.load(N_MARKETS); rows=[]
 for mid,wend,w in meta:
  events=ev.get(mid,[]); state=(0.,0.,0.,0.); hist=[]
  for i,z in enumerate(events):
   pre=geom(state); post_state=apply(state,z); post=geom(post_state)
   reserve=max(0.,pre['floor']-post['floor']); flag=pre['floor']>0 and reserve>1e-12 and post['edge']<0
   if flag:
    gross=max(pre['gross'],1e-9); surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
    relation='SURPLUS_SIDE' if z['side']==surplus else 'WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT'
    t0=int(z['t']); q5=[a for a in hist if t0-int(a['t'])<=5000]; q15=[a for a in hist if t0-int(a['t'])<=15000]
    old_state=(0.,0.,0.,0.)
    # Reconstruct state just before the oldest 15s event, or use current state if none.
    cutoff=t0-15000
    for a in events[:i]:
     if int(a['t'])<cutoff: old_state=apply(old_state,a)
    old=geom(old_state)
    same=sum(float(a['sh']) for a in q15 if surplus!='FLAT' and a['side']==surplus)
    opp=sum(float(a['sh']) for a in q15 if surplus!='FLAT' and a['side']!=surplus)
    x={
     'event_index_norm': i/max(1,len(events)-1), 'pre_floor_per_gross':safe_div(pre['floor'],gross), 'pre_edge':pre['edge'],
     'pre_coverage':pre['coverage'], 'pre_absnet_ratio':safe_div(pre['absnet'],gross), 'pre_cost_per_gross':safe_div(pre['cost'],gross),
     'pre_upside_per_gross':safe_div(max(pre['up']-pre['cost'],pre['down']-pre['cost']),gross), 'reserve_spend_ratio':safe_div(reserve,max(pre['floor'],1e-9)),
     'reserve_spend_per_gross':safe_div(reserve,gross), 'post_floor_per_gross':safe_div(post['floor'],max(post['gross'],1e-9)), 'post_edge':post['edge'],
     'candidate_price':float(z['px']), 'candidate_shares_per_gross':safe_div(float(z['sh']),gross), 'candidate_role_taker':float(z['role']=='TAKER'),
     'relation_surplus_side':float(relation=='SURPLUS_SIDE'), 'relation_weak_side_cross':float(relation=='WEAK_SIDE_CROSS'),
     'events_5s':float(len(q5)), 'events_15s':float(len(q15)), 'maker_events_15s':float(sum(a['role']=='MAKER' for a in q15)),
     'taker_events_15s':float(sum(a['role']=='TAKER' for a in q15)), 'same_side_shares_15s_per_gross':safe_div(same,gross),
     'opp_side_shares_15s_per_gross':safe_div(opp,gross), 'floor_change_15s_per_gross':safe_div(pre['floor']-old['floor'],gross),
     'edge_change_15s':pre['edge']-old['edge'],
    }
    s=post_state; recover60=False
    for zz in events[i+1:]:
     if int(zz['t'])-t0>60000: break
     s=apply(s,zz)
     if geom(s)['floor']>0: recover60=True; break
    # Fixed-downstream single-event counterfactual for offline upside label.
    b=state
    for j in range(i,len(events)): b=apply(b,events[j])
    cf=state
    for j in range(i+1,len(events)): cf=apply(cf,events[j])
    gb,gc=geom(b),geom(cf)
    pnl_b=(gb['up'] if w=='UP' else gb['down'])-gb['cost']; pnl_cf=(gc['up'] if w=='UP' else gc['down'])-gc['cost']
    rows.append({'marketId':mid,'windowEndMs':wend,'x':[float(x[f]) for f in FEATURES],'recover60':int(recover60),'keepPnlBetter':int(pnl_b>pnl_cf+1e-9),'both':int(recover60 and pnl_b>pnl_cf+1e-9),'relation':relation,'role':z['role']})
   hist.append(z); state=post_state
 return meta,rows

def fit_eval(rows,label):
 markets=sorted(set((r['windowEndMs'],r['marketId']) for r in rows)); n=len(markets); a=int(.70*n); b=int(.85*n)
 g=[set(mid for _,mid in markets[:a]),set(mid for _,mid in markets[a:b]),set(mid for _,mid in markets[b:])]
 out={}; model=None
 for idx,name in enumerate(['train','validation','test']):
  rr=[r for r in rows if r['marketId'] in g[idx]]; X=np.asarray([r['x'] for r in rr],dtype=float); y=np.asarray([r[label] for r in rr],dtype=int)
  if idx==0:
   model=HistGradientBoostingClassifier(max_iter=120,learning_rate=.05,max_leaf_nodes=9,min_samples_leaf=8,l2_regularization=2.,random_state=20260826).fit(X,y)
  p=model.predict_proba(X)[:,1]
  out[name]={'markets':len(g[idx]),'rows':len(rr),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(set(y))>1 else None,'balancedAccuracyAt05':float(balanced_accuracy_score(y,p>=.5)) if len(set(y))>1 else None}
 return out

def main():
 meta,rows=build_rows(); reports={k:fit_eval(rows,k) for k in ['recover60','keepPnlBetter','both']}
 report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'cohort':{'ordinaryMarkets':len(meta),'mpqFlaggedRows':len(rows),'uniqueFlaggedMarkets':len(set(r['marketId'] for r in rows)),'sealed20260816':True},'features':FEATURES,'labels':{'recover60':'floor returns >0 within 60s after MPQ BASE_BREAK','keepPnlBetter':'keeping flagged fill has higher terminal PnL than single-fill-suppression counterfactual; winner offline only','both':'recover60 AND keepPnlBetter'},'metrics':reports,'counts':{k:int(sum(r[k] for r in rows)) for k in ['recover60','keepPnlBetter','both']},'guards':{'strictPastRuntimeFeatures':True,'winnerOnlyOfflineLabel':True,'noEchtgeldTraining':True,'noThresholdSweep':True,'mpqRuleFrozen':True}}
 # preregistered evidence interpretation: predictor head is promising only if chronological TEST AUC >= .70.
 report['gates']={k:{'requiredTestAuc':0.70,'pass':bool(reports[k]['test']['auc'] is not None and reports[k]['test']['auc']>=.70)} for k in reports}
 out=ROOT/'data'/'research'/'r4_v0'/'hourly'/f"r4_mpq_override_decomposition_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json"; out.write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'cohort':report['cohort'],'counts':report['counts'],'test':{k:v['test'] for k,v in reports.items()},'gates':report['gates']},ensure_ascii=False))

if __name__=='__main__': main()
