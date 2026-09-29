from __future__ import annotations
import json, importlib.util
from pathlib import Path
import numpy as np, pandas as pd
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
EP=Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_EPISODES_V2_20260907.csv')
ACT=BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv'
CP=BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv'
PLAC=LANE/'C2_PLACEMENT_TIME_PASSIVE_VALUE_V1.csv'
SEED=20260907
spec=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(spec);spec.loader.exec_module(v1)
spec2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(v2)

def splitmap(a,q):return v2.frozen_split_map(a,q)
def slog(x):return np.sign(x)*np.log1p(np.abs(x))
def agg(hist,entry_debt,anchor,from_ms):
    z=hist[hist.event_ms>=from_ms] if np.isfinite(from_ms) else hist.iloc[0:0]
    rep=z[z.repair_qty>1e-9]; pure=rep[rep.economic_role.eq('REPAIR_ONLY')]; comp=rep[rep.economic_role.eq('COMPOSITE_CROSSING')]
    same=z[(z.birth_qty>1e-9)&z.birth_side.eq(anchor)]; opp=z[(z.birth_qty>1e-9)&z.birth_side.ne(anchor)]
    if len(z): start_debt=float(z.iloc[0].pre_outstanding_qty)
    else:start_debt=float(entry_debt)
    return {
      'repair_qty':float(rep.repair_qty.sum()),'repair_actions':int(len(rep)),'pure_repair_qty':float(pure.repair_qty.sum()),'composite_repair_qty':float(comp.repair_qty.sum()),
      'same_birth_qty':float(same.birth_qty.sum()),'opp_birth_qty':float(opp.birth_qty.sum()),
      'repair_floor_gain_pos':float(rep.delta_floor.clip(lower=0).sum()),'repair_floor_delta':float(rep.delta_floor.sum()),
      'debt_reduction':float(start_debt-entry_debt),'debt_reduction_pos':float(max(0,start_debt-entry_debt)),
    }
def build_features(ep,a):
    rows=[]
    by={int(m):z.sort_values('event_ms') for m,z in a.groupby('market_id',sort=False)}
    for r in ep.itertuples():
      h=by[int(r.market_id)]; h=h[h.event_ms<r.entry_ms]
      comps=h[h.economic_role.eq('COMPOSITE_CROSSING')]
      reset_ms=float(comps.iloc[-1].event_ms+1) if len(comps) else (float(h.iloc[0].event_ms) if len(h) else np.nan)
      clean=h[h.economic_role.eq('CLEAN_AGGREGATE_EXPAND')]
      last_clean_ms=float(clean.iloc[-1].event_ms+1) if len(clean) else np.nan
      rep=h[h.repair_qty>1e-9]
      last_rep=rep.iloc[-1] if len(rep) else None
      goodrep=rep[rep.delta_floor>1e-9]
      last_good=goodrep.iloc[-1] if len(goodrep) else None
      x={'market_id':int(r.market_id),'entry_ms':int(r.entry_ms),'anchor':r.anchor,'split':r.split,'renewed_clean':int(r.renewed_clean_lb_positive),
         'phase':float(r.seconds_left/300),'cur_predict_support':float(r.predict_support),'cur_strike_support':float(r.strike_support),
         'pre_debt':float(r.pre_outstanding_qty),'pre_floor':float(r.pre_floor),'pre_best':float(r.pre_best),'last_action_age_ms':float(r.last_action_age_ms),
         'time_since_repair_ms':float(r.entry_ms-last_rep.event_ms) if last_rep is not None else np.nan,
         'time_since_positive_floor_repair_ms':float(r.entry_ms-last_good.event_ms) if last_good is not None else np.nan}
      for sec in [5,10,20,60]:
        g=agg(h,float(r.pre_outstanding_qty),r.anchor,float(r.entry_ms-sec*1000));
        for k,v in g.items():x[f'w{sec}_{k}']=v
      for tag,st in [('reset',reset_ms),('clean',last_clean_ms)]:
        g=agg(h,float(r.pre_outstanding_qty),r.anchor,st)
        for k,v in g.items():x[f'{tag}_{k}']=v
      # conservative structural proxies, not accounting credits
      x['reset_repair_to_same_birth']=x['reset_repair_qty']/(1+x['reset_same_birth_qty'])
      x['reset_floor_gain_per_repair']=x['reset_repair_floor_gain_pos']/(1+x['reset_repair_qty'])
      x['clean_repair_fraction_of_entry_debt']=x['clean_repair_qty']/(1+x['pre_debt'])
      x['w20_repair_fraction_of_entry_debt']=x['w20_repair_qty']/(1+x['pre_debt'])
      rows.append(x)
    return pd.DataFrame(rows)

def fit_eval(d,groups,label):
  y=d[label].to_numpy(int);tr=d.split.eq('TRAIN').to_numpy();res={};pred={}
  for name,cols in groups.items():
    res[name]={};pred[name]={}
    for sp in ['VALIDATION','TEST']:
      te=d.split.eq(sp).to_numpy();x,w=v1.prepare(d.loc[tr,cols],d.loc[te,cols]);b=v1.fit_logit(x,y[tr]);p=1/(1+np.exp(-np.clip(w@b,-35,35)));pred[name][sp]=p;res[name][sp]=v1.metrics(y[te],p)
  inc={}
  for name in groups:
    if name=='C0_CURRENT':continue
    inc[name]={}
    for sp in ['VALIDATION','TEST']:
      z=d[d.split.eq(sp)].copy();inc[name][sp]=v2.market_cluster_delta(z,pred['C0_CURRENT'][sp],pred[name][sp],label=label,resamples=3000)
  return {'models':res,'incrementsVsCurrent':inc}

