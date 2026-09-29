from pathlib import Path
import os,json
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score
ROOT=Path.cwd(); SRC=ROOT/'.lan_worker_v1/staging/r4_p0b_target_objective_topology_rows_v2.csv'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s']
d=pd.read_csv(SRC).sort_values(['market_id','t','objective_family','parent_id']).copy(); rows=[]
for mid,g in d.groupby('market_id',sort=False):
    last={'PAIR_BALANCE':None,'STATE_SHAPING':None}; first={'PAIR_BALANCE':None,'STATE_SHAPING':None}; cnt={'PAIR_BALANCE':0,'STATE_SHAPING':0}; prev_set=None; prev_switch_t=None; event_idx=0
    for t,h in g.groupby('t',sort=True):
        cur=set(h.objective_family.astype(str))
        if prev_set is not None:
            common={
              'prev_has_pair_balance':int('PAIR_BALANCE' in prev_set),'prev_has_state_shaping':int('STATE_SHAPING' in prev_set),'prev_parallel_family_set':int(len(prev_set)>1),
              'ledger_event_index':event_idx,
              'ledger_pb_time_since_seen_s':(t-last['PAIR_BALANCE'])/1000. if last['PAIR_BALANCE'] is not None else 999.,
              'ledger_ss_time_since_seen_s':(t-last['STATE_SHAPING'])/1000. if last['STATE_SHAPING'] is not None else 999.,
              'ledger_pb_age_s':(t-first['PAIR_BALANCE'])/1000. if first['PAIR_BALANCE'] is not None else 0.,
              'ledger_ss_age_s':(t-first['STATE_SHAPING'])/1000. if first['STATE_SHAPING'] is not None else 0.,
              'ledger_pb_seen_count':cnt['PAIR_BALANCE'],'ledger_ss_seen_count':cnt['STATE_SHAPING'],
              'ledger_time_since_familyset_change_s':(t-prev_switch_t)/1000. if prev_switch_t is not None else 999.,
              'ledger_prev_family_count':len(prev_set)
            }
            for _,r in h.iterrows():
                z=r.to_dict(); z.update(common); z['label']=int(str(r.objective_family) not in prev_set); rows.append(z)
        if prev_set is not None and cur!=prev_set: prev_switch_t=t
        for fam in cur:
            if fam in last:
                last[fam]=t; first[fam]=t if first[fam] is None else first[fam]; cnt[fam]+=1
        prev_set=cur; event_idx+=1
x=pd.DataFrame(rows); mids=x.market_id.drop_duplicates().tolist(); cuts=[int(len(mids)*.25),int(len(mids)*.5),int(len(mids)*.75)]
OCC=['prev_has_pair_balance','prev_has_state_shaping','prev_parallel_family_set']; LED=['ledger_event_index','ledger_pb_time_since_seen_s','ledger_ss_time_since_seen_s','ledger_pb_age_s','ledger_ss_age_s','ledger_pb_seen_count','ledger_ss_seen_count','ledger_time_since_familyset_change_s','ledger_prev_family_count']; groups={'BASE':BASE,'BASE_OCCUPANCY':BASE+OCC,'BASE_OCCUPANCY_LEDGER_CLEAN':BASE+OCC+LED}
def ev(y,p,s): return {'ba':float(balanced_accuracy_score(y,p)),'switchRecall':float(((p==1)&(y==1)).sum()/max(1,(y==1).sum())),'continueRecall':float(((p==0)&(y==0)).sum()/max(1,(y==0).sum())),'auc':float(roc_auc_score(y,s)),'ap':float(average_precision_score(y,s))}
out={'version':'R4_MANAGEMENT_OBJECTIVE_TRANSITION_V3B_LEDGER_MEMORY_CLEAN','researchOnly':True,'strictPast':True,'candidateActionIdentityExcluded':True,'rows':len(x),'markets':len(mids),'groups':{}}
for name,fs in groups.items():
    rr=[]
    for i,c in enumerate(cuts):
        trm=set(mids[:c]); tem=set(mids[c:cuts[i+1] if i+1<len(cuts) else len(mids)]); tr=x[x.market_id.isin(trm)]; te=x[x.market_id.isin(tem)]; Xtr=tr[fs].replace([np.inf,-np.inf],np.nan).fillna(0); Xte=te[fs].replace([np.inf,-np.inf],np.nan).fillna(0); ytr=tr.label.values; yte=te.label.values
        cnty=np.bincount(ytr,minlength=2).astype(float); cw=np.array([cnty.sum()/max(v,1) for v in cnty]); cw=cw/cw.mean(); w=np.array([cw[v] for v in ytr]); m=HistGradientBoostingClassifier(max_iter=200,max_leaf_nodes=15,l2_regularization=2,random_state=41).fit(Xtr,ytr,sample_weight=w); s=m.predict_proba(Xte)[:,1]; p=(s>=.5).astype(int); rr.append(ev(yte,p,s))
    out['groups'][name]={'folds':rr,'meanBA':float(np.mean([r['ba'] for r in rr])),'worstBA':float(np.min([r['ba'] for r in rr])),'meanAUC':float(np.mean([r['auc'] for r in rr])),'meanSwitchRecall':float(np.mean([r['switchRecall'] for r in rr]))}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2))