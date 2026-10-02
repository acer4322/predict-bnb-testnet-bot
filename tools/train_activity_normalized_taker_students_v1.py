from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, average_precision_score, balanced_accuracy_score, f1_score, log_loss, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
REPORT=OUT/'activity_normalized_taker_students_v1_report.json'
HAZ_ART=OUT/'taker_hazard_1s_activity_norm_v1.joblib'
SIDE_ART=OUT/'taker_side_activity_norm_v1.joblib'
EFFECT_ART=OUT/'taker_effect_activity_norm_v1.joblib'
OWN_STATES=OUT/'taker_students_on_our_own_state_fast_v1_states.csv'
OWN_EVENTS=OUT/'taker_students_on_our_own_state_fast_v1_event_predictions.csv'
OWN_MARKETS=OUT/'taker_students_on_our_own_state_fast_v1_markets.csv'
FRESH_HAZ=OUT/'forward_hazard_states_v1.csv'
FRESH_TAKER=OUT/'forward_taker_states_v1.csv'

P=ROOT/'tools'/'train_target_maker_taker_coordination_big_v1.py'
spec=importlib.util.spec_from_file_location('actnorm_coord',P);coord=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=coord;spec.loader.exec_module(coord)

GRID_BOOK=list(coord.BOOK)
PRICE_FEATURES=list(coord.ECON)
AGE_COLS=['last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_age_ms','last_taker_up_age_ms','last_taker_down_age_ms']
FEATURES=[
 'seconds_left','maker_net_frac','maker_imbalance_ratio','maker_paired_coverage','taker_net_frac','taker_imbalance_ratio','taker_paired_coverage','combined_net_frac','combined_imbalance_ratio','combined_paired_coverage','floor_per_combined_gross','best_per_combined_gross','payoff_gap_frac','maker_taker_net_same_sign',
 'maker_activity_rate_eq_per_s','taker_activity_rate_eq_per_s',
 'maker_age_rel','maker_up_age_rel','maker_down_age_rel','taker_age_rel','taker_up_age_rel','taker_down_age_rel',
 'maker_age_log','maker_up_age_log','maker_down_age_log','taker_age_log','taker_up_age_log','taker_down_age_log',
 'maker_fills_1s_rel','maker_fills_5s_rel','maker_fills_10s_rel','taker_fills_1s_rel','taker_fills_5s_rel','taker_fills_10s_rel',
 'maker_mass_5s_rel','maker_mass_10s_rel','taker_mass_5s_rel','taker_mass_10s_rel',
 'maker_streak_frac','taker_streak_frac','maker_absnet_change_frac_10s','combined_absnet_change_frac_10s',
 *GRID_BOOK,*PRICE_FEATURES,
]
EPS=1e-9

def num(s):return pd.to_numeric(s,errors='coerce')
def safe_div(a,b):
 a=num(a);b=num(b);return a/b.where(b.abs()>EPS,np.nan)
def log_age(s):
 x=num(s)/1000.0;return np.log1p(x.where(x>=0,np.nan))
