from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo
import datetime as dt

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT = Path(__file__).resolve().parents[1]
FORMATION = ROOT / 'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
FULL300 = ROOT / 'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
JOINT = ROOT / 'data/research/r4_v0/hourly/r4_joint_base_upside_manager_v1_rows.csv'
OFF = ROOT / 'data/target_wallet_official_v1.db'
LIFE = ROOT / 'data/wallet_maker_book_inference.db'
PREREG = ROOT / 'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_preregistered_v1.json'
OUT = ROOT / 'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_v1.json'
ROWS = ROOT / 'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_rows_v1.csv'
EPS = 1e-9
H5 = 5000
H15 = 15000

GEOM = ['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_floor','pre_upside','pre_absNet','pre_coverage','pre_floor_per_gross']
GENMEM = ['seconds_since_prev_parent','events_5s_strict','events_15s_strict','objective_transitions_15s','last_objective_age_s']
ROLEMEM = ['weak_events_15s_strict','state_events_15s_strict','distinct_objective_keys_15s']
OWNER = ['active_weak_roots','active_surplus_roots','unresolved_weak_shares','unresolved_surplus_shares','recent_weak_fill_5s','recent_surplus_fill_5s','lifecycle_market_observed']
PAIRPRIOR = ['pair_prior_age_s','pair_prior_is_pair_balance','pair_prior_is_state_shaping','pair_prior_side_is_current_weak','pair_prior_side_is_current_surplus']
CAND = ['candidate_side_is_weak','candidate_side_is_surplus','candidate_price']


def qdf(db: Path, sql: str, params=()):
    c = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)
    c.execute('pragma query_only=on')
    x = pd.read_sql_query(sql, c, params=params)
    c.close()
    return x


def fee(sh, px):
    return float(sh) * float(px) * (1 - float(px)) * 0.02 * 4.0


def binmet(y, p):
    y = np.asarray(y, int); p = np.asarray(p, float)
    o = {'n': int(len(y)), 'rate': float(y.mean()) if len(y) else None, 'meanProbability': float(p.mean()) if len(p) else None}
    if len(y) and len(np.unique(y)) > 1:
        o.update({'auc': float(roc_auc_score(y,p)), 'ap': float(average_precision_score(y,p)), 'logLoss': float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))})
    else:
        o.update({'auc': None, 'ap': None, 'logLoss': None})
    return o


def hgb(seed):
    return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=220,random_state=seed)


def summarize_blocks(blocks, name):
    xs = [b[name] for b in blocks if b.get(name,{}).get('auc') is not None]
    if not xs: return {'blocks':0}
    return {'blocks':len(xs),'meanAuc':float(np.mean([x['auc'] for x in xs])),'worstAuc':float(np.min([x['auc'] for x in xs])),'meanAp':float(np.mean([x['ap'] for x in xs])),'meanLogLoss':float(np.mean([x['logLoss'] for x in xs])),'blockAucs':[float(x['auc']) for x in xs]}


