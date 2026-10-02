from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score, mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
TRAIN=[
 ('recent_execution_placement_p01.json','hft_native_action_sweep_p01_bothsides_v1.json'),
 ('recent_execution_placement_p02.json','hft_native_action_sweep_p02_bothsides_v1.json'),
 ('recent_execution_placement_p03.json','hft_native_action_sweep_p03a_bothsides_v1.json'),
 ('recent_execution_placement_p03b.json','hft_native_action_sweep_p03b_bothsides_v1.json'),
 ('recent_execution_placement_p04.json','hft_native_action_sweep_p04_bothsides_v1.json'),
]
EVAL=[
 ('freshA','hft_native_freshA10_collector_v0.json','hft_native_action_sweep_freshA10_bothsides_v1.json'),
 ('freshB','hft_native_freshB10_collector_v0.json','hft_native_action_sweep_freshB10_bothsides_v1.json'),
 ('freshC','hft_native_freshC5_collector_v0.json','hft_native_action_sweep_freshC5_bothsides_v1.json'),
]
FEATURES=['side_is_up','action_offset','action_price','current_bid','current_ask','current_spread_ticks','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s']

def finite(x):
    try:
        v=float(x); return v if math.isfinite(v) else np.nan
    except Exception:return np.nan

def post_floor_delta(state,side,price,shares):
    q=float(shares);p=float(price);net=float(state.get('combined_net') or 0.0);floor=float(state.get('worst_case_floor') or 0.0);best=float(state.get('best_case_pnl') or floor)
    if net>=0:pup,pdn=best,floor
    else:pup,pdn=floor,best
    if side=='UP':pup+=q*(1-p);pdn-=q*p
    else:pdn+=q*(1-p);pup-=q*p
    return min(pup,pdn)-floor

def load_rows(collector_name,sweep_name):
    c=json.load(open(BASE/collector_name,encoding='utf-8'));s=json.load(open(BASE/sweep_name,encoding='utf-8'))
    bykey={}
    for r in c['placementRows']:
        k=(int(r['market_id']),int(r['checkpoint_ms']))
        bykey.setdefault(k,r)
    out=[]
    for sr in s['rows']:
        k=(int(sr['marketId']),int(sr['checkpointMs']));st=bykey.get(k)
        if st is None:continue
        r2side=str(sr.get('r2OriginalSide') or st.get('side') or '').upper()
        for a in sr.get('actions',[]):
            if a.get('invalid'):continue
            side=str(a.get('side') or r2side).upper();bid=finite(sr.get('upBid') if side=='UP' else sr.get('downBid'));ask=finite(sr.get('upAsk') if side=='UP' else sr.get('downAsk'))
            row={f:finite(st.get(f)) for f in FEATURES if f not in {'side_is_up','action_offset','action_price','current_bid','current_ask','current_spread_ticks'}}
            row.update({'market_id':k[0],'checkpoint_ms':k[1],'r2_side':r2side,'side':side,'side_is_up':float(side=='UP'),'action_offset':float(a['offset']),'action_price':float(a['price']),'current_bid':bid,'current_ask':ask,'current_spread_ticks':(ask-bid)/.01 if math.isfinite(bid) and math.isfinite(ask) else np.nan})
            filled=float(a.get('filledShares5s') or 0.0);fp=finite(a.get('fillPrice'));mtm=float(a.get('mtm1sUsdt') or 0.0);px=float(fp) if math.isfinite(fp) else float(a['price'])
            row['filled']=float(filled>1e-9);row['filled_shares']=filled;row['mtm']=mtm;row['delta_floor_realized']=post_floor_delta(st,side,px,filled) if filled>1e-9 else 0.0;row['delta_floor_full']=post_floor_delta(st,side,float(a['price']),18.0);row['portfolio_reward']=mtm+row['delta_floor_realized'];out.append(row)
    return pd.DataFrame(out)

def X(df):return df[FEATURES].replace([np.inf,-np.inf],np.nan)
def reg():return HistGradientBoostingRegressor(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=5.0,min_samples_leaf=12,random_state=20260822)
def clf():return HistGradientBoostingClassifier(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=5.0,min_samples_leaf=12,random_state=20260822)

