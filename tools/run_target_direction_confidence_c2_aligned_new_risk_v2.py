from __future__ import annotations
import argparse, json, importlib.util
from pathlib import Path
import numpy as np, pandas as pd

BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
ACTIONS=BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv'
CHECKPOINTS=BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv'
BRIDGE=BASE/'35_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_RETROSPECTIVE_V1.csv'
SEED=20260907
# Reuse the now episode-key-parity-validated V1 mechanics for carrier allocation and entry generation.
spec=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py')
v1=importlib.util.module_from_spec(spec); spec.loader.exec_module(v1)

def frozen_split_map(actions,cp):
    starts=pd.concat([actions.groupby('market_id').event_ms.min(),cp.groupby('market_id').checkpoint_ms.min()],axis=1).min(axis=1).sort_values(kind='stable')
    mids=[int(x) for x in starts.index]
    return {m:('TRAIN' if i<72 else 'VALIDATION' if i<96 else 'TEST') for i,m in enumerate(mids)}

def add_end(entries,actions,cp):
    ends={}
    for mid,z in cp.groupby('market_id',sort=False):
        ev=actions[actions.market_id==mid]
        end=min(float(np.nanmedian(z.checkpoint_ms+1000*z.seconds_left)),float(max(z.checkpoint_ms.max(),ev.event_ms.max())))
        ends[int(mid)]=end
    out=entries.copy();out['observation_end_ms']=out.market_id.map(ends);out['full5s_observed']=out.entry_ms.add(5000).le(out.observation_end_ms)
    return out

def market_cluster_delta(d,p0,p1,label='renewed_clean_lb_positive',resamples=2000):
    y=d[label].to_numpy(float); p0=np.clip(np.asarray(p0,float),1e-7,1-1e-7);p1=np.clip(np.asarray(p1,float),1e-7,1-1e-7)
    ll0=-(y*np.log(p0)+(1-y)*np.log(1-p0));ll1=-(y*np.log(p1)+(1-y)*np.log(1-p1))
    br0=(p0-y)**2;br1=(p1-y)**2
    g=pd.DataFrame({'mid':d.market_id.to_numpy(),'n':1,'dll':ll0-ll1,'dbr':br0-br1}).groupby('mid').sum().to_numpy(float)
    rng=np.random.default_rng(SEED); ix=rng.integers(0,len(g),size=(resamples,len(g))); z=g[ix].sum(1)
    dll=z[:,1]/z[:,0];dbr=z[:,2]/z[:,0]
    return {'rows':int(len(d)),'markets':int(d.market_id.nunique()),'dLogLossImprovement':float((ll0-ll1).mean()),'dBrierImprovement':float((br0-br1).mean()),'dLogLoss95CI':[float(x) for x in np.quantile(dll,[.025,.975])],'dBrier95CI':[float(x) for x in np.quantile(dbr,[.025,.975])],'resamples':resamples}

def fit_models(d,split_map):
    d=d.copy(); d['split']=d.market_id.map(split_map)
    s=d.anchor.map({'UP':1.,'DOWN':-1.})
    f=pd.DataFrame(index=d.index)
    f['phase']=d.seconds_left/300
    f['opposition_predict']=-d.predict_support;f['opposition_strike']=-d.strike_support
    f['oriented_spot_queue']=s*d.spot_queue_imbalance;f['oriented_futures_queue']=s*d.futures_queue_imbalance
    f['oriented_spot_taker1s']=s*d.spot_taker_imbalance_1s;f['oriented_futures_taker1s']=s*d.futures_taker_imbalance_1s
    f['oriented_spot_ret1s']=s*d.spot_return_1s_bps;f['oriented_fut_ret1s']=s*d.futures_return_1s_bps
    market=list(f.columns)
    f['log_net_abs']=np.log1p(d.pre_net_shares.abs());f['log_debt']=np.log1p(d.pre_outstanding_qty.clip(lower=0))
    f['floor']=v1.safe_log1p(d.pre_floor);f['best']=v1.safe_log1p(d.pre_best);f['cost_per_gross']=d.pre_cost/d.pre_gross_shares.replace(0,np.nan)
    f['last_action_age']=np.log1p(d.last_action_age_ms)
    for role in ['CLEAN_AGGREGATE_EXPAND','REPAIR_ONLY','COMPOSITE_CROSSING']:f['last_'+role]=d.last_economic_role.eq(role).astype(float)
    portfolio=[c for c in f.columns if c not in market]
    f['prior_side_ask']=np.where(s==1,d.predict_up_ask,d.predict_down_ask);f['repair_ask']=np.where(s==1,d.predict_down_ask,d.predict_up_ask)
    f['pair_ask_surplus']=1-d.predict_up_ask-d.predict_down_ask;f['repair_price_x_debt']=f.repair_ask*np.log1p(d.pre_outstanding_qty.clip(lower=0))
    economics=['prior_side_ask','repair_ask','pair_ask_surplus','repair_price_x_debt']
    f['clean_age']=np.log1p(d.clean_age_ms);f['clean_run']=np.log1p(d.clean_run_length); history=['clean_age','clean_run']
    groups={'M0_MARKET':market,'M1_PLUS_PORTFOLIO':market+portfolio,'M2_PLUS_ECONOMICS':market+portfolio+economics,'M3_PLUS_HISTORY':market+portfolio+economics+history}
    y=d.renewed_clean_lb_positive.to_numpy(int);tr=d.split.eq('TRAIN').to_numpy(); out={};pred={}
    for name,cols in groups.items():
        out[name]={};pred[name]={}
        for sp in ['VALIDATION','TEST']:
            te=d.split.eq(sp).to_numpy();x,w=v1.prepare(f.loc[tr,cols],f.loc[te,cols]);beta=v1.fit_logit(x,y[tr]);p=1/(1+np.exp(-np.clip(w@beta,-35,35)))
            out[name][sp]=v1.metrics(y[te],p);pred[name][sp]=p
    increments={}
    for new,base in [('M1_PLUS_PORTFOLIO','M0_MARKET'),('M2_PLUS_ECONOMICS','M1_PLUS_PORTFOLIO'),('M3_PLUS_HISTORY','M2_PLUS_ECONOMICS')]:
        increments[new+'_minus_'+base]={}
        for sp in ['VALIDATION','TEST']:
            dd=d[d.split.eq(sp)].copy();increments[new+'_minus_'+base][sp]=market_cluster_delta(dd,pred[base][sp],pred[new][sp])
    return {'splitRows':d.split.value_counts().to_dict(),'splitMarkets':d.groupby('split').market_id.nunique().to_dict(),'features':groups,'models':out,'increments':increments}

