from __future__ import annotations
import json, math
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_TIMEFRAME_ACTION_VALUE_DECISION_SLICE_V1_20260907.csv'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_TIMEFRAME_JOINT_STATE_2X2_V1_20260907.json'
FRAMES=['BTC5M','BTC15M','BTC1H','ETH5M']

# Precommitted equal-weight, outcome-blind state definitions.
THESIS_FEATURES=[('dominant_mid',+1),('dominant_mid_delta_5updates',+1),('dominant_mid_delta_since_prev_action',+1)]
SERVICE_FEATURES=[('prev_service_fraction',+1),('paired_coverage',+1),('repair_progress_since_last_expand',+1),('imbalance_ratio',-1),('abs_net',-1)]
LIABILITY_FEATURES=[('imbalance_ratio',+1),('paired_coverage',-1),('last_repair_age_frac',+1),('abs_net',+1),('parent_gap_frac',+1)]


def pct_rank(s:pd.Series)->pd.Series:
    x=pd.to_numeric(s,errors='coerce')
    # percentile ranks use only strict-past state values; action label is not used.
    return x.rank(method='average',pct=True)

def composite(g:pd.DataFrame,spec):
    cols=[]
    for f,sgn in spec:
        r=pct_rank(g[f])
        cols.append(r if sgn>0 else 1-r)
    return pd.concat(cols,axis=1).mean(axis=1,skipna=True)

def ci_wilson(k,n,z=1.96):
    if not n:return [None,None]
    p=k/n;d=1+z*z/n
    c=(p+z*z/(2*n))/d
    h=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/d
    return [max(0,c-h),min(1,c+h)]

def cells(g,prev,pos,second_name,second_score):
    z=g[g.prev_class.eq(prev)&g.current_class.isin(['REPAIR','EXPAND'])].copy()
    z['thesis_score']=composite(z,THESIS_FEATURES)
    z[second_name]=second_score(z)
    # Fixed rank-composite split at 0.5. No target-label tuning.
    z=z[z.thesis_score.notna()&z[second_name].notna()].copy()
    z['thesis_high']=z.thesis_score>=0.5
    z['second_high']=z[second_name]>=0.5
    z['positive']=z.current_class.eq(pos)
    out={}
    for th in (False,True):
        for sh in (False,True):
            q=z[(z.thesis_high==th)&(z.second_high==sh)]
            k=int(q.positive.sum());n=len(q)
            out[f"T{'H' if th else 'L'}_{second_name[0].upper()}{'H' if sh else 'L'}"]={
                'n':n,'positive':k,'positiveRate':k/n if n else None,'wilson95':ci_wilson(k,n),
                'medianThesisScore':float(q.thesis_score.median()) if n else None,
                f'median{second_name.title().replace("_","")}':float(q[second_name].median()) if n else None,
            }
    return z,out

def rate(z,mask):
    q=z[mask]
    return float(q.positive.mean()) if len(q) else None,len(q)

def main():
    df=pd.read_csv(SRC)
    result={'version':'TARGET_CROSS_TIMEFRAME_JOINT_STATE_2X2_V1','date':'2026-09-07','researchOnly':True,'winnerUsed':False,
            'definitions':{
              'thesisScore':'equal-weight within-frame percentile ranks of dominant_mid, dominant_mid_delta_5updates, dominant_mid_delta_since_prev_action; high >=0.5',
              'serviceProgressScore':'equal-weight ranks of prev_service_fraction, paired_coverage, repair_progress_since_last_expand, inverse imbalance_ratio, inverse abs_net; high >=0.5',
              'liabilityPressureScore':'equal-weight ranks of imbalance_ratio, inverse paired_coverage, last_repair_age_frac, abs_net, parent_gap_frac; high >=0.5',
              'guards':['No winner/settlement','No current action feature inside state scores','No action_mid','No thresholds tuned on labels','Within-frame ranks remove raw scale/timeframe differences']
            },'frames':{}}
    for fr in FRAMES:
        g=df[df.frame.eq(fr)].copy()
        rz,rcells=cells(g,'REPAIR','EXPAND','service_progress_score',lambda x:composite(x,SERVICE_FEATURES))
        ez,ecells=cells(g,'EXPAND','REPAIR','liability_pressure_score',lambda x:composite(x,LIABILITY_FEATURES))
        # Hypothesis contrasts, predeclared.
        re_hh,n_hh=rate(rz,rz.thesis_high & rz.second_high)
        re_ll,n_ll=rate(rz,(~rz.thesis_high) & (~rz.second_high))
        re_th_sl,n_thsl=rate(rz,rz.thesis_high & (~rz.second_high))
        re_tl_sh,n_tlsh=rate(rz,(~rz.thesis_high) & rz.second_high)
        ex_tl_lh,n_tllh=rate(ez,(~ez.thesis_high) & ez.second_high)
        ex_th_ll,n_thll=rate(ez,ez.thesis_high & (~ez.second_high))
        result['frames'][fr]={
          'afterRepair_expand':{'n':len(rz),'cells':rcells,
             'contrasts':{
               'TH_SH_minus_TL_SL':None if re_hh is None or re_ll is None else re_hh-re_ll,
               'TH_SL_minus_TL_SH':None if re_th_sl is None or re_tl_sh is None else re_th_sl-re_tl_sh,
             },
             'expectedOrdering':'TH_SH highest; TL_SL lowest if thesis+service joint state matters'},
          'afterExpand_repair':{'n':len(ez),'cells':ecells,
             'contrasts':{'TL_LH_minus_TH_LL':None if ex_tl_lh is None or ex_th_ll is None else ex_tl_lh-ex_th_ll},
             'expectedOrdering':'TL_LH highest; TH_LL lowest if weak thesis + high liability triggers Repair'}
        }
    # Cross-frame consistency counts.
    re=[];ex=[]
    for fr in FRAMES:
        a=result['frames'][fr]['afterRepair_expand']['cells'];b=result['frames'][fr]['afterExpand_repair']['cells']
        rates={k:v['positiveRate'] for k,v in a.items() if v['positiveRate'] is not None}
        if rates:
            re.append({'frame':fr,'highest':max(rates,key=rates.get),'lowest':min(rates,key=rates.get),
                       'TH_SH_is_highest':max(rates,key=rates.get)=='TH_S','TL_SL_is_lowest':min(rates,key=rates.get)=='TL_S'})
        rates2={k:v['positiveRate'] for k,v in b.items() if v['positiveRate'] is not None}
        if rates2:
            ex.append({'frame':fr,'highest':max(rates2,key=rates2.get),'lowest':min(rates2,key=rates2.get),
                       'TL_LH_is_highest':max(rates2,key=rates2.get)=='TL_L','TH_LL_is_lowest':min(rates2,key=rates2.get)=='TH_L'})
    result['consistency']={'afterRepair_expand':re,'afterExpand_repair':ex}
    OUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    compact={'ok':True,'frames':{fr:{
      'afterRepair':{k:v['positiveRate'] for k,v in result['frames'][fr]['afterRepair_expand']['cells'].items()},
      'afterExpand':{k:v['positiveRate'] for k,v in result['frames'][fr]['afterExpand_repair']['cells'].items()},
      'contrastsRepair':result['frames'][fr]['afterRepair_expand']['contrasts'],
      'contrastsExpand':result['frames'][fr]['afterExpand_repair']['contrasts'],
    } for fr in FRAMES}}
    print(json.dumps(compact,ensure_ascii=False))
if __name__=='__main__':main()
