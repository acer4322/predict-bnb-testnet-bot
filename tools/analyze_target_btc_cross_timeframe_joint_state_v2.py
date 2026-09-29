from __future__ import annotations
import json, math
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_TIMEFRAME_ACTION_VALUE_DECISION_SLICE_V1_20260907.csv'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_CROSS_TIMEFRAME_JOINT_STATE_V2_20260907.json'
BTC=['BTC5M','BTC15M','BTC1H']
ALL=BTC+['ETH5M']
LOCAL_THESIS=[('dominant_mid',+1),('dominant_mid_delta_5updates',+1),('dominant_mid_delta_since_prev_action',+1)]
SERVICE=[('prev_service_fraction',+1),('paired_coverage',+1),('repair_progress_since_last_expand',+1),('imbalance_ratio',-1),('abs_net',-1)]
LIABILITY=[('imbalance_ratio',+1),('paired_coverage',-1),('last_repair_age_frac',+1),('abs_net',+1),('parent_gap_frac',+1)]
EPS=1e-9

def sign(x): return 1 if x>EPS else -1 if x<-EPS else 0

def pct(s): return pd.to_numeric(s,errors='coerce').rank(method='average',pct=True)
def comp(g,spec):
    a=[]
    for f,s in spec:
        r=pct(g[f]); a.append(r if s>0 else 1-r)
    return pd.concat(a,axis=1).mean(axis=1,skipna=True)

def latest_other_state(groups,fr,t):
    # strict-past latest action state from another timeframe whose market contains current t.
    q=groups[fr]
    z=q[(q.window_start_ms<=t)&(q.window_end_ms>t)&(q.t<t)]
    if z.empty:return None
    r=z.loc[z.t.idxmax()]
    post=float(r['net'])+(float(r['action_shares']) if r['current_side']=='UP' else -float(r['action_shares']))
    return sign(post)

def add_cross(df):
    groups={fr:df[df.frame.eq(fr)].sort_values('t').copy() for fr in BTC}
    vals={fr:{'alignA':[],'alignB':[],'supportCount':[],'available':[]} for fr in BTC}
    others={'BTC5M':['BTC15M','BTC1H'],'BTC15M':['BTC5M','BTC1H'],'BTC1H':['BTC5M','BTC15M']}
    out=[]
    for fr in BTC:
        g=groups[fr].copy()
        aa=[];bb=[];cc=[];av=[]
        for _,r in g.iterrows():
            s=sign(float(r['net'])); states=[latest_other_state(groups,o,int(r['t'])) for o in others[fr]]
            al=[(1 if s and x and s==x else 0 if s and x else math.nan) for x in states]
            aa.append(al[0]);bb.append(al[1]);valid=[x for x in al if not pd.isna(x)];cc.append(sum(valid) if valid else math.nan);av.append(len(valid))
        g['alignA']=aa;g['alignB']=bb;g['higher_support_count']=cc;g['higher_available']=av
        out.append(g)
    out.append(df[df.frame.eq('ETH5M')].copy())
    return pd.concat(out,ignore_index=True)

def cell_rates(z,second):
    z=z[z.thesis_score.notna()&z[second].notna()].copy();z['th']=z.thesis_score>=.5;z['sh']=z[second]>=.5
    d={}
    for th in (False,True):
      for sh in (False,True):
        q=z[(z.th==th)&(z.sh==sh)];d[f"T{'H' if th else 'L'}_{'H' if sh else 'L'}"]={'n':len(q),'rate':float(q.positive.mean()) if len(q) else None}
    return d

def analyze_frame(g,fr):
    g=g.copy();local=comp(g,LOCAL_THESIS)
    if fr in BTC:
        # Cross-timeframe alignment enters as two additional equal-weight state components, no label fitting.
        comps=[local,pd.to_numeric(g.alignA,errors='coerce'),pd.to_numeric(g.alignB,errors='coerce')]
        g['thesis_score']=pd.concat(comps,axis=1).mean(axis=1,skipna=True)
    else:g['thesis_score']=local
    g['service_score']=comp(g,SERVICE);g['liability_score']=comp(g,LIABILITY)
    r=g[g.prev_class.eq('REPAIR')&g.current_class.isin(['REPAIR','EXPAND'])].copy();r['positive']=r.current_class.eq('EXPAND')
    e=g[g.prev_class.eq('EXPAND')&g.current_class.isin(['REPAIR','EXPAND'])].copy();e['positive']=e.current_class.eq('REPAIR')
    return {
      'afterRepair_expand':{'n':len(r),'cells':cell_rates(r,'service_score')},
      'afterExpand_repair':{'n':len(e),'cells':cell_rates(e,'liability_score')},
      'crossSupportAvailability':float((g.higher_available>0).mean()) if fr in BTC else None,
    }

def main():
    df=pd.read_csv(SRC);df=add_cross(df)
    res={'version':'TARGET_BTC_CROSS_TIMEFRAME_JOINT_STATE_V2','date':'2026-09-07','researchOnly':True,'winnerUsed':False,
         'definition':'Local thesis rank composite plus, for BTC only, strict-past same-absolute-time alignment with the other two BTC timeframes. Equal weights; no label tuning. Service/liability composites unchanged from V1.',
         'frames':{fr:analyze_frame(df[df.frame.eq(fr)],fr) for fr in ALL},
         'guards':['No winner','Strict-past cross-timeframe state only','No action_mid','No label-tuned threshold','Same latest 6h cohort as V1']}
    OUT.write_text(json.dumps(res,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'frames':{fr:{'R':res['frames'][fr]['afterRepair_expand']['cells'],'E':res['frames'][fr]['afterExpand_repair']['cells'],'coverage':res['frames'][fr]['crossSupportAvailability']} for fr in ALL}},ensure_ascii=False))
if __name__=='__main__':main()