def transform(d:pd.DataFrame)->pd.DataFrame:
 x=pd.DataFrame(index=d.index);sec=num(d['seconds_left']);elapsed=(300.0-sec).clip(lower=.5)
 mg=num(d['maker_gross']);tg=num(d['taker_gross']);cg=num(d['combined_gross']);meq=mg/18.0;teq=tg/18.0;mrate=meq/elapsed;trate=teq/elapsed
 x['seconds_left']=sec;x['maker_net_frac']=safe_div(d['maker_net'],mg);x['maker_imbalance_ratio']=num(d['maker_imbalance_ratio']);x['maker_paired_coverage']=num(d['maker_paired_coverage']);x['taker_net_frac']=safe_div(d['taker_net'],tg);x['taker_imbalance_ratio']=num(d['taker_imbalance_ratio']);x['taker_paired_coverage']=num(d['taker_paired_coverage']);x['combined_net_frac']=safe_div(d['combined_net'],cg);x['combined_imbalance_ratio']=num(d['combined_imbalance_ratio']);x['combined_paired_coverage']=num(d['combined_paired_coverage']);x['floor_per_combined_gross']=safe_div(d['worst_case_floor'],cg);x['best_per_combined_gross']=safe_div(d['best_case_pnl'],cg);x['payoff_gap_frac']=safe_div(d['abs_payoff_gap'],cg);x['maker_taker_net_same_sign']=num(d['maker_taker_net_same_sign']);x['maker_activity_rate_eq_per_s']=mrate;x['taker_activity_rate_eq_per_s']=trate
 for role,rate in [('maker',mrate),('taker',trate)]:
  for suffix in ('','_up','_down'):
   src=f'last_{role}{suffix}_age_ms';name=f'{role}{suffix}_age';age_s=num(d[src])/1000.0;x[name+'_rel']=age_s*rate;x[name+'_log']=log_age(d[src])
 for role,rate in [('maker',mrate),('taker',trate)]:
  for w in (1,5,10):
   expected=(rate*w).where(rate>EPS,np.nan);x[f'{role}_fills_{w}s_rel']=safe_div(d[f'{role}_fills_{w}s'],expected)
 x['maker_mass_5s_rel']=safe_div(num(d['maker_shares_5s'])/18.0,mrate*5);x['maker_mass_10s_rel']=safe_div(num(d['maker_shares_10s'])/18.0,mrate*10);x['taker_mass_5s_rel']=safe_div(num(d['taker_shares_5s'])/18.0,trate*5);x['taker_mass_10s_rel']=safe_div(num(d['taker_shares_10s'])/18.0,trate*10)
 x['maker_streak_frac']=safe_div(d['maker_side_streak'],meq);x['taker_streak_frac']=safe_div(d['taker_side_streak'],teq);x['maker_absnet_change_frac_10s']=safe_div(d['maker_absnet_change_10s'],mg);x['combined_absnet_change_frac_10s']=safe_div(d['combined_absnet_change_10s'],cg)
 for c in GRID_BOOK+PRICE_FEATURES:x[c]=num(d[c])
 return x[FEATURES]

def binmet(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def multimet(y,p):
 y=list(map(str,y));p=list(map(str,p));return {'n':len(y),'accuracy':float(accuracy_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p)),'macroF1':float(f1_score(y,p,average='macro',zero_division=0)),'truthDistribution':pd.Series(y).value_counts().to_dict(),'predictedDistribution':pd.Series(p).value_counts().to_dict()}
def ebm(seed):return ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=96,max_interaction_bins=48,interactions=6,outer_bags=6,learning_rate=.035,max_rounds=2200,early_stopping_rounds=100,min_samples_leaf=8,n_jobs=-2,random_state=seed)
def exposure_counts(d,p):
 q=d[['market_id','decision_ms']].copy();q['p']=p;rows=[]
 for m,g in q.groupby('market_id'):
  g=g.sort_values('decision_ms');ts=g.decision_ms.astype('int64').to_numpy();pp=g.p.astype(float).to_numpy();dt=np.concatenate([np.diff(ts)/1000.0,[1.0]]) if len(ts)>1 else np.array([1.0]);dt=np.clip(dt,0,5);lam=-np.log(np.maximum(1e-9,1-np.clip(pp,0,.999999)));rows.append((int(m),float(np.sum(lam*dt))))
 return dict(rows)
