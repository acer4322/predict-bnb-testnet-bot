from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np
import pandas as pd

SEED=20260907
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
ACTIONS=BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv'
CHECKPOINTS=BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv'
BRIDGE=BASE/'35_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_RETROSPECTIVE_V1.csv'


def side_sign(x):
    return 1.0 if x=='UP' else (-1.0 if x=='DOWN' else np.nan)

def safe_log1p(x):
    x=np.asarray(x,float); return np.sign(x)*np.log1p(np.abs(x))

def prepare(train,test):
    a=np.asarray(train,float); b=np.asarray(test,float)
    keep=np.isfinite(a).any(axis=0); a=a[:,keep]; b=b[:,keep]
    am=~np.isfinite(a); bm=~np.isfinite(b)
    med=np.nanmedian(np.where(am,np.nan,a),axis=0)
    a=np.where(am,med,a); b=np.where(bm,med,b)
    mu=a.mean(0); sd=a.std(0); sd[sd<1e-8]=1
    masks=am.any(0)
    return np.c_[np.ones(len(a)),np.clip((a-mu)/sd,-8,8),am[:,masks]], np.c_[np.ones(len(b)),np.clip((b-mu)/sd,-8,8),bm[:,masks]]

def fit_logit(x,y,penalty=10.0):
    y=np.asarray(y,float); beta=np.zeros(x.shape[1]); rate=np.clip(y.mean(),1e-5,1-1e-5); beta[0]=np.log(rate/(1-rate))
    reg=np.full(x.shape[1],penalty); reg[0]=0
    def obj(w):
        z=x@w; return np.logaddexp(0,z).sum()-y@z+.5*np.sum(reg*w*w)
    prev=obj(beta)
    for _ in range(40):
        p=1/(1+np.exp(-np.clip(x@beta,-35,35))); g=x.T@(p-y)+reg*beta
        h=x.T@(x*(p*(1-p))[:,None])+np.diag(reg+1e-9)
        step=np.linalg.solve(h,g); scale=1.0
        while obj(beta-scale*step)>prev+1e-8 and scale>1e-6: scale*=.5
        beta-=scale*step; now=obj(beta)
        if abs(prev-now)<1e-7: break
        prev=now
    return beta

def metrics(y,p):
    y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
    if len(y)==0: return {'rows':0}
    pos=y.sum(); neg=len(y)-pos; ranks=pd.Series(p).rank(method='average').to_numpy()
    auc=(ranks[y==1].sum()-pos*(pos+1)/2)/(pos*neg) if pos and neg else None
    ll=float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()); br=float(np.mean((p-y)**2))
    return {'rows':int(len(y)),'eventRate':float(y.mean()),'auc':None if auc is None else float(auc),'logLoss':ll,'brier':br}

def carrier_for_action(r,entry_ms,bridge_map):
    if r.birth_qty<=1e-9 or r.birth_side not in ('UP','DOWN'): return None
    legs=bridge_map.get((int(r.market_id),int(r.event_ms),r.birth_side))
    maker=float(r.maker_up_qty if r.birth_side=='UP' else r.maker_down_qty)
    taker=float(r.taker_up_qty if r.birth_side=='UP' else r.taker_down_qty)
    pre=post=unk=0.0; prelegs=postlegs=unklegs=0
    if legs is not None:
        for v in legs.itertuples():
            good=(v.parent_found==1 and v.parent_placement_coverage>=.85 and v.parent_fill_allocation_coverage>=.70 and v.parent_target_side==r.birth_side and np.isfinite(v.parent_placement_first_ms) and v.parent_placement_first_ms<=r.event_ms)
            if not good:
                unk+=float(v.shares); unklegs+=1
            elif v.parent_placement_first_ms < entry_ms:
                pre+=float(v.shares); prelegs+=1
            else:
                post+=float(v.shares); postlegs+=1
        covered=pre+post+unk
        if covered<maker-1e-6: unk+=maker-covered
    else:
        unk=maker
    birth=float(r.birth_qty); total=maker+taker
    # Conservative lower bound on birth that cannot be explained by PRE or unresolved Maker input.
    new_lb=max(0.0,birth-pre-unk)
    new_ub=min(birth,post+taker)
    return {'preMaker':pre,'postMaker':post,'unknownMaker':unk,'taker':taker,'newBirthLower':new_lb,'newBirthUpper':new_ub,
            'preLegs':prelegs,'postLegs':postlegs,'unknownLegs':unklegs,
            'hqPostOnlyMaker':bool(r.route_mix=='MAKER_ONLY' and post>0 and pre+unk<1e-9),
            'takerOrMixed':bool(r.route_mix in ('TAKER_ONLY','MIXED'))}