def quartiles(d,col):
    tr=d[d.split.eq('TRAIN')][col].dropna()
    if len(tr)<20:return None
    qs=np.unique(np.nanquantile(tr,[0,.25,.5,.75,1]));
    if len(qs)<3:return None
    out={}
    for sp in ['VALIDATION','TEST']:
        z=d[d.split.eq(sp)].copy();z['bin']=pd.cut(z[col],bins=qs,include_lowest=True,duplicates='drop')
        out[sp]=[{'bin':str(k),'n':int(len(g)),'markets':int(g.market_id.nunique()),'rate':float(g.renewed_clean_lb_positive.mean()),'mean':float(g[col].mean())} for k,g in z.groupby('bin',observed=True)]
    return {'trainEdges':[float(x) for x in qs],**out}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--csv');a=ap.parse_args()
    actions=pd.read_csv(ACTIONS,low_memory=False).sort_values(['market_id','event_ms']).reset_index(drop=True)
    cp=pd.read_csv(CHECKPOINTS,low_memory=False).sort_values(['market_id','checkpoint_ms','source_snapshot_id']).reset_index(drop=True)
    bridge=pd.read_csv(BRIDGE,low_memory=False)
    smap=frozen_split_map(actions,cp)
    entries=v1.build_entries(actions,cp); entries=add_end(entries,actions,cp);entries=v1.add_correct_clean_history(entries,actions)
    lab=v1.label_outcomes(entries,actions,bridge)
    # exact GPT6 C2 parity + full 5s + current net aligned.
    primary=lab[lab.net_aligned_prior & lab.full5s_observed].copy();primary['split']=primary.market_id.map(smap)
    model=fit_models(primary,smap)
    # Episode-level route provenance for the conservative positive outcome.
    route_summary={'positiveEpisodes':int(primary.renewed_clean_lb_positive.sum()),'positiveRate':float(primary.renewed_clean_lb_positive.mean()),'rows':int(len(primary)),'markets':int(primary.market_id.nunique()),'hqPostOnlyActionCount':int(primary.hq_postonly_clean_actions.sum()),'takerMixedActionCount':int(primary.taker_or_mixed_clean_actions.sum()),'renewedCleanLowerQty':float(primary.renewed_clean_birth_lower_5s.sum())}
    fields=['predict_support','strike_support','pre_outstanding_qty','pre_floor','pre_best','last_action_age_ms','clean_age_ms','clean_run_length']
    bins={c:quartiles(primary,c) for c in fields}
    report={'version':'OUR_C2_ALIGNED_RENEWED_RISK_FALSIFICATION_V2','status':'RESEARCH_ONLY','episodeParity':{'C2':int(len(lab)),'expectedGPT6C2':633,'keySemantics':'exact GPT6 first C2 per conflict/reset run'},'primary':route_summary,'frozenSplit':{'TRAIN':72,'VALIDATION':24,'TEST':24},'model':model,'quartileDiagnostics':bins,'guards':['Primary = C2 conflict, current net aligned to prior clean, full 5s observable.','Positive label requires conservative lower-bound same-side CLEAN birth attributable to post-conflict HQ Maker or Taker/Mixed; PRE and unresolved Maker cannot create positive lower bound.','Retrospective Target parent timing is diagnostic only.','Models predict Target trace, not profitability or runtime authority.']}
    Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if a.csv:primary.to_csv(a.csv,index=False)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
