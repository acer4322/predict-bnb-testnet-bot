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
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
HIST=OUT/'post_taker_maker_reentry_hazard_v0.csv'
HAND=OUT/'post_taker_handoff_states_v1.csv'
TAKER=OUT/'taker_event_states_v1.csv'
FRESH=OUT/'post_taker_maker_reentry_hazard_forward_states_v0.csv'
FHAND=OUT/'forward_handoff_states_v1.csv'
FTAKER=OUT/'forward_taker_states_v1.csv'
SAME_ART=OUT/'post_taker_reentry_same_plus_memory_v0.joblib'
OPP_ART=OUT/'post_taker_reentry_opp_plus_memory_v0.joblib'
REPORT=OUT/'post_taker_reentry_by_effect_v0_report.json'

P=ROOT/'tools'/'train_target_maker_taker_coordination_big_v1.py'
spec=importlib.util.spec_from_file_location('effect_strata_coord',P);coord=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=coord;spec.loader.exec_module(coord)

EFFECTS=['BUILD_FROM_FLAT','REPAIR_EFFECT','ADD_EFFECT']


def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None}


def attach_effect(reentry:pd.DataFrame,handoff:pd.DataFrame,taker:pd.DataFrame)->pd.DataFrame:
 if reentry.empty or handoff.empty or taker.empty:return pd.DataFrame()
 r=reentry.copy();h=handoff.copy();t=taker[['parent_id','label_effect']].copy()
 r['taker_end_ms']=(pd.to_numeric(r['checkpoint_ms'])-pd.to_numeric(r['time_since_taker_ms'])).round().astype('int64')
 h['taker_end_ms']=(pd.to_numeric(h['checkpoint_ms'])-1).astype('int64')
 for d in (r,h):
  d['_sh']=pd.to_numeric(d['intervention_shares']).round(6)
  d['_px']=pd.to_numeric(d['intervention_avg_price']).round(6)
  d['_side']=d['intervention_side'].astype(str)
 keys=['market_id','taker_end_ms','_side','_sh','_px']
 hm=h[keys+['parent_id']].drop_duplicates(keys)
 x=r.merge(hm,on=keys,how='left').merge(t,on='parent_id',how='left')
 return x


def score(df,art):
 fs=list(art['features']);X=df.reindex(columns=fs).apply(pd.to_numeric,errors='coerce');return art['model'].predict_proba(X)[:,1]


def eval_part(df:pd.DataFrame)->dict[str,Any]:
 if df.empty:return {'n':0}
 out={'n':len(df),'parents':int(df['parent_id'].nunique()) if 'parent_id' in df else None,'markets':int(df['market_id'].nunique())}
 for e in EFFECTS:
  q=df[df.label_effect.astype(str)==e]
  out[e]={'rows':len(q),'parents':int(q.parent_id.nunique()) if len(q) else 0,'markets':int(q.market_id.nunique()) if len(q) else 0}
  if len(q):
   out[e]['same']=metric(q.label_same_next1s.astype(int),q.p_same)
   out[e]['opp']=metric(q.label_opp_next1s.astype(int),q.p_opp)
 return out


def main():
 same=joblib.load(SAME_ART);opp=joblib.load(OPP_ART)
 hist=pd.read_csv(HIST); hand=pd.read_csv(HAND); taker=pd.read_csv(TAKER)
 h=attach_effect(hist,hand,taker)
 h=h[h.label_effect.isin(EFFECTS)].copy()
 h['p_same']=score(h,same);h['p_opp']=score(h,opp)
 splits=coord.split_markets(h)
 rep={'reportVersion':'POST_TAKER_REENTRY_BY_EFFECT_V0','researchOnly':True,'question':'Does Target post-Taker SAME/OPP sequential re-entry behavior differ by active-intervention effect (BUILD/REPAIR/ADD)?','historical':{},'fresh':None,'joinCoverage':{'historicalRows':len(hist),'joinedRows':len(h),'joinedParents':int(h.parent_id.nunique())}}
 for k,ms in splits.items(): rep['historical'][k]=eval_part(h[h.market_id.astype(int).isin(ms)])
 if FRESH.exists() and FHAND.exists() and FTAKER.exists():
  fr=pd.read_csv(FRESH);fh=pd.read_csv(FHAND);ft=pd.read_csv(FTAKER)
  z=attach_effect(fr,fh,ft);z=z[z.label_effect.isin(EFFECTS)].copy()
  if len(z):
   z['p_same']=score(z,same);z['p_opp']=score(z,opp);rep['fresh']=eval_part(z);rep['joinCoverage'].update({'freshRows':len(fr),'freshJoinedRows':len(z),'freshJoinedParents':int(z.parent_id.nunique())})
 rep['interpretationBoundary']='BUILD_FROM_FLAT is sparse. Effect-stratified metrics are diagnostic and do not justify separate models unless chronological/fresh evidence and sample size are adequate.'
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