def build_entries(actions,cp):
    # Correct only the eligibility needed here; age is reconstructed separately below.
    action_times={int(mid):set(z.event_ms.astype(np.int64)) for mid,z in actions.groupby('market_id')}
    resets={int(mid):z[z.economic_role.eq('COMPOSITE_CROSSING')].event_ms.to_numpy(np.int64) for mid,z in actions.groupby('market_id')}
    rows=[]
    for mid,z in cp.sort_values(['market_id','checkpoint_ms']).groupby('market_id',sort=False):
        mid=int(mid); prev_key=None; c2run=0; seen=False
        evm=actions[actions.market_id==mid].event_ms
        reconstructed_end=float(np.nanmedian(z.checkpoint_ms.to_numpy(float)+1000*z.seconds_left.to_numpy(float)))
        observed_end=min(reconstructed_end,float(max(z.checkpoint_ms.max(),evm.max() if len(evm) else z.checkpoint_ms.max())))
        for r in z.itertuples():
            if r.checkpoint_ms>=observed_end:
                continue
            if int(r.checkpoint_ms) in action_times.get(mid,set()):
                # GPT6 q.usable removes this row before episode grouping; omission must not break a surrounding C1 run.
                continue
            if pd.notna(r.next_action_delay_ms) and r.next_action_delay_ms<=1:
                continue
            s=side_sign(r.prior_clean_expand_side)
            if not np.isfinite(s) or r.seconds_left<=0: prev_key=None; c2run=0; seen=False; continue
            predict_support=s*(r.predict_up_mid-r.predict_down_mid)/2
            strike_support=s*r.spot_minus_strike_bps if pd.notna(r.spot_minus_strike_bps) else np.nan
            c1=bool(predict_support<0)
            c2=bool(c1 and np.isfinite(strike_support) and strike_support<0)
            if not c1:
                prev_key=None; c2run=0; seen=False; continue
            gen=int(np.searchsorted(resets.get(mid,np.array([],dtype=np.int64)),int(r.checkpoint_ms),side='left'))
            key=(r.prior_clean_expand_side,gen)
            if key!=prev_key: seen=False; c2run=0
            if c2:
                c2run+=1
                if not seen:
                    net=float(r.pre_net_shares); aligned=(np.sign(net)==s and abs(net)>1e-9)
                    rows.append({'market_id':mid,'entry_ms':int(r.checkpoint_ms),'anchor':r.prior_clean_expand_side,
                                 'predict_support':float(predict_support),'strike_support':float(strike_support),'pre_net_shares':net,
                                 'net_aligned_prior':bool(aligned),'pre_outstanding_qty':float(r.pre_outstanding_qty),
                                 'pre_floor':float(r.pre_floor),'pre_best':float(r.pre_best),'pre_cost':float(r.pre_cost),'pre_gross_shares':float(r.pre_gross_shares),
                                 'last_economic_role':r.last_economic_role,'last_action_age_ms':float(r.last_action_age_ms) if pd.notna(r.last_action_age_ms) else np.nan,
                                 'seconds_left':float(r.seconds_left),'predict_up_ask':float(r.predict_up_ask) if pd.notna(r.predict_up_ask) else np.nan,
                                 'predict_down_ask':float(r.predict_down_ask) if pd.notna(r.predict_down_ask) else np.nan,
                                 'predict_up_mid':float(r.predict_up_mid),'predict_down_mid':float(r.predict_down_mid),
                                 'spot_queue_imbalance':float(r.spot_queue_imbalance) if pd.notna(r.spot_queue_imbalance) else np.nan,
                                 'futures_queue_imbalance':float(r.futures_queue_imbalance) if pd.notna(r.futures_queue_imbalance) else np.nan,
                                 'spot_taker_imbalance_1s':float(r.spot_taker_imbalance_1s) if pd.notna(r.spot_taker_imbalance_1s) else np.nan,
                                 'futures_taker_imbalance_1s':float(r.futures_taker_imbalance_1s) if pd.notna(r.futures_taker_imbalance_1s) else np.nan,
                                 'spot_return_1s_bps':float(r.spot_return_1s_bps) if pd.notna(r.spot_return_1s_bps) else np.nan,
                                 'futures_return_1s_bps':float(r.futures_return_1s_bps) if pd.notna(r.futures_return_1s_bps) else np.nan})
                    seen=True
            else:
                c2run=0
            prev_key=key
    return pd.DataFrame(rows)

