from __future__ import annotations
import json, math, sys
from bisect import bisect_right
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score, mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
TRAIN=[(f'recent_execution_placement_p0{i}.json',f'hft_native_action_sweep_p0{i}_first10_v0.json') for i in range(1,5)]
EVAL=[('freshA','hft_native_freshA10_collector_v0.json','hft_native_action_sweep_freshA10_v0.json'),('freshB','hft_native_freshB10_collector_v0.json','hft_native_action_sweep_freshB10_v0.json'),('freshC','hft_native_freshC5_collector_v0.json','hft_native_action_sweep_freshC5_v0.json')]
BASE_FEATURES=['side_is_up','action_offset','action_price','current_bid','current_ask','current_spread_ticks','active_opp_count','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s']
PUBLIC=['seconds_left','direction_toward_side','bias_toward_side','spot_strike_toward_side','chainlink_strike_toward_side','spot_ret1_toward_side','spot_ret3_toward_side','futures_ret1_toward_side','futures_ret3_toward_side','spot_queue_toward_side','futures_queue_toward_side','spot_taker1_toward_side','futures_taker1_toward_side','perp_spot_basis_signed']
FEATURES=BASE_FEATURES+PUBLIC

def finite(x):
    try:v=float(x);return v if math.isfinite(v) else np.nan
    except:return np.nan

def post_floor_delta(st,side,price,shares):
    q=float(shares);p=float(price);net=float(st.get('combined_net') or 0);floor=float(st.get('worst_case_floor') or 0);best=float(st.get('best_case_pnl') or floor)
    pup,pdn=(best,floor) if net>=0 else (floor,best)
    if side=='UP':pup+=q*(1-p);pdn-=q*p
    else:pdn+=q*(1-p);pup-=q*p
    return min(pup,pdn)-floor

def pub_lookup(mid):
    a=sorted(load_public_snapshots(mid),key=lambda z:int(z.get('sampledAtMs') or 0));ts=[int(z.get('sampledAtMs') or 0) for z in a]
    return lambda t: a[bisect_right(ts,int(t))-1] if bisect_right(ts,int(t))>0 else {}

def pubfeat(s,side):
    sg=1.0 if side=='UP' else -1.0;bias=str(s.get('directionBias') or 'NEUTRAL').upper();b=1.0 if bias==side else -1.0 if bias in {'UP','DOWN'} else 0.0
    return {'seconds_left':finite(s.get('secondsLeft')),'direction_toward_side':finite(s.get('directionScore'))*sg,'bias_toward_side':b,'spot_strike_toward_side':finite(s.get('spotMinusStrikeBps'))*sg,'chainlink_strike_toward_side':finite(s.get('chainlinkMinusStrikeBps'))*sg,'spot_ret1_toward_side':finite(s.get('spotReturn1sBps'))*sg,'spot_ret3_toward_side':finite(s.get('spotReturn3sBps'))*sg,'futures_ret1_toward_side':finite(s.get('futuresReturn1sBps'))*sg,'futures_ret3_toward_side':finite(s.get('futuresReturn3sBps'))*sg,'spot_queue_toward_side':finite(s.get('spotQueueImbalance'))*sg,'futures_queue_toward_side':finite(s.get('futuresQueueImbalance'))*sg,'spot_taker1_toward_side':finite(s.get('spotTakerImbalance1s'))*sg,'futures_taker1_toward_side':finite(s.get('futuresTakerImbalance1s'))*sg,'perp_spot_basis_signed':finite(s.get('perpSpotBasisBps'))*sg}

