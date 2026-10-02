from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo
import importlib.util, sys

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_marginal_pair_quality_replication_v2.py'
spec=importlib.util.spec_from_file_location('r4_mpq_rep_v2',P)
m=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=m; spec.loader.exec_module(m)
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_MARGINAL_PAIR_QUALITY_SEMANTIC_ABLATION_V1'


def replay(events,winner='',mode='BASELINE'):
    up=down=cu=cd=0.; pos_ms=0.; last_t=None; flags=0; suppressed=0.; subtypes={'BASE_BREAK':0,'DILUTION_WHILE_SAFE':0}
    for z in events:
        pre=m.geom(up,down,cu,cd); sh=float(z['sh']); side=z['side']; px=float(z['px'])
        pu,pd,pcu,pcd=up,down,cu,cd
        if side=='UP': pu+=sh; pcu+=sh*px
        else: pd+=sh; pcd+=sh*px
        post=m.geom(pu,pd,pcu,pcd)
        reserve_spent=max(0.,pre['floor']-post['floor'])
        full=pre['floor']>0 and reserve_spent>1e-12 and post['edge']<0
        subtype='BASE_BREAK' if full and post['floor']<=0 else 'DILUTION_WHILE_SAFE' if full else None
        if subtype: subtypes[subtype]+=1
        suppress=(mode=='FULL_MPQ' and full) or (mode=='BASE_BREAK_ONLY' and subtype=='BASE_BREAK') or (mode=='DILUTION_WHILE_SAFE_ONLY' and subtype=='DILUTION_WHILE_SAFE')
        if suppress:
            flags+=1; suppressed+=sh; post=pre
        else:
            up,down,cu,cd=pu,pd,pcu,pcd
        if last_t is not None and pre['floor']>0: pos_ms+=max(0,int(z['t'])-int(last_t))
        last_t=int(z['t'])
    g=m.geom(up,down,cu,cd); payout=up if winner=='UP' else down if winner=='DOWN' else 0.
    return {**g,'positiveDurationSec':pos_ms/1000.,'flags':flags,'suppressedShares':suppressed,'subtypes':subtypes,'pnl':payout-(cu+cd)}


def summarize(rows,mode):
    active=[r for r in rows if r[mode]['flags']>0]
    def med(vals): return median(vals) if vals else None
    return {
        'activeMarkets':len(active),
        'medianDeltaFinalFloor':med([r[mode]['floor']-r['BASELINE']['floor'] for r in active]),
        'meanDeltaFinalFloor':sum(r[mode]['floor']-r['BASELINE']['floor'] for r in active)/len(active) if active else None,
        'improved':sum(r[mode]['floor']>r['BASELINE']['floor']+1e-9 for r in active),
        'worsened':sum(r[mode]['floor']<r['BASELINE']['floor']-1e-9 for r in active),
        'medianDeltaPositiveDurationSec':med([r[mode]['positiveDurationSec']-r['BASELINE']['positiveDurationSec'] for r in active]),
        'medianDeltaCoverage':med([r[mode]['coverage']-r['BASELINE']['coverage'] for r in active]),
        'medianDeltaAbsNet':med([r[mode]['absnet']-r['BASELINE']['absnet'] for r in active]),
        'medianDeltaPnl':med([r[mode]['pnl']-r['BASELINE']['pnl'] for r in active]),
        'medianSuppressedShares':med([r[mode]['suppressedShares'] for r in active]),
    }


def main():
    meta,ev=m.load(m.N_MARKETS)
    modes=['FULL_MPQ','BASE_BREAK_ONLY','DILUTION_WHILE_SAFE_ONLY']
    all_rows=[]; blocks=[]
    for bi in range(0,len(meta),m.BLOCK_SIZE):
        block=meta[bi:bi+m.BLOCK_SIZE]; br=[]
        for mid,wend,winner in block:
            rec={'marketId':mid,'windowEndMs':wend,'winner':winner,'BASELINE':replay(ev.get(mid,[]),winner,'BASELINE')}
            for mode in modes: rec[mode]=replay(ev.get(mid,[]),winner,mode)
            br.append(rec); all_rows.append(rec)
        blocks.append({'blockIndex':bi//m.BLOCK_SIZE+1,**{mode:summarize(br,mode) for mode in modes}})
    subtype_events={'BASE_BREAK':0,'DILUTION_WHILE_SAFE':0}
    subtype_markets={'BASE_BREAK':0,'DILUTION_WHILE_SAFE':0}
    for r in all_rows:
        base=replay(ev.get(r['marketId'],[]),r['winner'],'BASELINE')
        # Subtype counts are path-fixed on baseline.
        counts=base['subtypes']
        for k in subtype_events:
            subtype_events[k]+=counts[k]
            subtype_markets[k]+=int(counts[k]>0)
    report={
        'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),
        'cohort':{'ordinaryMarkets':len(meta),'blocks':len(blocks),'sealed20260816':True},
        'semanticSplit':{
            'BASE_BREAK':'full MPQ flag and post-fill floor <= 0',
            'DILUTION_WHILE_SAFE':'full MPQ flag and post-fill floor > 0',
            'thresholdTuned':False
        },
        'subtypeEventsOnBaselinePath':subtype_events,'subtypeMarketsOnBaselinePath':subtype_markets,
        'combined':{mode:summarize(all_rows,mode) for mode in modes},
        'blocks':blocks,
        'guards':{'noEchtgeldTraining':True,'noLiveR3Change':True,'no8781Change':True,'winnerEvaluationOnly':True,'pathFixedCounterfactual':True}
    }
    out=ROOT/'data'/'research'/'r4_v0'/'hourly'/f"r4_marginal_pair_quality_semantic_ablation_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'subtypes':subtype_events,'combined':report['combined']},ensure_ascii=False))

if __name__=='__main__': main()