def add_correct_clean_history(entries,actions):
    out=entries.copy(); ages=[]; runs=[]
    for r in out.itertuples():
        ev=actions[(actions.market_id==r.market_id)&(actions.event_ms<r.entry_ms)&actions.economic_role.eq('CLEAN_AGGREGATE_EXPAND')].sort_values('event_ms')
        if len(ev)==0: ages.append(np.nan); runs.append(0); continue
        ages.append(float(r.entry_ms-ev.iloc[-1].event_ms))
        sides=ev.birth_side.tolist(); last=sides[-1]; run=0
        for s in reversed(sides):
            if s==last: run+=1
            else: break
        runs.append(run)
    out['clean_age_ms']=ages; out['clean_run_length']=runs
    return out

def label_outcomes(entries,actions,bridge,horizon_ms=5000):
    bridge_map={k:v for k,v in bridge.groupby(['market_id','event_ms','side'],sort=False)}
    out=[]
    for e in entries.itertuples():
        fut=actions[(actions.market_id==e.market_id)&(actions.event_ms>e.entry_ms)&(actions.event_ms<=e.entry_ms+horizon_ms)].sort_values('event_ms')
        same_clean=fut[(fut.economic_role=='CLEAN_AGGREGATE_EXPAND')&(fut.birth_side==e.anchor)&(fut.birth_qty>1e-9)]
        same_birth=fut[(fut.birth_side==e.anchor)&(fut.birth_qty>1e-9)]
        clean_lb=clean_ub=all_lb=all_ub=0.0; postonly=taker=0
        for r in same_clean.itertuples():
            c=carrier_for_action(r,e.entry_ms,bridge_map); clean_lb+=c['newBirthLower']; clean_ub+=c['newBirthUpper']; postonly+=int(c['hqPostOnlyMaker']); taker+=int(c['takerOrMixed'])
        for r in same_birth.itertuples():
            c=carrier_for_action(r,e.entry_ms,bridge_map); all_lb+=c['newBirthLower']; all_ub+=c['newBirthUpper']
        x=e._asdict(); x.update({'same_clean_count_5s':int(len(same_clean)),'same_birth_count_5s':int(len(same_birth)),
                                'renewed_clean_birth_lower_5s':float(clean_lb),'renewed_clean_birth_upper_5s':float(clean_ub),
                                'renewed_any_birth_lower_5s':float(all_lb),'renewed_any_birth_upper_5s':float(all_ub),
                                'renewed_clean_lb_positive':int(clean_lb>1e-9),'renewed_any_lb_positive':int(all_lb>1e-9),
                                'hq_postonly_clean_actions':int(postonly),'taker_or_mixed_clean_actions':int(taker)})
        out.append(x)
    return pd.DataFrame(out)

def model_table(d):
    if len(d)<30 or d.market_id.nunique()<6: return {'status':'NOT_ENOUGH_ROWS','rows':len(d),'markets':d.market_id.nunique()}
    starts=d.groupby('market_id').entry_ms.min().sort_values(); mids=starts.index.tolist(); n=len(mids)
    ntr=max(1,int(n*.6)); nval=max(1,int(n*.2)); smap={m:'TRAIN' for m in mids[:ntr]}; smap.update({m:'VALIDATION' for m in mids[ntr:ntr+nval]}); smap.update({m:'TEST' for m in mids[ntr+nval:]}); d=d.copy(); d['split']=d.market_id.map(smap)
    s=d.anchor.map({'UP':1.0,'DOWN':-1.0})
    f=pd.DataFrame(index=d.index)
    f['phase']=d.seconds_left/300; f['opposition_predict']=-d.predict_support; f['opposition_strike']=-d.strike_support
    f['oriented_spot_queue']=s*d.spot_queue_imbalance; f['oriented_futures_queue']=s*d.futures_queue_imbalance
    f['oriented_spot_taker1s']=s*d.spot_taker_imbalance_1s; f['oriented_futures_taker1s']=s*d.futures_taker_imbalance_1s
    f['oriented_spot_ret1s']=s*d.spot_return_1s_bps; f['oriented_fut_ret1s']=s*d.futures_return_1s_bps
    market=list(f.columns)
    f['log_net_abs']=np.log1p(d.pre_net_shares.abs()); f['log_debt']=np.log1p(d.pre_outstanding_qty.clip(lower=0)); f['floor']=safe_log1p(d.pre_floor); f['best']=safe_log1p(d.pre_best)
    f['cost_per_gross']=d.pre_cost/d.pre_gross_shares.replace(0,np.nan); f['last_action_age']=np.log1p(d.last_action_age_ms)
    for role in ['CLEAN_AGGREGATE_EXPAND','REPAIR_ONLY','COMPOSITE_CROSSING']:
        f['last_'+role]=d.last_economic_role.eq(role).astype(float)
    portfolio=[c for c in f.columns if c not in market]
    f['prior_side_ask']=np.where(s==1,d.predict_up_ask,d.predict_down_ask); f['repair_ask']=np.where(s==1,d.predict_down_ask,d.predict_up_ask)
    f['pair_ask_surplus']=1-d.predict_up_ask-d.predict_down_ask; f['repair_price_x_debt']=f.repair_ask*np.log1p(d.pre_outstanding_qty.clip(lower=0))
    economics=['prior_side_ask','repair_ask','pair_ask_surplus','repair_price_x_debt','cost_per_gross']
    f['clean_age']=np.log1p(d.clean_age_ms); f['clean_run']=np.log1p(d.clean_run_length)
    history=['clean_age','clean_run']
    groups={'M0_MARKET':market,'M1_MARKET_PORTFOLIO':market+portfolio,'M2_PLUS_ECONOMICS':market+portfolio+economics,'M3_PLUS_HISTORY':market+portfolio+economics+history}
    y=d.renewed_clean_lb_positive.to_numpy(int); results={}
    tr=d.split.eq('TRAIN').to_numpy()
    for name,cols in groups.items():
        results[name]={}
        for sp in ['VALIDATION','TEST']:
            te=d.split.eq(sp).to_numpy(); x,w=prepare(f.loc[tr,cols],f.loc[te,cols]); beta=fit_logit(x,y[tr]); p=1/(1+np.exp(-np.clip(w@beta,-35,35))); results[name][sp]=metrics(y[te],p)
    return {'status':'OK','splits':{k:[int(x) for x in d[d.split==k].market_id.unique()] for k in ['TRAIN','VALIDATION','TEST']},'features':groups,'models':results}