def evaluate(name,d,fill,mark):
    z=d.copy();xx=X(z);pf=fill.predict_proba(xx)[:,1];pm=mark.predict(xx);z['pfill']=pf;z['pred_mark']=pm;z['score']=pf*(pm+z['delta_floor_full'])
    rec={'name':name,'actionRows':len(z),'fillRate':float(z.filled.mean())}
    try:rec['fillAuc']=float(roc_auc_score(z.filled,pf))
    except:rec['fillAuc']=None
    if (z.filled>0).any():rec['markoutMaeFilled']=float(mean_absolute_error(z.loc[z.filled>0,'mtm'],z.loc[z.filled>0,'pred_mark']))
    rows=[]
    for (mid,ts),g in z.groupby(['market_id','checkpoint_ms']):
        oracle=max(0.0,float(g.portfolio_reward.max()));r2=str(g.iloc[0].r2_side)
        gr=g[g.side==r2]
        def choose(gg):
            if gg.empty:return ('WAIT',0.0,None)
            ix=gg.score.idxmax();sc=float(gg.loc[ix,'score'])
            return ((f"{gg.loc[ix,'side']}_{int(gg.loc[ix,'action_offset'])}" if sc>0 else 'WAIT'),float(gg.loc[ix,'portfolio_reward']) if sc>0 else 0.0,sc)
        ar,rr,sr=choose(gr);af,rf,sf=choose(g)
        rows.append({'marketId':int(mid),'checkpointMs':int(ts),'r2Side':r2,'oracleReward':oracle,'restrictedAction':ar,'restrictedReward':rr,'freeAction':af,'freeReward':rf,'freeChoseOppositeR2':bool(af!='WAIT' and not af.startswith(r2+'_'))})
    rec['checkpoints']=len(rows);rec['oracleReward']=float(sum(x['oracleReward'] for x in rows));rec['restrictedReward']=float(sum(x['restrictedReward'] for x in rows));rec['freeReward']=float(sum(x['freeReward'] for x in rows));rec['restrictedActs']=sum(x['restrictedAction']!='WAIT' for x in rows);rec['freeActs']=sum(x['freeAction']!='WAIT' for x in rows);rec['freeOppositeR2Acts']=sum(x['freeChoseOppositeR2'] for x in rows);rec['rows']=rows;return rec

def main():
    tr=pd.concat([load_rows(c,s) for c,s in TRAIN],ignore_index=True);fill=clf();fill.fit(X(tr),tr.filled);mf=tr[tr.filled>0].copy();mark=reg();mark.fit(X(mf),mf.mtm)
    ev={name:evaluate(name,load_rows(c,s),fill,mark) for name,c,s in EVAL}
    comb={'checkpoints':sum(x['checkpoints'] for x in ev.values()),'oracleReward':sum(x['oracleReward'] for x in ev.values()),'restrictedReward':sum(x['restrictedReward'] for x in ev.values()),'freeReward':sum(x['freeReward'] for x in ev.values()),'restrictedActs':sum(x['restrictedActs'] for x in ev.values()),'freeActs':sum(x['freeActs'] for x in ev.values()),'freeOppositeR2Acts':sum(x['freeOppositeR2Acts'] for x in ev.values())}
    rep={'version':'HFT_NATIVE_VALUE_SURFACE_V2_FULL_SIDE','researchOnly':True,'dreamFillAllowed':False,'winnerSettlementRuntimeInput':False,'trainingActionRows':len(tr),'trainingFilledRows':int(tr.filled.sum()),'features':FEATURES,'score':'P(fill5s) * (E[1s MTM|fill] + deterministic delta worst-case-floor for 18-share fill)','noTuning':'Same native-unit portfolio score as V1; no new weights, threshold sweep, or PnL rescue. WAIT=0. Compare free side vs R2-side restricted with identical model.','holdouts':ev,'combined':comb}
    out=BASE/'hft_native_value_surface_v2_full_side_report.json';out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'trainRows':len(tr),'filled':int(tr.filled.sum()),'holdouts':{k:{kk:vv for kk,vv in v.items() if kk!='rows'} for k,v in ev.items()},'combined':comb},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
