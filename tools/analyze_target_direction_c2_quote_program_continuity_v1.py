from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
PFILE=BASE/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'
PLAC=LANE/'C2_PLACEMENT_TIME_PASSIVE_VALUE_V1.csv'
EP=Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_EPISODES_V2_20260907.csv')

def find_parent(z,placement_ms,side):
    q=z[(z.target_side==side)&(z.placement_first_ms==placement_ms)]
    if len(q):return q.iloc[0]
    q=z[(z.target_side==side)&((z.placement_first_ms-placement_ms).abs()<=1)]
    return q.iloc[0] if len(q) else None

def predecessor(z,cur):
    q=z[(z.target_side==cur.target_side)&(z.placement_first_ms<cur.placement_first_ms)&z.post_action.isin(['SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT','REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT'])].copy()
    q=q[q.post_action_native_price.notna()]
    if not len(q):return None
    # post_action price is native-book oriented; match current native price exactly to 1e-6, fallback 1 cent for audit only.
    exact=q[(q.post_action_native_price-cur.native_price).abs()<=1e-6]
    if len(exact):q=exact;match='EXACT_NATIVE_PRICE'
    else:
        near=q[(q.post_action_native_price-cur.native_price).abs()<=0.0100001]
        if not len(near):return None
        q=near;match='WITHIN_1_TICK'
    # prior lifecycle label only confirms a next parent inside its inference window; require candidate not >5s before current.
    q=q[(cur.placement_first_ms-q.placement_first_ms)<=5000]
    if not len(q):return None
    r=q.sort_values('placement_first_ms').iloc[-1].copy();r['_match_kind']=match
    return r

def trace(z,cur,entry,max_depth=8):
    chain=[];seen=set();node=cur
    for _ in range(max_depth):
        pred=predecessor(z,node)
        if pred is None:break
        key=str(pred.order_hash)
        if key in seen:break
        seen.add(key)
        chain.append({'order_hash':key,'placement_first_ms':int(pred.placement_first_ms),'target_price':float(pred.target_price),'native_price':float(pred.native_price),'post_action':str(pred.post_action),'post_action_native_price':float(pred.post_action_native_price),'match':str(pred['_match_kind']),'pre_conflict':bool(pred.placement_first_ms<entry)})
        node=pred
        if pred.placement_first_ms<entry:break
    return chain

def main():
    p=pd.read_csv(PFILE,low_memory=False);pl=pd.read_csv(PLAC,low_memory=False);ep=pd.read_csv(EP,usecols=['market_id','entry_ms','renewed_clean_lb_positive','renewed_clean_birth_lower_5s'])
    p=p[(p.placement_coverage>=.85)&(p.fill_allocation_coverage>=.70)&p.placement_first_ms.notna()].copy()
    by={int(m):z.sort_values('placement_first_ms') for m,z in p.groupby('market_id',sort=False)}
    rows=[]
    for r in pl.itertuples():
      z=by[int(r.market_id)];cur=find_parent(z,int(r.placement_first_ms),r.anchor)
      if cur is None:continue
      ch=trace(z,cur,int(r.entry_ms));back=any(x['pre_conflict'] for x in ch)
      direct=bool(ch and ch[0]['pre_conflict'])
      rows.append({'market_id':int(r.market_id),'entry_ms':int(r.entry_ms),'split':r.split,'anchor':r.anchor,'current_order_hash':str(cur.order_hash),'current_placement_ms':int(cur.placement_first_ms),'current_target_price':float(cur.target_price),'current_native_price':float(cur.native_price),'chain_depth':len(ch),'direct_pre_conflict_predecessor':direct,'traces_to_pre_conflict_program':back,'all_chain_exact':bool(ch and all(x['match']=='EXACT_NATIVE_PRICE' for x in ch)),'chain_json':json.dumps(ch,separators=(',',':'))})
    d=pd.DataFrame(rows).merge(ep,on=['market_id','entry_ms'],how='left',validate='one_to_one')
    summ={}
    for sp,z in [('ALL',d)]+[(s,d[d.split.eq(s)]) for s in ['TRAIN','VALIDATION','TEST']]:
      summ[sp]={'rows':len(z),'markets':int(z.market_id.nunique()),'directPreRate':float(z.direct_pre_conflict_predecessor.mean()) if len(z) else None,'tracesPreProgramRate':float(z.traces_to_pre_conflict_program.mean()) if len(z) else None,'allExactTraceRate':float(z.all_chain_exact.mean()) if len(z) else None,'confirmedRenewalRateTraced':float(z.loc[z.traces_to_pre_conflict_program,'renewed_clean_lb_positive'].mean()) if z.traces_to_pre_conflict_program.any() else None,'confirmedRenewalRateUntraced':float(z.loc[~z.traces_to_pre_conflict_program,'renewed_clean_lb_positive'].mean()) if (~z.traces_to_pre_conflict_program).any() else None,'chainDepth':z.chain_depth.value_counts().sort_index().to_dict()}
    # Conservative reclassification: only untraced HQ parent placements count as evidence of fresh Maker-program admission.
    d['fresh_program_admission']=~d.traces_to_pre_conflict_program
    # Of all canonical 417 episodes, how many have such untraced Maker admission?
    all_ep=pd.read_csv(EP,usecols=['market_id','entry_ms','split']);k=set(zip(d.loc[d.fresh_program_admission,'market_id'].astype(int),d.loc[d.fresh_program_admission,'entry_ms'].astype(int)));all_ep['fresh_program_admission']=[int((int(m),int(t)) in k) for m,t in zip(all_ep.market_id,all_ep.entry_ms)]
    cohort={sp:{'rows':len(z),'markets':int(z.market_id.nunique()),'freshAdmissions':int(z.fresh_program_admission.sum()),'freshAdmissionRate':float(z.fresh_program_admission.mean())} for sp,z in [('ALL',all_ep)]+[(s,all_ep[all_ep.split.eq(s)]) for s in ['TRAIN','VALIDATION','TEST']]}
    out={'version':'OUR_C2_QUOTE_PROGRAM_CONTINUITY_V1','status':'RESEARCH_ONLY','parentAdmissionRows':len(d),'parentAdmissionMarkets':int(d.market_id.nunique()),'summary':summ,'canonical417FreshProgramEvidence':cohort,'guards':['Trace uses only retrospective inferred parent lifecycle; post_action CONFIRMED_NEXT_PARENT already encodes a later observed parent and is diagnostic, never runtime-safe.','A traced chain means new order_hash can be explained as refill/reprice continuation of a same-side quote program that began before conflict; it does not prove private intent unchanged.','Untraced does not prove fresh directional decision; missing/ambiguous chain linkage remains possible.','Exact native-price matching preferred; <=1 tick fallback reported in chain.']}
    (LANE/'C2_QUOTE_PROGRAM_CONTINUITY_V1.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');d.to_csv(LANE/'C2_QUOTE_PROGRAM_CONTINUITY_V1.csv',index=False);print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