def summarize(d):
    aligned=d[d.net_aligned_prior].copy();
    def desc(x):
        x=pd.Series(x).dropna(); return {'n':int(len(x)),'mean':float(x.mean()) if len(x) else None,'median':float(x.median()) if len(x) else None}
    pos=aligned[aligned.renewed_clean_lb_positive==1]; neg=aligned[aligned.renewed_clean_lb_positive==0]
    features=['predict_support','strike_support','pre_net_shares','pre_outstanding_qty','pre_floor','pre_best','last_action_age_ms','clean_age_ms','clean_run_length']
    comps={f:{'positive':desc(pos[f]),'negative':desc(neg[f])} for f in features}
    return {'allC2Entries':int(len(d)),'alignedEntries':int(len(aligned)),'alignedMarkets':int(aligned.market_id.nunique()),
            'renewedCleanLowerPositive':int(aligned.renewed_clean_lb_positive.sum()),'renewedCleanLowerRate':float(aligned.renewed_clean_lb_positive.mean()) if len(aligned) else None,
            'renewedAnyBirthLowerPositive':int(aligned.renewed_any_lb_positive.sum()),'renewedAnyBirthLowerRate':float(aligned.renewed_any_lb_positive.mean()) if len(aligned) else None,
            'renewedCleanLowerQty':float(aligned.renewed_clean_birth_lower_5s.sum()),'renewedCleanUpperQty':float(aligned.renewed_clean_birth_upper_5s.sum()),
            'hqPostOnlyCleanActions':int(aligned.hq_postonly_clean_actions.sum()),'takerOrMixedCleanActions':int(aligned.taker_or_mixed_clean_actions.sum()),
            'featureComparison':comps}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--markets',nargs='*',type=int); ap.add_argument('--output',required=True); ap.add_argument('--csv'); a=ap.parse_args()
    actions=pd.read_csv(ACTIONS,low_memory=False); cp=pd.read_csv(CHECKPOINTS,low_memory=False); bridge=pd.read_csv(BRIDGE,low_memory=False)
    if a.markets:
        m=set(a.markets); actions=actions[actions.market_id.isin(m)].copy(); cp=cp[cp.market_id.isin(m)].copy(); bridge=bridge[bridge.market_id.isin(m)].copy()
    entries=build_entries(actions,cp); entries=add_correct_clean_history(entries,actions); lab=label_outcomes(entries,actions,bridge)
    summary=summarize(lab); model=model_table(lab[lab.net_aligned_prior].copy())
    report={'version':'OUR_C2_ALIGNED_RENEWED_RISK_FALSIFICATION_V1','researchOnly':True,'horizonSeconds':5,'marketsRequested':a.markets,'summary':summary,'model':model,
            'interpretationGuards':['Outcome is conservative lower-bound post-conflict/Taker same-side CLEAN birth, not private thesis.','C2 requires Predict and spot-strike opposition to prior clean side.','Primary analysis conditions current net still aligned with prior clean side to avoid mechanical post-crossing reversal.','Retrospective parent timing is diagnostic; unresolved timing is excluded from the lower-bound renewed-birth label.','Models are predictive diagnostics only; no runtime authority or economic-value claim.']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if a.csv: Path(a.csv).parent.mkdir(parents=True,exist_ok=True); lab.to_csv(a.csv,index=False)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
