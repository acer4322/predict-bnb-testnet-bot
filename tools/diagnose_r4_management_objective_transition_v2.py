from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_p0b_target_objective_topology_rows_v2.csv'
OUT=P/'r4_management_objective_transition_v2_diagnostic.json'

BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']
DELTA_COLS=['floor','upside','absNet','coverage','risk_deficit','current_commitment']

def model(seed):
    return HistGradientBoostingClassifier(learning_rate=.06,max_iter=140,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,random_state=seed)

def metrics(y,p,prob):
    y=np.asarray(y).astype(str); p=np.asarray(p).astype(str)
    yb=(y=='SWITCH_OR_OPEN').astype(int)
    out={
        'balancedAccuracy':float(balanced_accuracy_score(y,p)),
        'counts':pd.Series(y).value_counts().to_dict(),
        'perClass':{c:float(np.mean(p[y==c]==c)) for c in sorted(set(y))},
    }
    if len(np.unique(yb))==2:
        out['switchAuc']=float(roc_auc_score(yb,prob))
        out['switchAp']=float(average_precision_score(yb,prob))
    return out

def main():
    d=pd.read_csv(SRC).sort_values(['market_id','t','parent_id']).copy()

    # V1 flaw audit: row-wise shift creates artificial ordering among simultaneous parallel responsibilities.
    g=d.groupby('market_id',sort=False)
    d['v1_prev_t']=g.t.shift(1)
    d['v1_prev_family']=g.objective_family.shift(1)
    d['v1_switch']=d.v1_prev_family.notna() & d.objective_family.ne(d.v1_prev_family)
    same_t=d.v1_prev_t.eq(d.t)

    # Strictly-earlier event-time family occupancy. No within-timestamp ordering is allowed.
    time_family_sets=(d.groupby(['market_id','t'],sort=False).objective_family
                        .agg(lambda x: tuple(sorted(set(map(str,x))))))
    prev_sets=time_family_sets.groupby(level=0).shift(1)
    prev_map=prev_sets.to_dict()
    d['prev_family_set']=[prev_map.get((m,t),np.nan) for m,t in zip(d.market_id,d.t)]
    d=d[d.prev_family_set.map(lambda x:isinstance(x,tuple))].copy()
    d['transition']=np.where([str(f) in set(s) for f,s in zip(d.objective_family,d.prev_family_set)],'CONTINUE','SWITCH_OR_OPEN')
    d['prev_has_pair_balance']=[float('PAIR_BALANCE' in set(s)) for s in d.prev_family_set]
    d['prev_has_state_shaping']=[float('STATE_SHAPING' in set(s)) for s in d.prev_family_set]
    d['prev_parallel_family_set']=[float(len(set(s))>1) for s in d.prev_family_set]

    # Strictly-earlier timestamp economic state for deltas; one representative state per event-time.
    ts=d.groupby(['market_id','t'],sort=False)[DELTA_COLS].first()
    prev_ts=ts.groupby(level=0).shift(1)
    for c in DELTA_COLS:
        mp=prev_ts[c].to_dict()
        d[f'{c}_delta_prev_event']=pd.to_numeric(d[c],errors='coerce')-[mp.get((m,t),np.nan) for m,t in zip(d.market_id,d.t)]

    fs=BASE+['prev_has_pair_balance','prev_has_state_shaping','prev_parallel_family_set']+[f'{c}_delta_prev_event' for c in DELTA_COLS]
    mids=sorted(d.market_id.unique(),key=lambda m:d.loc[d.market_id==m,'t'].min())
    folds=[]
    for i,cut in enumerate((.60,.70,.80)):
        k=int(len(mids)*cut); v=int(len(mids)*.10)
        tr=d[d.market_id.isin(mids[:k])].copy(); te=d[d.market_id.isin(mids[k:k+v])].copy()
        ytr=tr.transition.to_numpy(); yte=te.transition.to_numpy()
        base=np.repeat('CONTINUE',len(te))
        baseprob=np.zeros(len(te),dtype=float)

        m=model(9200+i).fit(tr[fs],ytr)
        pred=m.predict(te[fs]); cls=list(m.classes_); sw_idx=cls.index('SWITCH_OR_OPEN'); prob=m.predict_proba(te[fs])[:,sw_idx]

        vc=pd.Series(ytr).value_counts(); w={c:len(ytr)/(len(vc)*n) for c,n in vc.items()}
        sw=np.array([w[x] for x in ytr],dtype=float)
        mb=model(9300+i).fit(tr[fs],ytr,sample_weight=sw)
        predb=mb.predict(te[fs]); clsb=list(mb.classes_); swb_idx=clsb.index('SWITCH_OR_OPEN'); probb=mb.predict_proba(te[fs])[:,swb_idx]

        folds.append({
            'trainMarkets':int(tr.market_id.nunique()),'testMarkets':int(te.market_id.nunique()),
            'alwaysContinue':metrics(yte,base,baseprob),
            'modelUnweighted':metrics(yte,pred,prob),
            'modelBalancedWeight':metrics(yte,predb,probb),
        })

    v1_old_switch=int((d['v1_switch']).sum())
    disagreements=int(np.sum(d['v1_switch'].to_numpy() != (d.transition.eq('SWITCH_OR_OPEN')).to_numpy()))
    rep={
        'version':'R4_MANAGEMENT_OBJECTIVE_TRANSITION_V2_DIAGNOSTIC',
        'researchOnly':True,'promotionEligible':False,'strictPast':True,
        'target':'At a Target responsibility event, does its objective family CONTINUE from the strictly-earlier event-time family occupancy, or SWITCH/OPEN relative to it?',
        'semanticFix':'No ordering among responsibilities sharing the same market_id+t. Previous family memory is a family SET from the strictly earlier timestamp.',
        'v1LabelAudit':{
            'sourceRows':26584,
            'sameTimestampAdjacencyRows':int(same_t.sum()),
            'v1OldSwitchesAllSourceRows':int((pd.read_csv(SRC).sort_values(['market_id','t','parent_id']).groupby('market_id').objective_family.apply(lambda x:x.ne(x.shift(1)) & x.shift(1).notna()).sum())),
            'v1SwitchesOnSameTimestampAdjacency':int((same_t & d.reindex(same_t.index,fill_value=False).get('v1_switch',False)).sum()) if False else None,
            'strictValidRows':int(len(d)),
            'labelDisagreementsOnStrictValidRows':disagreements,
            'labelDisagreementRate':float(disagreements/len(d)),
        },
        'features':fs,
        'folds':folds,
        'summary':{
            'unweightedMeanBA':float(np.mean([x['modelUnweighted']['balancedAccuracy'] for x in folds])),
            'balancedWeightMeanBA':float(np.mean([x['modelBalancedWeight']['balancedAccuracy'] for x in folds])),
            'balancedWeightMeanSwitchRecall':float(np.mean([x['modelBalancedWeight']['perClass']['SWITCH_OR_OPEN'] for x in folds])),
            'balancedWeightMeanSwitchAuc':float(np.mean([x['modelBalancedWeight']['switchAuc'] for x in folds])),
            'alwaysContinueMeanBA':0.5,
        },
        'interpretation':'V2 tests lifecycle transition semantics after removing impossible within-timestamp ordering. Balanced sample weights are a diagnostic for minority-class learnability, not a promotion threshold sweep.'
    }
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'summary':rep['summary'],'folds':folds},indent=2))

if __name__=='__main__':main()
