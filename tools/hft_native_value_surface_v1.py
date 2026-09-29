from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score, mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
TRAIN=[(f'recent_execution_placement_p0{i}.json',f'hft_native_action_sweep_p0{i}_first10_v0.json') for i in range(1,5)]
EVAL=[('freshA','hft_native_freshA10_collector_v0.json','hft_native_action_sweep_freshA10_v0.json'),('freshB','hft_native_freshB10_collector_v0.json','hft_native_action_sweep_freshB10_v0.json'),('freshC','hft_native_freshC5_collector_v0.json','hft_native_action_sweep_freshC5_v0.json')]
FEATURES=['side_is_up','action_offset','action_price','current_bid','current_ask','current_spread_ticks','active_opp_count','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s']

def finite(x):
    try:
        v=float(x); return v if math.isfinite(v) else np.nan
    except Exception:return np.nan

def post_floor_delta(state,side,price,shares):
    q=float(shares); p=float(price); net=float(state.get('combined_net') or 0.0)
    floor=float(state.get('worst_case_floor') or 0.0); best=float(state.get('best_case_pnl') or floor)
    if net>=0: pnl_up,pnl_down=best,floor
    else: pnl_up,pnl_down=floor,best
    if side=='UP': pnl_up += q*(1-p); pnl_down -= q*p
    else: pnl_down += q*(1-p); pnl_up -= q*p
    return min(pnl_up,pnl_down)-floor

def load_rows(collector_name,sweep_name):
    c=json.load(open(BASE/collector_name,encoding='utf-8')); s=json.load(open(BASE/sweep_name,encoding='utf-8'))
    pm={(int(r['market_id']),int(r['checkpoint_ms']),str(r['side'])):r for r in c['placementRows']}
    rows=[]
    for sr in s['rows']:
        key=(int(sr['marketId']),int(sr['checkpointMs']),str(sr['side'])); st=pm.get(key)
        if st is None: continue
        for a in sr.get('actions',[]):
            if a.get('invalid'): continue
            r={k:finite(st.get(k)) for k in FEATURES if k not in {'action_offset','action_price'}}
            r.update({'market_id':key[0],'checkpoint_ms':key[1],'side':key[2],'action_offset':float(a['offset']),'action_price':float(a['price'])})
            filled=float(a.get('filledShares5s') or 0.0); fill_px=finite(a.get('fillPrice')); mtm=float(a.get('mtm1sUsdt') or 0.0)
            r['filled']=float(filled>1e-9); r['filled_shares']=filled; r['mtm']=mtm
            px=float(fill_px) if math.isfinite(fill_px) else float(a['price'])
            r['delta_floor_realized']=post_floor_delta(st,key[2],px,filled) if filled>1e-9 else 0.0
            r['delta_floor_full']=post_floor_delta(st,key[2],float(a['price']),18.0)
            r['portfolio_reward']=mtm+r['delta_floor_realized']
            rows.append(r)
    return pd.DataFrame(rows),s

def X(df): return df[FEATURES].replace([np.inf,-np.inf],np.nan)

def make_reg(): return HistGradientBoostingRegressor(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=5.0,min_samples_leaf=12,random_state=20260822)
def make_clf(): return HistGradientBoostingClassifier(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=5.0,min_samples_leaf=12,random_state=20260822)

def evaluate(name,df,direct,fill_model,mark_model):
    z=df.copy(); xx=X(z)
    z['pred_A']=direct.predict(xx)
    pf=fill_model.predict_proba(xx)[:,1]; z['pfill']=pf
    z['pred_mark']=mark_model.predict(xx)
    z['pred_B']=pf*z['pred_mark']
    z['pred_C']=pf*(z['pred_mark']+z['delta_floor_full'])
    out={'name':name,'actionRows':len(z),'fillRate':float(z.filled.mean())}
    try: out['fillAuc']=float(roc_auc_score(z.filled,pf))
    except Exception: out['fillAuc']=None
    if (z.filled>0).any(): out['markoutMaeFilled']=float(mean_absolute_error(z.loc[z.filled>0,'mtm'],z.loc[z.filled>0,'pred_mark']))
    groups=[]
    for (mid,ts,side),g in z.groupby(['market_id','checkpoint_ms','side']):
        rec={'marketId':int(mid),'checkpointMs':int(ts),'side':side}
        oracle=max(0.0,float(g.portfolio_reward.max())); rec['oracleReward']=oracle
        for tag in ['A','B','C']:
            col='pred_'+tag; idx=g[col].idxmax(); pred=float(g.loc[idx,col])
            if pred>0:
                rec[f'action_{tag}']=int(g.loc[idx,'action_offset']); rec[f'reward_{tag}']=float(g.loc[idx,'portfolio_reward'])
            else:
                rec[f'action_{tag}']='WAIT'; rec[f'reward_{tag}']=0.0
        for off in [0,1,2]:
            gg=g[g.action_offset==off]; rec[f'fixed_{off}']=float(gg.iloc[0].portfolio_reward) if len(gg) else 0.0
        groups.append(rec)
    out['checkpoints']=len(groups); out['oracleReward']=float(sum(r['oracleReward'] for r in groups))
    for tag in ['A','B','C']:
        out[f'policyReward_{tag}']=float(sum(r[f'reward_{tag}'] for r in groups)); out[f'acts_{tag}']=sum(r[f'action_{tag}']!='WAIT' for r in groups)
    out['fixed']={str(off):float(sum(r[f'fixed_{off}'] for r in groups)) for off in [0,1,2]}; out['rows']=groups
    return out

def main():
    train=[]
    for c,s in TRAIN:
        d,_=load_rows(c,s); train.append(d)
    tr=pd.concat(train,ignore_index=True)
    direct=make_reg(); direct.fit(X(tr),tr['mtm'])
    fill=make_clf(); fill.fit(X(tr),tr['filled'])
    mf=tr[tr.filled>0].copy(); mark=make_reg(); mark.fit(X(mf),mf['mtm'])
    evals={}
    for name,c,s in EVAL:
        d,_=load_rows(c,s); evals[name]=evaluate(name,d,direct,fill,mark)
    combined={k:0.0 for k in ['oracleReward','policyReward_A','policyReward_B','policyReward_C']}
    combined['checkpoints']=0
    for e in evals.values():
        combined['checkpoints']+=e['checkpoints']
        for k in ['oracleReward','policyReward_A','policyReward_B','policyReward_C']: combined[k]+=e[k]
    combined['fixed']={str(o):sum(e['fixed'][str(o)] for e in evals.values()) for o in [0,1,2]}
    rep={'version':'HFT_NATIVE_VALUE_SURFACE_V1','researchOnly':True,'dreamFillAllowed':False,'winnerSettlementRuntimeInput':False,'trainingMarkets':40,'trainingActionRows':len(tr),'trainingFilledRows':int(tr.filled.sum()),'features':FEATURES,'architectures':{'A':'direct E[1s MTM, no-fill=0]','B':'P(fill5s)*E[1s MTM|fill]','C':'P(fill5s)*(E[1s MTM|fill]+deterministic delta worst-case-floor if full 18-share fill)'},'noTuning':'No reward-weight or PnL threshold sweep; WAIT value fixed at 0; C combines USDT components 1:1 in native units.','holdouts':evals,'combined':combined}
    out=BASE/'hft_native_value_surface_v1_report.json';out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(out),'trainingRows':len(tr),'filled':int(tr.filled.sum()),'freshA':{k:v for k,v in evals['freshA'].items() if k not in {'rows'}},'freshB':{k:v for k,v in evals['freshB'].items() if k not in {'rows'}},'combined':combined},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