def load_rows(cn,sn):
    c=json.load(open(BASE/cn,encoding='utf-8'));s=json.load(open(BASE/sn,encoding='utf-8'));pm={(int(r['market_id']),int(r['checkpoint_ms']),str(r['side'])):r for r in c['placementRows']};pl={};rows=[]
    for sr in s['rows']:
        mid=int(sr['marketId']);t=int(sr['checkpointMs']);side=str(sr['side']).upper();st=pm.get((mid,t,side))
        if st is None:continue
        if mid not in pl:pl[mid]=pub_lookup(mid)
        snap=pl[mid](t)
        for a in sr.get('actions',[]):
            if a.get('invalid'):continue
            r={k:finite(st.get(k)) for k in BASE_FEATURES if k not in {'action_offset','action_price'}};r.update({'market_id':mid,'checkpoint_ms':t,'side':side,'action_offset':float(a['offset']),'action_price':float(a['price'])});r.update(pubfeat(snap,side))
            filled=float(a.get('filledShares5s') or 0);fp=finite(a.get('fillPrice'));mtm=float(a.get('mtm1sUsdt') or 0);px=float(fp) if math.isfinite(fp) else float(a['price']);r['filled']=float(filled>1e-9);r['mtm']=mtm;r['delta_floor_realized']=post_floor_delta(st,side,px,filled) if filled>1e-9 else 0.0;r['delta_floor_full']=post_floor_delta(st,side,float(a['price']),18.0);r['portfolio_reward']=mtm+r['delta_floor_realized'];rows.append(r)
    return pd.DataFrame(rows)
def X(d):return d[FEATURES].replace([np.inf,-np.inf],np.nan)
def clf():return HistGradientBoostingClassifier(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=5.0,min_samples_leaf=12,random_state=20260822)
def reg():return HistGradientBoostingRegressor(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=5.0,min_samples_leaf=12,random_state=20260822)

def evaluate(name,d,fm,mm):
    z=d.copy();pf=fm.predict_proba(X(z))[:,1];pm=mm.predict(X(z));z['pfill']=pf;z['pred_mark']=pm;z['score']=pf*(pm+z['delta_floor_full']);out={'name':name,'actionRows':len(z),'fillRate':float(z.filled.mean())}
    try:out['fillAuc']=float(roc_auc_score(z.filled,pf))
    except:out['fillAuc']=None
    if (z.filled>0).any():out['markoutMaeFilled']=float(mean_absolute_error(z.loc[z.filled>0,'mtm'],z.loc[z.filled>0,'pred_mark']))
    rows=[]
    for (mid,t,side),g in z.groupby(['market_id','checkpoint_ms','side']):
        ix=g.score.idxmax();sc=float(g.loc[ix,'score']);act=int(g.loc[ix,'action_offset']) if sc>0 else 'WAIT';rew=float(g.loc[ix,'portfolio_reward']) if sc>0 else 0.0;rows.append({'marketId':int(mid),'checkpointMs':int(t),'side':side,'oracleReward':max(0.0,float(g.portfolio_reward.max())),'action':act,'reward':rew})
    out['checkpoints']=len(rows);out['oracleReward']=sum(r['oracleReward'] for r in rows);out['policyReward']=sum(r['reward'] for r in rows);out['acts']=sum(r['action']!='WAIT' for r in rows);out['rows']=rows;return out

def main():
    tr=pd.concat([load_rows(c,s) for c,s in TRAIN],ignore_index=True);fm=clf();fm.fit(X(tr),tr.filled);mf=tr[tr.filled>0];mm=reg();mm.fit(X(mf),mf.mtm);ev={n:evaluate(n,load_rows(c,s),fm,mm) for n,c,s in EVAL};comb={'checkpoints':sum(e['checkpoints'] for e in ev.values()),'oracleReward':sum(e['oracleReward'] for e in ev.values()),'policyReward':sum(e['policyReward'] for e in ev.values()),'acts':sum(e['acts'] for e in ev.values())};rep={'version':'HFT_NATIVE_VALUE_SURFACE_V4_R2SIDE_PUBLIC_PRIOR','researchOnly':True,'dreamFillAllowed':False,'targetRuntimeInput':False,'winnerSettlementRuntimeInput':False,'trainingActionRows':len(tr),'trainingFilledRows':int(tr.filled.sum()),'score':'P(fill5s)*(E[1s MTM|fill]+delta worst-case-floor)','noTuning':'Exact V1 score/model family/hyperparameters; only strict-past public directional priors added; WAIT=0; no threshold/reward sweep.','holdouts':ev,'combined':comb};out=BASE/'hft_native_value_surface_v4_r2side_public_prior_report.json';out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'trainRows':len(tr),'filled':int(tr.filled.sum()),'holdouts':{k:{kk:vv for kk,vv in v.items() if kk!='rows'} for k,v in ev.items()},'combined':comb},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