def chronological_binary(df, label, feature_sets, seed0=31000):
    x = df.dropna(subset=[label]).copy()
    x[label] = x[label].astype(int)
    if x.empty or x[label].nunique() < 2: return {'coverage':{'rows':int(len(x)),'markets':int(x.market_id.nunique())},'blocks':[],'summary':{}}
    order = x.groupby('market_id').first_event_ms.min().sort_values().index.astype(int).tolist()
    initial = max(45, int(len(order)*.60))
    initial = min(initial, max(1,len(order)-4))
    rem = len(order)-initial
    sizes = [rem//4]*4
    for i in range(rem%4): sizes[i] += 1
    cur=initial; blocks=[]
    for bi,sz in enumerate(sizes,1):
        if sz <= 0: continue
        trm=set(order[:cur]); tem=set(order[cur:cur+sz]); cur += sz
        tr=x[x.market_id.isin(trm)]; te=x[x.market_id.isin(tem)]
        b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':int(len(tr)),'testRows':int(len(te)),'testRate':float(te[label].mean()) if len(te) else None}
        for j,(name,feats) in enumerate(feature_sets.items()):
            use=[f for f in feats if f in x.columns]
            a=tr.dropna(subset=use+[label]); z=te.dropna(subset=use+[label])
            if len(a)<50 or len(z)<10 or a[label].nunique()<2 or z[label].nunique()<2:
                b[name]={'n':int(len(z)),'rate':float(z[label].mean()) if len(z) else None,'auc':None,'ap':None,'logLoss':None}; continue
            m=hgb(seed0+bi*20+j).fit(a[use],a[label])
            b[name]=binmet(z[label],m.predict_proba(z[use])[:,1])
        blocks.append(b)
    return {'coverage':{'rows':int(len(x)),'markets':int(x.market_id.nunique()),'positiveRate':float(x[label].mean())},'blocks':blocks,'summary':{name:summarize_blocks(blocks,name) for name in feature_sets}}


def prob_superiority(a,b,greater=True):
    a=np.asarray(list(a),float);b=np.asarray(list(b),float);a=a[np.isfinite(a)];b=b[np.isfinite(b)]
    if len(a)==0 or len(b)==0:return None
    if greater:return float(np.mean(a[:,None]>b[None,:]) + .5*np.mean(a[:,None]==b[None,:]))
    return float(np.mean(a[:,None]<b[None,:]) + .5*np.mean(a[:,None]==b[None,:]))


def main():
    prereg=json.loads(PREREG.read_text(encoding='utf-8'))
    f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan)
    f.market_id=f.market_id.astype(int); f.first_event_ms=f.first_event_ms.astype(np.int64)
    f['order_hash']=f.parent_id.astype(str).str.extract(r'(0x[0-9a-fA-F]{64})',expand=False).str.lower()

    mids=tuple(sorted(f.market_id.unique().tolist())); ph=','.join('?'*len(mids))
    results=qdf(OFF,f"select market_id,resolved_at_ms from target_market_results where market_id in ({ph})",mids)
    sealed=set()
    for r in results.itertuples():
        if pd.notna(r.resolved_at_ms) and int(r.resolved_at_ms)>0:
            day=dt.datetime.fromtimestamp(int(r.resolved_at_ms)/1000,ZoneInfo('Asia/Taipei')).date().isoformat()
            if day=='2026-08-16': sealed.add(int(r.market_id))
    f=f[~f.market_id.isin(sealed)].copy()
    mids=tuple(sorted(f.market_id.unique().tolist())); ph=','.join('?'*len(mids))

    # Official parent chronology, used strictly before each current parent to reconstruct portfolio geometry.
    po=qdf(OFF,f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where market_id in ({ph}) order by market_id,first_event_ms,parent_id",mids)
    po.market_id=po.market_id.astype(int);po.first_event_ms=po.first_event_ms.astype(np.int64)
    pog={int(k):g.sort_values(['first_event_ms','parent_id']).copy() for k,g in po.groupby('market_id')}

    # High-confidence inferred lifecycle state; identity remains probabilistic and is used only as supporting topology/progress evidence.
    life=qdf(LIFE,f"select market_id,lower(order_hash) order_hash,target_side,placement_first_ms,last_target_ms,placement_allocated_shares,expected_parent_shares,confidence,placement_coverage,fill_allocation_coverage from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null",mids)
    life=life[(life.confidence>=.75)&(life.placement_coverage>=.85)&(life.fill_allocation_coverage>=.70)].copy()
    lifeg={int(k):g.copy() for k,g in life.groupby('market_id')}
    observed_life_markets=set(lifeg)
    ev=qdf(OFF,f"select market_id,lower(order_hash) order_hash,role,side,quote_type,event_ms,shares from wallet_shadow_target_events where market_id in ({ph}) and role='MAKER' and quote_type='BID' order by market_id,event_ms,id",mids)
    ev.market_id=ev.market_id.astype(int);ev.event_ms=ev.event_ms.astype(np.int64)
    evg={int(k):g.copy() for k,g in ev.groupby('market_id')}

    # Frozen objective-family hypothesis from observed Target semantics.
    f['objective_family']=np.where(f.side.astype(str)==f.weak_side.astype(str),'PAIR_BALANCE',np.where((f.is_add.astype(int)==1)&(f.side.astype(str)==f.dominant_side.astype(str)),'STATE_SHAPING','OTHER'))
    f['archetype']=np.where((f.objective_family=='PAIR_BALANCE')&(f.is_add.astype(int)==0),'REPAIR_WEAK',np.where((f.objective_family=='PAIR_BALANCE')&(f.is_add.astype(int)==1),'PREPOSITION_LIKE_WEAK_ADD',np.where(f.objective_family=='STATE_SHAPING','ALLOW_STATE_SHAPING','OTHER')))
    f['objective_key']=f.objective_family.astype(str)+'|'+f.side.astype(str)

    # Strict-past feature reconstruction.
    out=[]
    for mid,g0 in f.groupby('market_id'):
        g=g0.sort_values(['first_event_ms','parent_id']).copy().reset_index(drop=True)
        pp=pog.get(int(mid)); gl=lifeg.get(int(mid)); ge=evg.get(int(mid))
        if pp is not None:
            pt=pp.first_event_ms.to_numpy(np.int64); pside=pp.side.astype(str).to_numpy(); prole=pp.role.astype(str).to_numpy(); psh=pp.shares.fillna(0).to_numpy(float); ppx=pp.average_price.fillna(0).to_numpy(float)
            cup=np.cumsum(np.where(pside=='UP',psh,0.)); cdn=np.cumsum(np.where(pside=='DOWN',psh,0.)); ccost=np.cumsum(psh*ppx); cfee=np.cumsum(np.array([fee(q,p) if r=='TAKER' else 0. for q,p,r in zip(psh,ppx,prole)]))
        else: pt=np.array([],np.int64)
        if ge is not None:
            byhash={str(h):x.sort_values('event_ms') for h,x in ge.groupby('order_hash') if pd.notna(h)}
        else: byhash={}
        ts=g.first_event_ms.to_numpy(np.int64); fam=g.objective_family.astype(str).to_numpy(); keys=g.objective_key.astype(str).to_numpy(); sides=g.side.astype(str).to_numpy()
        for i,r in enumerate(g.itertuples()):
            t=int(r.first_event_ms)
            # official geometry strictly before t
            if len(pt):
                j=np.searchsorted(pt,t,side='left')-1
            else:j=-1
            up=float(cup[j]) if j>=0 else 0.; dn=float(cdn[j]) if j>=0 else 0.; cost=float(ccost[j]) if j>=0 else 0.; fees=float(cfee[j]) if j>=0 else 0.
            pu=up-cost-fees; pdn=dn-cost-fees; gross=up+dn; absnet=abs(up-dn); floor=min(pu,pdn); upside=max(pu,pdn); cov=(1-absnet/gross) if gross>EPS else 0.
            weak='DOWN' if up>dn+EPS else 'UP' if dn>up+EPS else str(r.weak_side); surplus='UP' if weak=='DOWN' else 'DOWN'
            prior_idx=np.where((ts<t)&(ts>=t-H15))[0]
            prior5=np.where((ts<t)&(ts>=t-H5))[0]
            prev_idx=np.where(ts<t)[0]
            prev=int(prev_idx[-1]) if len(prev_idx) else None
            ev15=int(len(prior_idx));ev5=int(len(prior5))
            wf=int(np.sum(fam[prior_idx]=='PAIR_BALANCE')) if len(prior_idx) else 0; sf=int(np.sum(fam[prior_idx]=='STATE_SHAPING')) if len(prior_idx) else 0
            distinct=int(len(set(keys[prior_idx]))) if len(prior_idx) else 0
            trans=0
            if len(prior_idx)>1:
                seq=fam[prior_idx]; trans=int(np.sum(seq[1:]!=seq[:-1]))
            secprev=float((t-ts[prev])/1000.) if prev is not None else 999.
            last_age=0.
            if prev is not None:
                pk=keys[prev]; start=ts[prev]; k=prev-1
                while k>=0 and keys[k]==pk and ts[k+1]-ts[k]<=H15:
                    start=ts[k];k-=1
                last_age=float((t-start)/1000.)
            # lifecycle root counts/progress strictly before t
            aw=asu=0; uw=usu=rw5=rs5=0.
            if gl is not None and len(gl):
                starts=gl.placement_first_ms.fillna(0).to_numpy(np.int64); ends=gl.last_target_ms.fillna(gl.placement_first_ms).to_numpy(np.int64); lsides=gl.target_side.astype(str).to_numpy(); hashes=gl.order_hash.astype(str).to_numpy(); comm=gl.placement_allocated_shares.fillna(0).to_numpy(float); exp=gl.expected_parent_shares.fillna(0).to_numpy(float);comm=np.where(comm>EPS,comm,exp)
                active=(starts<t)&(ends>=t)&(comm>EPS)
                for ii in np.where(active)[0]:
                    h=hashes[ii]; side=lsides[ii]; cqty=float(comm[ii]); x=byhash.get(h);real=recent=0.
                    if x is not None:
                        et=x.event_ms.to_numpy(np.int64);eq=x.shares.fillna(0).to_numpy(float);q=np.searchsorted(et,t,side='left');lo=np.searchsorted(et,t-H5,side='left');real=float(eq[:q].sum());recent=float(eq[lo:q].sum())
                    unresolved=max(0.,cqty-min(real,cqty))
                    if side==weak: aw+=1;uw+=unresolved;rw5+=recent
                    elif side==surplus: asu+=1;usu+=unresolved;rs5+=recent
            z=r._asdict();z.update({'pre_floor':float(floor),'pre_upside':float(upside),'pre_absNet':float(absnet),'pre_coverage':float(cov),'pre_floor_per_gross':float(floor/gross) if gross>EPS else 0.,'seconds_since_prev_parent':secprev,'events_5s_strict':ev5,'events_15s_strict':ev15,'objective_transitions_15s':trans,'weak_events_15s_strict':wf,'state_events_15s_strict':sf,'distinct_objective_keys_15s':distinct,'last_objective_age_s':last_age,'active_weak_roots':float(aw),'active_surplus_roots':float(asu),'unresolved_weak_shares':float(uw),'unresolved_surplus_shares':float(usu),'recent_weak_fill_5s':float(rw5),'recent_surplus_fill_5s':float(rs5),'lifecycle_market_observed':int(int(mid) in observed_life_markets),'candidate_side_is_weak':int(str(r.side)==weak),'candidate_side_is_surplus':int(str(r.side)==surplus),'candidate_price':float(r.price),'strict_pre_weak_side':weak,'strict_pre_surplus_side':surplus})
            out.append(z)
    d=pd.DataFrame(out).sort_values(['market_id','first_event_ms','parent_id']).reset_index(drop=True)

    # Pair rows: all strict-past parents within 15s, frozen prereg rule.
    pairs=[]
    for mid,g in d.groupby('market_id'):
        g=g.sort_values(['first_event_ms','parent_id']).reset_index(drop=True);ts=g.first_event_ms.to_numpy(np.int64)
        for i in range(len(g)):
            t=int(ts[i]); ids=np.where((ts<t)&(ts>=t-H15))[0]
            for j in ids:
                cur=g.iloc[i];pr=g.iloc[j];z=cur.to_dict();z.update({'pair_prior_t':int(pr.first_event_ms),'pair_prior_age_s':float((t-int(pr.first_event_ms))/1000.),'pair_prior_family':str(pr.objective_family),'pair_prior_key':str(pr.objective_key),'pair_prior_side':str(pr.side),'pair_prior_is_pair_balance':int(pr.objective_family=='PAIR_BALANCE'),'pair_prior_is_state_shaping':int(pr.objective_family=='STATE_SHAPING'),'pair_prior_side_is_current_weak':int(str(pr.side)==str(cur.strict_pre_weak_side)),'pair_prior_side_is_current_surplus':int(str(pr.side)==str(cur.strict_pre_surplus_side)),'same_objective_pair':int(str(pr.objective_key)==str(cur.objective_key)),'same_side_pair':int(str(pr.side)==str(cur.side))})
                pairs.append(z)
    pairdf=pd.DataFrame(pairs)

    # Future topology from each observed responsibility checkpoint: next 5s same-objective vs different-objective openings.
    future_rows=[]
    for mid,g in d.groupby('market_id'):
        g=g.sort_values(['first_event_ms','parent_id']).reset_index(drop=True);ts=g.first_event_ms.to_numpy(np.int64);keys=g.objective_key.astype(str).to_numpy()
        for i in range(len(g)):
            t=int(ts[i]); fut=np.where((ts>t)&(ts<=t+H5))[0]
            if not len(fut):continue
            same=bool(np.any(keys[fut]==keys[i])); diff=bool(np.any(keys[fut]!=keys[i]));
            if same==diff:continue
            z=g.iloc[i].to_dict();z['future_diff_objective_5s']=int(diff);future_rows.append(z)
    futdf=pd.DataFrame(future_rows)

    # PREPOSITION-like validation: weak-side ADD, then same pre-event weak side REPAIR within 5/15s.
    pre_rows=[]
    for mid,g in d.groupby('market_id'):
        g=g.sort_values(['first_event_ms','parent_id']).reset_index(drop=True)
        for i,r in g.iterrows():
            if r.objective_family!='PAIR_BALANCE':continue
            t=int(r.first_event_ms);future=g[(g.first_event_ms>t)&(g.first_event_ms<=t+H15)&(g.archetype=='REPAIR_WEAK')&(g.side.astype(str)==str(r.side))]
            dtms=int(future.first_event_ms.min()-t) if len(future) else None
            z=r.to_dict();z['preposition_label']=int(r.archetype=='PREPOSITION_LIKE_WEAK_ADD');z['future_same_side_repair_5s']=int(dtms is not None and dtms<=H5);z['future_same_side_repair_15s']=int(dtms is not None and dtms<=H15);z['next_same_side_repair_delay_ms']=dtms;z['shares_to_pre_absnet']=float(r.shares/max(float(r.pre_absNet),EPS)) if float(r.pre_absNet)>EPS else None;pre_rows.append(z)
    predf=pd.DataFrame(pre_rows)

    # State-shaping parallel structural rows: current surplus ADD with recent weak objective; owner-active evidence is separate high-confidence support.
    state=d[d.archetype=='ALLOW_STATE_SHAPING'].copy()
    state['weak_allow_recent_parallel_5s']=(state.weak_events_15s_strict>0).astype(int)  # 15s objective memory, name retained for historical topology contract
    state['weak_allow_lifecycle_parallel']=(state.active_weak_roots>0).astype(int)

    # Objective-group observation lifecycle counts under frozen 15s memory.
    group_counts=Counter(); group_markets=defaultdict(set); simultaneous=0
    for mid,g in d.groupby('market_id'):
        g=g.sort_values(['first_event_ms','parent_id']).reset_index(drop=True)
        open_last={}; last_global_t=None; last_key=None
        for _,r in g.iterrows():
            t=int(r.first_event_ms);key=str(r.objective_key)
            if last_global_t is not None and t==last_global_t: simultaneous+=1
            same_open=key in open_last and t-open_last[key]<=H15
            other_open=any(k!=key and t-v<=H15 for k,v in open_last.items())
            if same_open:
                state_name='ACCUMULATE_SAME_OBJECTIVE'
            elif other_open:
                state_name='OPEN_DIFFERENT_OBJECTIVE_PARALLEL'
            else:
                state_name='OPEN_NEW_OBJECTIVE'
            group_counts[state_name]+=1;group_markets[state_name].add(int(mid));open_last[key]=t;last_global_t=t;last_key=key
        # observed retirement is simply keys aging beyond memory horizon by market end chronology; not a claim of economic completion.
        if len(g):
            end=int(g.first_event_ms.max())
            for k,v in open_last.items():
                if end-v>H15: group_counts['OBSERVED_MEMORY_RETIRE']+=1;group_markets['OBSERVED_MEMORY_RETIRE'].add(int(mid))

    # Lifecycle matched partial-completion evidence by archetype/objective family.
    lifematch=d.merge(life[['market_id','order_hash','target_side','placement_first_ms','last_target_ms','placement_allocated_shares','expected_parent_shares','confidence']],on=['market_id','order_hash'],how='inner',suffixes=('','_life'))
    partial=[]
    for r in lifematch.itertuples():
        cqty=float(r.placement_allocated_shares) if pd.notna(r.placement_allocated_shares) and float(r.placement_allocated_shares)>EPS else float(r.expected_parent_shares or 0)
        x=evg.get(int(r.market_id));real=0.
        if x is not None:
            xx=x[(x.order_hash.astype(str)==str(r.order_hash))&(x.event_ms.astype(np.int64)<int(r.first_event_ms))]
            real=float(xx.shares.fillna(0).sum())
        partial.append({'market_id':int(r.market_id),'order_hash':str(r.order_hash),'archetype':str(r.archetype),'objective_family':str(r.objective_family),'commitment':cqty,'confirmed_before_first_parent_event':real,'partial_before_event':int(real>EPS and real<cqty-EPS)})
    partialdf=pd.DataFrame(partial)

    # Fixed narrow teachers/ablations.
    pair_features={
        'GEOMETRY':GEOM,
        'GEOM_MEMORY':GEOM+GENMEM+PAIRPRIOR,
        'GEOM_MEMORY_OWNER':GEOM+GENMEM+PAIRPRIOR+OWNER,
        'FULL_CANDIDATE_DIAGNOSTIC':GEOM+GENMEM+PAIRPRIOR+OWNER+CAND
    }
    pair_model=chronological_binary(pairdf,'same_objective_pair',pair_features,32000) if len(pairdf) else {}
    future_features={'GEOMETRY':GEOM,'GEOM_GENERIC_MEMORY':GEOM+GENMEM,'GEOM_MEMORY_OWNER':GEOM+GENMEM+OWNER,'GEOM_MEMORY_OWNER_ROLE_CONTEXT':GEOM+GENMEM+OWNER+ROLEMEM}
    future_model=chronological_binary(futdf,'future_diff_objective_5s',future_features,33000) if len(futdf) else {}
    pre_features={'GEOMETRY':GEOM,'GEOM_GENERIC_MEMORY':GEOM+GENMEM,'GEOM_MEMORY_OWNER':GEOM+GENMEM+OWNER,'GEOM_MEMORY_OWNER_ROLE_CONTEXT':GEOM+GENMEM+OWNER+ROLEMEM}
    pre_model=chronological_binary(predf,'preposition_label',pre_features,34000) if len(predf) else {}

    # Structural summaries.
    pair_summary={'rows':int(len(pairdf)),'markets':int(pairdf.market_id.nunique()) if len(pairdf) else 0,'sameObjectivePairs':int(pairdf.same_objective_pair.sum()) if len(pairdf) else 0,'differentObjectivePairs':int((1-pairdf.same_objective_pair).sum()) if len(pairdf) else 0,'sameSidePairs':int(pairdf.same_side_pair.sum()) if len(pairdf) else 0,'sameSideSameObjective':int(((pairdf.same_side_pair==1)&(pairdf.same_objective_pair==1)).sum()) if len(pairdf) else 0,'sameSideDifferentObjective':int(((pairdf.same_side_pair==1)&(pairdf.same_objective_pair==0)).sum()) if len(pairdf) else 0}
    archetype_counts={str(k):int(v) for k,v in d.archetype.value_counts().to_dict().items()}
    prepos=predf[predf.preposition_label==1] if len(predf) else predf
    repair=predf[predf.preposition_label==0] if len(predf) else predf
    preposition_summary={'rows':int(len(predf)),'prepositionLike':int(len(prepos)),'repairWeak':int(len(repair)),'prepositionFutureRepair5sRate':float(prepos.future_same_side_repair_5s.mean()) if len(prepos) else None,'prepositionFutureRepair15sRate':float(prepos.future_same_side_repair_15s.mean()) if len(prepos) else None,'repairFutureRepair5sRate':float(repair.future_same_side_repair_5s.mean()) if len(repair) else None,'repairFutureRepair15sRate':float(repair.future_same_side_repair_15s.mean()) if len(repair) else None,'prepositionMedianSharesToPreAbsNet':float(prepos.shares_to_pre_absnet.dropna().median()) if len(prepos.shares_to_pre_absnet.dropna()) else None,'prepositionSharesWithinDeficitRate':float((prepos.shares_to_pre_absnet.dropna()<=1+1e-9).mean()) if len(prepos.shares_to_pre_absnet.dropna()) else None,'highConfidenceLifecycleMatchedPreposition':int((lifematch.archetype=='PREPOSITION_LIKE_WEAK_ADD').sum()) if len(lifematch) else 0}
    state_summary={'rows':int(len(state)),'markets':int(state.market_id.nunique()),'recentWeakObjective15s':int((state.weak_events_15s_strict>0).sum()),'recentWeakObjective15sRate':float((state.weak_events_15s_strict>0).mean()) if len(state) else None,'highConfidenceLifecycleWeakActive':int((state.active_weak_roots>0).sum()),'highConfidenceLifecycleWeakActiveRateAllStateRows':float((state.active_weak_roots>0).mean()) if len(state) else None,'lifecycleObservedStateRows':int((state.lifecycle_market_observed==1).sum()),'lifecycleWeakActiveRateConditionalObservedMarket':float((state.loc[state.lifecycle_market_observed==1,'active_weak_roots']>0).mean()) if int((state.lifecycle_market_observed==1).sum()) else None}
    # Antecedent anatomy, parallel vs solo state-shaping, excluding definitional weak-event features.
    stpar=state[state.weak_events_15s_strict>0];stsolo=state[state.weak_events_15s_strict==0]
    anatomy={}
    for col in GEOM+['seconds_since_prev_parent','events_5s_strict','events_15s_strict','objective_transitions_15s','last_objective_age_s','active_weak_roots','active_surplus_roots','recent_weak_fill_5s','recent_surplus_fill_5s']:
        if col not in state:continue
        anatomy[col]={'parallelN':int(stpar[col].notna().sum()),'soloN':int(stsolo[col].notna().sum()),'parallelMedian':float(stpar[col].median()) if len(stpar) else None,'soloMedian':float(stsolo[col].median()) if len(stsolo) else None,'parallelGreaterProbability':prob_superiority(stpar[col].dropna(),stsolo[col].dropna(),True)}

    # Independent full300 topology replication (disjoint cohort, no ADD/REPAIR semantics claimed).
    q=pd.read_csv(FULL300).replace([np.inf,-np.inf],np.nan);q.market_id=q.market_id.astype(int);q=q[(q.seconds_left>=60)&(q.seconds_left<=300)].copy()
    q['owner_topology']=np.select([(q.weak_active_owners>0)&(q.dominant_active_owners>0),(q.weak_active_owners>0)&(q.dominant_active_owners<=0),(q.weak_active_owners<=0)&(q.dominant_active_owners>0)],['BOTH_SIDES','WEAK_ONLY','DOMINANT_ONLY'],default='NONE')
    full300_counts={str(k):int(v) for k,v in q.owner_topology.value_counts().to_dict().items()}; both=q[q.owner_topology=='BOTH_SIDES']; one=q[q.owner_topology.isin(['WEAK_ONLY','DOMINANT_ONLY'])]
    full300_anat={}
    for col in ['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','current_mode_age_s','events_5s','events_15s','transitions_15s','weak_unresolved_shares','dominant_unresolved_shares','weak_fill_shares_5s','dominant_fill_shares_5s']:
        full300_anat[col]={'bothMedian':float(both[col].median()) if len(both) else None,'oneSideMedian':float(one[col].median()) if len(one) else None,'bothGreaterProbability':prob_superiority(both[col].dropna(),one[col].dropna(),True)}
    full300_summary={'rows':int(len(q)),'markets':int(q.market_id.nunique()),'sourceMarketOverlapWithRole107':int(len(set(q.market_id)&set(d.market_id))),'topologyCounts':full300_counts,'bothSideRate':float((q.owner_topology=='BOTH_SIDES').mean()),'sameSideMultiOwnerRows':int(((q.weak_active_owners>=2)|(q.dominant_active_owners>=2)).sum()),'anatomy':full300_anat}

    # Independent joint base+upside replication: strict-past dual weak/surplus maker flow history.
    j=pd.read_csv(JOINT).replace([np.inf,-np.inf],np.nan);j.market_id=j.market_id.astype(int)
    j['flow_context']=np.select([(j.weak_maker_shares_15s>0)&(j.surplus_maker_shares_15s>0),(j.weak_maker_shares_15s>0)&(j.surplus_maker_shares_15s<=0),(j.weak_maker_shares_15s<=0)&(j.surplus_maker_shares_15s>0)],['DUAL_WEAK_SURPLUS','WEAK_ONLY','SURPLUS_ONLY'],default='NONE')
    jc={}
    for k,g in j.groupby('flow_context'):
        jc[str(k)]={'rows':int(len(g)),'markets':int(g.market_id.nunique()),'jointOutcomeRate':float(g.y_joint.mean()),'floorProgressRate':float(g.y_floor.mean()),'upsideProgressRate':float(g.y_upside.mean())}
    dual=jc.get('DUAL_WEAK_SURPLUS',{}).get('jointOutcomeRate'); none=jc.get('NONE',{}).get('jointOutcomeRate');
    joint_summary={'rows':int(len(j)),'markets':int(j.market_id.nunique()),'contexts':jc,'dualVsNoneJointRateRatio':float(dual/none) if dual is not None and none not in (None,0) else None}

    # Support/decision: structural, not AUC threshold mining.
    recurring_same=pair_summary['sameObjectivePairs']>=50 and pair_summary['sameSideSameObjective']>=20
    recurring_diff=pair_summary['differentObjectivePairs']>=50 and state_summary['recentWeakObjective15s']>=20
    preposition_found=preposition_summary['prepositionLike']>=20 and (preposition_summary['prepositionFutureRepair15sRate'] or 0)>0
    independent_parallel=full300_counts.get('BOTH_SIDES',0)>0
    dual_econ=(joint_summary['contexts'].get('DUAL_WEAK_SURPLUS',{}).get('rows',0)>0)
    status='KEEP' if (recurring_same and recurring_diff and preposition_found and independent_parallel and dual_econ) else 'INCONCLUSIVE'

    # Save compact research rows (not full future labels from unrelated cohorts).
    keepcols=['market_id','first_event_ms','parent_id','order_hash','side','shares','price','mode','is_add','weak_side','dominant_side','objective_family','archetype','objective_key']+GEOM+GENMEM+ROLEMEM+OWNER+CAND+['strict_pre_weak_side','strict_pre_surplus_side']
    d[[c for c in keepcols if c in d.columns]].to_csv(ROWS,index=False)

    rep={
      'version':'R4_P0B_TARGET_OBJECTIVE_TOPOLOGY_V1','lane':'E_TARGET_OBJECTIVE_TOPOLOGY','status':status,'researchOnly':True,'actionAuthority':False,
      'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),
      'coverage':{'objectiveRoleRows':int(len(d)),'objectiveRoleMarkets':int(d.market_id.nunique()),'sealedMarketsRemoved':sorted(sealed),'highConfidenceLifecycleRows':int(len(life)),'highConfidenceLifecycleMarkets':int(life.market_id.nunique()),'lifecycleMatchedFormationRows':int(len(lifematch))},
      'objectiveHypothesisObservedArchetypes':archetype_counts,
      'sameVsDifferentObjective':{'structural':pair_summary,'narrowTeacher':pair_model},
      'futureObjectiveTopology5s':future_model,
      'prepositionLikeTopology':{'structural':preposition_summary,'narrowTeacher':pre_model},
      'stateShapingWeakAllowParallel':{'structural':state_summary,'strictPastAntecedentAnatomy':anatomy},
      'objectiveGroupLifecycleObservation':{'counts':{k:int(v) for k,v in group_counts.items()},'markets':{k:int(len(v)) for k,v in group_markets.items()},'sameTimestampParentTransitions':int(simultaneous),'lifecyclePartialEvidence':{'matchedRows':int(len(partialdf)),'partialBeforeFirstParentEvent':int(partialdf.partial_before_event.sum()) if len(partialdf) else 0,'byArchetype':{str(k):{'rows':int(len(g)),'partialRate':float(g.partial_before_event.mean())} for k,g in partialdf.groupby('archetype')} if len(partialdf) else {}},'interpretationGuard':'OPEN/ACCUMULATE/PARALLEL/RETIRE are observed objective-memory topology states, not a claim that Target stores an explicit hidden objective_id. Explicit objective completion/credit is only inferred where lifecycle/fill evidence supports it.'},
      'independentReplication':{'large300OwnerTopology':full300_summary,'jointBaseUpsideDualFlow':joint_summary},
      'decisionChecks':{'recurringSameObjective':recurring_same,'recurringDifferentObjective':recurring_diff,'prepositionTopologyFound':preposition_found,'independentParallelTopology':independent_parallel,'jointDualObjectiveEconomicContext':dual_econ},
      'guards':prereg['guards'],
      'files':{'rows':str(ROWS.relative_to(ROOT)).replace('\\','/')}
    }
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':rep['coverage'],'archetypes':archetype_counts,'sameVsDifferent':pair_summary,'preposition':preposition_summary,'stateParallel':state_summary,'groupLifecycle':rep['objectiveGroupLifecycleObservation'],'full300':{'topologyCounts':full300_counts,'bothSideRate':full300_summary['bothSideRate'],'sameSideMultiOwnerRows':full300_summary['sameSideMultiOwnerRows'],'overlap':full300_summary['sourceMarketOverlapWithRole107']},'joint':joint_summary,'modelSummaries':{'sameVsDifferent':pair_model.get('summary',{}),'futureTopology':future_model.get('summary',{}),'preposition':pre_model.get('summary',{})}},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    main()