def main():
 hz=pd.read_csv(coord.HAZARD_CSV);tk=pd.read_csv(coord.TAKER_CSV);Xh=transform(hz);Xt=transform(tk);hsp=coord.split_markets(hz);tsp=coord.split_markets(tk)
 hm=HistGradientBoostingClassifier(max_iter=350,learning_rate=.06,max_leaf_nodes=31,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=30,random_state=20260820);htr=hz.market_id.astype(int).isin(hsp['train']);hm.fit(Xh.loc[htr],hz.loc[htr,'label_taker_1s'].astype(int));joblib.dump({'version':'ACTIVITY_NORM_TAKER_V1','task':'hazard_1s','features':FEATURES,'model':hm},HAZ_ART)
 sm=ebm(20260821);em=ebm(20260822);ttr=tk.market_id.astype(int).isin(tsp['train']);sm.fit(Xt.loc[ttr],tk.loc[ttr,'label_side'].astype(str));em.fit(Xt.loc[ttr],tk.loc[ttr,'label_effect'].astype(str));joblib.dump({'version':'ACTIVITY_NORM_TAKER_V1','task':'side','features':FEATURES,'model':sm},SIDE_ART);joblib.dump({'version':'ACTIVITY_NORM_TAKER_V1','task':'effect','features':FEATURES,'model':em},EFFECT_ART)
 hist={'hazard':{},'side':{},'effect':{}}
 for k,ms in hsp.items():q=hz.market_id.astype(int).isin(ms);hist['hazard'][k]=binmet(hz.loc[q,'label_taker_1s'],hm.predict_proba(Xh.loc[q])[:,1])
 for k,ms in tsp.items():q=tk.market_id.astype(int).isin(ms);hist['side'][k]=multimet(tk.loc[q,'label_side'],sm.predict(Xt.loc[q]));hist['effect'][k]=multimet(tk.loc[q,'label_effect'],em.predict(Xt.loc[q]))
 fresh={}
 if FRESH_HAZ.exists():d=pd.read_csv(FRESH_HAZ);fresh['hazard']=binmet(d['label_taker_1s'],hm.predict_proba(transform(d))[:,1])
 if FRESH_TAKER.exists():d=pd.read_csv(FRESH_TAKER);z=transform(d);fresh['side']=multimet(d['label_side'],sm.predict(z));fresh['effect']=multimet(d['label_effect'],em.predict(z))
 own={}
 if OWN_STATES.exists() and OWN_EVENTS.exists() and OWN_MARKETS.exists():
  os=pd.read_csv(OWN_STATES);oe=pd.read_csv(OWN_EVENTS);om=pd.read_csv(OWN_MARKETS);p=hm.predict_proba(transform(os))[:,1];exp=exposure_counts(os.rename(columns={'our_market_id':'market_id'}),p);om=om.copy();om['activityNormExpected']=om.targetMarketId.map(lambda _:np.nan);# map by OUR market id from state grouping
  expdf=pd.DataFrame({'ourMarketId':list(exp.keys()),'activityNormExpected':list(exp.values())});om=om.drop(columns=['activityNormExpected']).merge(expdf,on='ourMarketId',how='left');actual=om.targetTakerParents.astype(float);ev=om.activityNormExpected.astype(float)
  zo=transform(oe);ps=sm.predict(zo);pe=em.predict(zo);own={'hazardExpected':{'markets':len(om),'expectedTotal':float(ev.sum()),'targetTotal':int(actual.sum()),'meanExpectedPerMarket':float(ev.mean()),'meanTargetPerMarket':float(actual.mean()),'medianExpected':float(ev.median()),'medianTarget':float(actual.median()),'spearmanExpectedVsActual':float(ev.corr(actual,method='spearman')),'mae':float((ev-actual).abs().mean())},'sideAtTrueTaker':multimet(oe.truth_side,ps),'effectAtTrueTaker':multimet(oe.truth_effect,pe),'meanP1AtTrueTaker':float(hm.predict_proba(zo)[:,1].mean())}
 rep={'reportVersion':'ACTIVITY_NORMALIZED_TAKER_STUDENTS_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'question':'Can dimensionless portfolio/lifecycle features preserve Target unseen/fresh Taker behavior while transferring better to OUR lower-activity endogenous state?','featureDesign':{'orderEquivalentShares':18,'activityRate':'cumulative role gross /18 / elapsed market seconds','ageRelative':'age_seconds * own cumulative activity rate','recentActivity':'recent fill/share activity divided by own average activity exposure','portfolio':'net/gross, coverage, floor/best/gap per gross','absoluteAgeAlsoLogCompressed':True},'historical':hist,'fresh':fresh,'ourOwnStateTransfer':own,'references':{'originalFrozenTargetFresh':{'hazard1sAuc':.8505,'sideBalanced':.8053,'effectBalanced':.7446},'originalFrozenOnOurState':{'expectedTakersPerMarket':2.6728,'sideBalanced':.5965,'effectBalanced':.3810}},'artifacts':{'hazard':str(HAZ_ART),'side':str(SIDE_ART),'effect':str(EFFECT_ART)},'guards':['Chronological market splits inherited from Big V1.','No OUR rows are used for training or tuning.','No threshold tuning; transfer hazard compared by probability exposure.','This is one fixed activity-normalization design, not a sweep.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
