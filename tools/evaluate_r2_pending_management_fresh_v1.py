from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import run_market
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners, realized_pnl, stats, finite

OUT = ROOT / 'data' / 'research' / 'execution_aware_fill_lifecycle_v0'
SPLIT = OUT / 'r2_pending_management_fresh_split_v1.json'
ART = OUT / 'execution_aware_pending_management_v0.joblib'


def row_for(mid: int, base: dict[str, Any], skill: dict[str, Any], winner: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    br = base['studentRollout']; sr = skill['studentRollout']
    bp = br.get('finalPortfolio') or {}; sp = sr.get('finalPortfolio') or {}
    b_pnl = realized_pnl(br, winner); s_pnl = realized_pnl(sr, winner)
    idx = {(int(x.get('checkpointMs') or -1), str(x.get('orderId') or '')): x for x in skill.get('orderStateRows') or []}
    vetoes = []
    for v in skill.get('pendingSkillVetoEvents') or []:
        st = idx.get((int(v['atMs']), str(v['existingOrderId']))) or {}
        vetoes.append({
            'marketId': mid, **v,
            'hftFill1sAfterVeto': int(st.get('labelAnyFill1s') or 0),
            'hftFill3sAfterVeto': int(st.get('labelAnyFill3s') or 0),
            'hftFill5sAfterVeto': int(st.get('labelAnyFill5s') or 0),
            'hftEventualFillAfterVeto': int(float(st.get('eventualAdditionalFillShares') or 0.0) > 1e-9),
            'futureFirstFillDelayMs': st.get('futureFirstFillDelayMs'),
        })
    return ({
        'marketId': mid, 'winner': winner,
        'baselineMakerPlacements': int(br.get('makerPlacements') or 0), 'skillMakerPlacements': int(sr.get('makerPlacements') or 0),
        'deltaMakerPlacements': int(sr.get('makerPlacements') or 0)-int(br.get('makerPlacements') or 0),
        'baselineMakerFilledShares': float(br.get('makerFilledShares') or 0), 'skillMakerFilledShares': float(sr.get('makerFilledShares') or 0),
        'deltaMakerFilledShares': float(sr.get('makerFilledShares') or 0)-float(br.get('makerFilledShares') or 0),
        'baselineTakerFills': int(br.get('takerFills') or 0), 'skillTakerFills': int(sr.get('takerFills') or 0),
        'deltaTakerFills': int(sr.get('takerFills') or 0)-int(br.get('takerFills') or 0),
        'vetoes': len(vetoes), 'uniqueOptionOrders': int(sr.get('pendingOptionUniqueOrders') or 0),
        'baselineCombinedPairedCoverage': finite(bp.get('combined_paired_coverage')), 'skillCombinedPairedCoverage': finite(sp.get('combined_paired_coverage')),
        'deltaCombinedPairedCoverage': float(sp.get('combined_paired_coverage') or 0)-float(bp.get('combined_paired_coverage') or 0),
        'baselineMakerPairedCoverage': finite(bp.get('maker_paired_coverage')), 'skillMakerPairedCoverage': finite(sp.get('maker_paired_coverage')),
        'deltaMakerPairedCoverage': float(sp.get('maker_paired_coverage') or 0)-float(bp.get('maker_paired_coverage') or 0),
        'baselineCombinedAbsNet': finite(bp.get('combined_abs_net')), 'skillCombinedAbsNet': finite(sp.get('combined_abs_net')),
        'deltaCombinedAbsNet': float(sp.get('combined_abs_net') or 0)-float(bp.get('combined_abs_net') or 0),
        'baselineWorstCaseFloor': finite(bp.get('worst_case_floor')), 'skillWorstCaseFloor': finite(sp.get('worst_case_floor')),
        'deltaWorstCaseFloor': float(sp.get('worst_case_floor') or 0)-float(bp.get('worst_case_floor') or 0),
        'baselineCapital': float(br.get('makerCostUsdt') or 0)+float(br.get('takerCostUsdt') or 0)+float(br.get('takerFeesUsdt') or 0),
        'skillCapital': float(sr.get('makerCostUsdt') or 0)+float(sr.get('takerCostUsdt') or 0)+float(sr.get('takerFeesUsdt') or 0),
        'baselineRealizedPnl': b_pnl, 'skillRealizedPnl': s_pnl,
        'deltaRealizedPnl': (s_pnl-b_pnl) if s_pnl is not None and b_pnl is not None else None,
    }, vetoes)


def aggregate(rows: list[dict[str, Any]], vetoes: list[dict[str, Any]], split_name: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
    n_v = len(vetoes)
    by_order = defaultdict(list)
    for v in vetoes:
        by_order[(v['marketId'],v['existingOrderId'])].append(v)
    chains = []
    for _, xs in by_order.items():
        xs=sorted(xs,key=lambda x:int(x['atMs']))
        chains.append({'n':len(xs),'spanMs':int(xs[-1]['atMs'])-int(xs[0]['atMs']),'maxOptionAgeMs':max(int(x.get('optionAgeMs') or 0) for x in xs)})
    pnl_rows=[r for r in rows if r.get('deltaRealizedPnl') is not None]
    def sm(k: str) -> float: return float(sum(float(r.get(k) or 0) for r in rows))
    return {
        'split': split_name, 'markets': len(rows), 'errors': errors,
        'vetoes': n_v, 'marketsWithVeto': sum(int(r['vetoes'])>0 for r in rows), 'uniqueOptionOrders': len(by_order),
        'maxVetoesPerOrder': max((x['n'] for x in chains), default=0), 'maxVetoSpanMs': max((x['spanMs'] for x in chains), default=0),
        'maxOptionAgeMsObserved': max((x['maxOptionAgeMs'] for x in chains), default=0),
        'vetoHftFill1sRate': sum(int(v['hftFill1sAfterVeto']) for v in vetoes)/n_v if n_v else None,
        'vetoHftFill3sRate': sum(int(v['hftFill3sAfterVeto']) for v in vetoes)/n_v if n_v else None,
        'vetoHftFill5sRate': sum(int(v['hftFill5sAfterVeto']) for v in vetoes)/n_v if n_v else None,
        'vetoHftEventualFillRate': sum(int(v['hftEventualFillAfterVeto']) for v in vetoes)/n_v if n_v else None,
        'baselineMakerPlacements': sm('baselineMakerPlacements'), 'skillMakerPlacements': sm('skillMakerPlacements'),
        'baselineMakerFilledShares': sm('baselineMakerFilledShares'), 'skillMakerFilledShares': sm('skillMakerFilledShares'),
        'baselineTakerFills': sm('baselineTakerFills'), 'skillTakerFills': sm('skillTakerFills'),
        'baselineCapital': sm('baselineCapital'), 'skillCapital': sm('skillCapital'),
        'baselineRealizedPnl': sum(float(r['baselineRealizedPnl']) for r in pnl_rows), 'skillRealizedPnl': sum(float(r['skillRealizedPnl']) for r in pnl_rows),
        'deltaRealizedPnl': sum(float(r['deltaRealizedPnl']) for r in pnl_rows), 'pnlMarkets':len(pnl_rows),
        'betterPnlMarkets':sum(float(r['deltaRealizedPnl'])>1e-9 for r in pnl_rows),'worsePnlMarkets':sum(float(r['deltaRealizedPnl'])<-1e-9 for r in pnl_rows),'samePnlMarkets':sum(abs(float(r['deltaRealizedPnl']))<=1e-9 for r in pnl_rows),
        'deltaMakerFilledShares': stats([r['deltaMakerFilledShares'] for r in rows]),
        'deltaCombinedPairedCoverage': stats([r['deltaCombinedPairedCoverage'] for r in rows]),
        'deltaCombinedAbsNet': stats([r['deltaCombinedAbsNet'] for r in rows]),
        'deltaWorstCaseFloor': stats([r['deltaWorstCaseFloor'] for r in rows]),
        'deltaRealizedPnlDistribution':stats([float(r['deltaRealizedPnl']) for r in pnl_rows]),
    }


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--split',choices=['development','holdout'],default='development'); ap.add_argument('--start',type=int,default=0); ap.add_argument('--count',type=int,default=10); ap.add_argument('--aggregate-only',action='store_true'); a=ap.parse_args()
    contract=json.loads(SPLIT.read_text(encoding='utf-8'))
    key='developmentMarkets' if a.split=='development' else 'sealedHoldoutMarkets'
    ids=[int(x) for x in contract[key]]
    batch_ids=ids[a.start:a.start+a.count]
    win=winners(ids)
    batch_path=OUT/f'r2_pending_fresh_{a.split}_i{a.start}_n{len(batch_ids)}_v1.json'
    if not a.aggregate_only:
        rows=[]; vetoes=[]; errors=[]
        for i,mid in enumerate(batch_ids,1):
            try:
                base=run_market(mid)
                skill=run_market(mid,pending_management_artifact=ART,pending_option_horizon_ms=5000)
                (OUT/f'r2_fresh_baseline_market{mid}_v1.json').write_text(json.dumps(base,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
                (OUT/f'r2_fresh_pending_skill_market{mid}_v1.json').write_text(json.dumps(skill,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
                row,vs=row_for(mid,base,skill,win.get(mid)); rows.append(row); vetoes.extend(vs)
                print(json.dumps({'progress':i,'marketId':mid,'vetoes':row['vetoes'],'dMakerShares':row['deltaMakerFilledShares'],'dCoverage':row['deltaCombinedPairedCoverage'],'dPnl':row['deltaRealizedPnl']},ensure_ascii=False),flush=True)
            except Exception as exc:
                errors.append({'marketId':mid,'error':f'{type(exc).__name__}: {exc}'})
                print(json.dumps({'progress':i,'marketId':mid,'error':errors[-1]['error']},ensure_ascii=False),flush=True)
        batch={'version':'R2_PENDING_MANAGEMENT_FRESH_V1','split':a.split,'start':a.start,'ids':batch_ids,'rows':rows,'vetoes':vetoes,'errors':errors}
        batch_path.write_text(json.dumps(batch,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
        print(json.dumps({'ok':True,'batch':str(batch_path),'summary':aggregate(rows,vetoes,a.split,errors)},ensure_ascii=False,allow_nan=True))
        return 0
    rows=[]; vetoes=[]; errors=[]
    for p in sorted(OUT.glob(f'r2_pending_fresh_{a.split}_i*_n*_v1.json')):
        z=json.loads(p.read_text(encoding='utf-8')); rows.extend(z.get('rows') or []); vetoes.extend(z.get('vetoes') or []); errors.extend(z.get('errors') or [])
    # Keep only contract members and deduplicate in case a batch was rerun.
    by_mid={int(r['marketId']):r for r in rows if int(r['marketId']) in set(ids)}
    rows=[by_mid[m] for m in ids if m in by_mid]
    seen=set(); vv=[]
    for v in vetoes:
        k=(int(v['marketId']),int(v['atMs']),str(v['existingOrderId']),str(v['attemptReason']))
        if int(v['marketId']) in set(ids) and k not in seen: seen.add(k); vv.append(v)
    rep={'reportVersion':'R2_PENDING_MANAGEMENT_FRESH_V1','researchOnly':True,'liveTradingChanges':False,'studentScale':'PRE_CAP100_ORIGINAL_R2','splitContract':str(SPLIT),'split':a.split,'policy':{'artifact':str(ART),'naturalThreshold':0.5,'optionHorizonMs':5000,'sameOrderRearm':False,'dreamFillAllowed':False,'targetRuntimeInput':False},'aggregate':aggregate(rows,vv,a.split,errors),'rows':rows,'vetoAudit':vv,'guards':['No threshold or PnL sweep.','Settlement winner/PnL is post-hoc evaluation only.','Holdout split must not be run before development semantics are accepted.']}
    out=OUT/f'r2_pending_management_fresh_{a.split}_v1.json'; out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    pd.DataFrame(rows).to_csv(OUT/f'r2_pending_management_fresh_{a.split}_markets_v1.csv',index=False)
    print(json.dumps({'ok':True,'report':str(out),'aggregate':rep['aggregate']},ensure_ascii=False,allow_nan=True))
    return 0

if __name__=='__main__': raise SystemExit(main())