def binned(d,col,label):
  tr=d[d.split.eq('TRAIN')][col].dropna();
  if len(tr)<20:return None
  edges=np.unique(np.nanquantile(tr,[0,.25,.5,.75,1]));
  if len(edges)<3:return None
  out={'trainEdges':[float(x) for x in edges]}
  for sp in ['VALIDATION','TEST']:
    z=d[d.split.eq(sp)].copy();z['bin']=pd.cut(z[col],edges,include_lowest=True,duplicates='drop')
    out[sp]=[{'bin':str(k),'n':len(g),'markets':g.market_id.nunique(),'rate':float(g[label].mean()),'median':float(g[col].median())} for k,g in z.groupby('bin',observed=True)]
  return out

def main():
  ep=pd.read_csv(EP,low_memory=False);a=pd.read_csv(ACT,low_memory=False);q=pd.read_csv(CP,low_memory=False);p=pd.read_csv(PLAC,low_memory=False)
  sm=splitmap(a,q);ep['split']=ep.market_id.map(sm)
  keys=set(zip(p.market_id.astype(int),p.entry_ms.astype(int)));ep['maker_parent_admit']=[int((int(m),int(t)) in keys) for m,t in zip(ep.market_id,ep.entry_ms)]
  d=build_features(ep,a);d=d.merge(ep[['market_id','entry_ms','maker_parent_admit']],on=['market_id','entry_ms'],how='left',validate='one_to_one')
  # fixed low-dimensional groups; no threshold tuning
  current=['phase','cur_predict_support','cur_strike_support']
  recent=['w5_repair_qty','w20_repair_qty','w60_repair_qty','time_since_repair_ms']
  progress=['w20_debt_reduction_pos','w20_repair_floor_gain_pos','w60_debt_reduction_pos','w60_repair_floor_gain_pos']
  cycle=['reset_repair_qty','reset_same_birth_qty','reset_debt_reduction_pos','reset_repair_floor_gain_pos','reset_repair_to_same_birth','reset_floor_gain_per_repair']
  sinceclean=['clean_repair_qty','clean_debt_reduction_pos','clean_repair_floor_gain_pos','clean_repair_fraction_of_entry_debt','time_since_positive_floor_repair_ms']
  groups={'C0_CURRENT':current,'C1_RECENT_REPAIR':current+recent,'C2_REALIZED_PROGRESS':current+progress,'C3_RESET_CYCLE':current+cycle,'C4_SINCE_CLEAN':current+sinceclean,'C5_MINIMAL_CREDIT':current+['w20_repair_qty','w20_debt_reduction_pos','w20_repair_floor_gain_pos','time_since_repair_ms']}
  # transform unbounded magnitudes before model fit, preserving zeros/signs
  for c in set(sum(groups.values(),[])):
    if c in current:continue
    if c in d:d[c]=slog(d[c])
  admit=fit_eval(d,groups,'maker_parent_admit');renew=fit_eval(d,groups,'renewed_clean')
  diag_cols=['w20_repair_qty','w20_debt_reduction_pos','w20_repair_floor_gain_pos','reset_repair_qty','reset_repair_to_same_birth','clean_repair_qty','time_since_repair_ms']
  bins={c:{'makerAdmission':binned(d,c,'maker_parent_admit'),'confirmedRenewal':binned(d,c,'renewed_clean')} for c in diag_cols}
  # smoke: earliest three markets structurally, no outcome selection
  earliest=d.groupby('market_id').entry_ms.min().sort_values().index[:3];smoke=d[d.market_id.isin(earliest)]
  smoke_summary={'markets':[int(x) for x in earliest],'rows':len(smoke),'makerAdmissions':int(smoke.maker_parent_admit.sum()),'confirmedRenewals':int(smoke.renewed_clean.sum()),'featureNullRates':{c:float(smoke[c].isna().mean()) for c in set(recent+progress+cycle+sinceclean)}}
  out={'version':'OUR_C2_CYCLE_CREDIT_FALSIFICATION_V1','status':'RESEARCH_ONLY','cohort':{'rows':len(d),'markets':d.market_id.nunique(),'makerParentAdmissions':int(d.maker_parent_admit.sum()),'confirmedRenewals':int(d.renewed_clean.sum())},'smoke3':smoke_summary,'groups':groups,'makerAdmission':admit,'confirmedRenewal':renew,'quartileDiagnostics':bins,'guards':['All cycle/progress features use confirmed actions strictly before C2 entry.','Primary admission label is first HQ same-side Maker parent placement within 5s from existing canonical placement artifact; confirmed renewed clean is secondary.','Repair progress proxies are descriptive reconstructions, not Target private credit and not runtime authority.','Original frozen 72/24/24 market split; fixed low-dimensional groups; no TEST tuning.']}
  outp=LANE/'C2_CYCLE_CREDIT_FALSIFICATION_V1.json';outp.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');d.to_csv(LANE/'C2_CYCLE_CREDIT_FEATURES_V1.csv',index=False);print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
