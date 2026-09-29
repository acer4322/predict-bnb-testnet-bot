from __future__ import annotations
import numpy as np, pandas as pd, json
from pathlib import Path
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
A=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',low_memory=False).sort_values(['market_id','event_ms']).reset_index(drop=True)
Q=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',low_memory=False).sort_values(['market_id','checkpoint_ms','source_snapshot_id']).reset_index(drop=True)
# Rebuild GPT6 datasets() fields exactly enough for episode_entries.
Q['clock_equal_action']=False; Q['observation_end_ms']=np.nan
for mid,z in Q.groupby('market_id',sort=False):
    ev=A[A.market_id==mid]
    evts=ev.event_ms.to_numpy(np.int64); now=z.checkpoint_ms.to_numpy(np.int64)
    Q.loc[z.index,'clock_equal_action']=np.isin(now,evts)
    end=min(float(np.nanmedian(z.checkpoint_ms+1000*z.seconds_left)),float(max(z.checkpoint_ms.max(),ev.event_ms.max())))
    Q.loc[z.index,'observation_end_ms']=end
Q['usable']=~Q.clock_equal_action & ~Q.next_action_delay_ms.le(1)
Q['prior_sign']=Q.prior_clean_expand_side.map({'UP':1.,'DOWN':-1.})
Q['predict_support']=Q.prior_sign*(Q.predict_up_mid-Q.predict_down_mid)/2
Q['strike_support']=Q.prior_sign*Q.spot_minus_strike_bps
Q['C1']=Q.prior_sign.notna()&Q.predict_support.lt(0)
Q['C2']=Q.C1&Q.strike_support.lt(0)

def gpt6_entries(q,b):
    out=[]
    resets={mid:z[z.economic_role=='COMPOSITE_CROSSING'].event_ms.to_numpy(np.int64) for mid,z in b.groupby('market_id')}
    eligible=q.usable&q.checkpoint_ms.lt(q.observation_end_ms)&q.seconds_left.gt(0)
    for mid,z in q[eligible].groupby('market_id',sort=False):
        prevkey=None;run=-1;seen=set();c2run=0;rt=resets[mid]
        for r in z.itertuples():
            if not r.C1: prevkey=None;c2run=0;continue
            generation=int(np.searchsorted(rt,r.checkpoint_ms,side='left'))
            key=(r.prior_clean_expand_side,generation)
            if key!=prevkey: run+=1;seen=set();c2run=0
            c2run=c2run+1 if r.C2 else 0
            tiers=['C1']+(['C2'] if r.C2 else [])+(['C3'] if c2run>=2 else [])
            for tier in tiers:
                if tier in seen: continue
                seen.add(tier); out.append((int(mid),int(r.checkpoint_ms),tier,key))
            prevkey=key
    return out
G=gpt6_entries(Q,A); G2=[x for x in G if x[2]=='C2']
# Import our runner function.
import importlib.util
spec=importlib.util.spec_from_file_location('ours','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
O=m.build_entries(A,Q)
Okeys=set(zip(O.market_id.astype(int),O.entry_ms.astype(int)))
Gkeys=set((x[0],x[1]) for x in G2)
print(json.dumps({'gpt6C2':len(G2),'gpt6Unique':len(Gkeys),'ours':len(O),'oursUnique':len(Okeys),'oursOnly':sorted(Okeys-Gkeys),'gpt6Only':sorted(Gkeys-Okeys)},ensure_ascii=False,indent=2))
