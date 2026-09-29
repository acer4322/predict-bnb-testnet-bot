from __future__ import annotations
import importlib.util,json,sys
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
P1=ROOT/'tools'/'test_r4_mpq_relation_aware_tranche_v1.py'
s1=importlib.util.spec_from_file_location('r4_rel_tranche',P1);r=importlib.util.module_from_spec(s1);assert s1 and s1.loader
sys.modules[s1.name]=r;s1.loader.exec_module(r)
P2=ROOT/'tools'/'test_r4_marginal_pair_quality_replication_v2.py'
s2=importlib.util.spec_from_file_location('r4_mpq_rep',P2);m=importlib.util.module_from_spec(s2);assert s2 and s2.loader
sys.modules[s2.name]=m;s2.loader.exec_module(m)
TZ=ZoneInfo('Asia/Taipei');VERSION='R4_MPQ_RELATION_AWARE_TRANCHE_REPLICATION_V2';BLOCK=500

def md(a):return median(a) if a else None

def eval_ids(ids,ev,winners,stress):
    active=[x for x in ids if x in ev and winners.get(x) in {'UP','DOWN'} and r.t.has_flag(ev[x],stress)]
    base={x:r.replay(ev[x],winners[x],'BASELINE',stress) for x in active};hard={x:r.replay(ev[x],winners[x],'HARD_MPQ',stress) for x in active};rel={x:r.replay(ev[x],winners[x],'RELATION_TRANCHE',stress) for x in active}
    hf=[hard[x]['floor']-base[x]['floor'] for x in active];rf=[rel[x]['floor']-base[x]['floor'] for x in active];hp=[hard[x]['pnl']-base[x]['pnl'] for x in active];rp=[rel[x]['pnl']-base[x]['pnl'] for x in active]
    mhf=md(hf);mrf=md(rf);mhp=md(hp);mrp=md(rp);ret=mrf/mhf if mhf and mhf>1e-9 else None;rec=(mrp-mhp)/abs(mhp) if mhp is not None and mhp< -1e-9 else None
    passed=bool(len(active)>=5 and mrf is not None and mrf>0 and ret is not None and ret>=.5 and (rec is None or rec>=.25))
    return {'markets':len(ids),'activeMarkets':len(active),'hardMedianDeltaFloor':mhf,'relationTrancheMedianDeltaFloor':mrf,'floorRetention':ret,'hardMedianDeltaPnl':mhp,'relationTrancheMedianDeltaPnl':mrp,'pnlRecoveryFraction':rec,'hardMedianDeltaCoverage':md([hard[x]['coverage']-base[x]['coverage'] for x in active]),'relationTrancheMedianDeltaCoverage':md([rel[x]['coverage']-base[x]['coverage'] for x in active]),'hardMedianDeltaAbsNet':md([hard[x]['absnet']-base[x]['absnet'] for x in active]),'relationTrancheMedianDeltaAbsNet':md([rel[x]['absnet']-base[x]['absnet'] for x in active]),'surplusTrancheEvents':sum(rel[x]['surplusTranche'] for x in active),'crossHardEvents':sum(rel[x]['crossHard'] for x in active),'pass':passed}

def main():
    meta,ev=m.load(2000);winners={mid:w for mid,wend,w in meta};ids=[mid for mid,wend,w in meta]
    blocks=[]
    for i in range(0,len(ids),BLOCK):
        z=ids[i:i+BLOCK];res=eval_ids(z,ev,winners,'NONE');res['blockIndex']=i//BLOCK+1;res['startMarket']=z[0] if z else None;res['endMarket']=z[-1] if z else None;blocks.append(res)
    combined=eval_ids(ids,ev,winners,'NONE')
    stresses={s:eval_ids(ids,ev,winners,s) for s in ['WEAK_DROP_ALTERNATE','WEAK_PARTIAL_HALF_ALTERNATE','MAKER_DROP_EVERY5']}
    block_pass=sum(b['pass'] for b in blocks if b['activeMarkets']>=5);eligible_blocks=sum(b['activeMarkets']>=5 for b in blocks)
    stress_eligible=[v for v in stresses.values() if v['activeMarkets']>=10];stress_pass=sum(v['pass'] for v in stress_eligible)
    overall=bool(combined['pass'] and eligible_blocks>=3 and block_pass>=3 and len(stress_eligible)>=2 and stress_pass>=2)
    report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':'Weak-side crossing/flat hard-protected; surplus-side MPQ fill truncated exactly to post-floor=0 boundary.','cohort':{'ordinaryMarkets':len(ids),'blockSize':BLOCK,'blocks':len(blocks),'sealed20260816':True},'blocks':blocks,'combined':combined,'stresses':stresses,'gate':{'required':'combined pass, >=3 eligible chronology blocks pass, and >=2 eligible execution stresses pass','eligibleBlocks':eligible_blocks,'blockPasses':block_pass,'eligibleStressVariants':len(stress_eligible),'stressPasses':stress_pass,'pass':overall},'guards':{'floorBoundary':0.0,'noThresholdSweep':True,'winnerEvaluationOnly':True,'noEchtgeldTraining':True,'noLiveR3Change':True,'no8781Change':True,'mpqFrozen':True}}
    out=ROOT/'data'/'research'/'r4_v0'/'hourly'/f"r4_mpq_relation_aware_tranche_replication_v2_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'blocks':blocks,'combined':combined,'stresses':stresses,'gate':report['gate']},ensure_ascii=False))
if __name__=='__main__':main()
