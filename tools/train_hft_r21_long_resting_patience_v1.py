from __future__ import annotations

import argparse, json, lzma, math, sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
HFT_DB = ROOT / 'data/hft_forward_paper_v1.db'
REPORT_DIR = ROOT / 'data/hft_forward_paper_v1/markets'
LANDMARKS_MS = [15_000, 30_000, 45_000, 60_000, 90_000, 120_000]


def finite(v: Any, default=0.0) -> float:
    try:
        x=float(v)
    except Exception:
        return float(default)
    return x if math.isfinite(x) else float(default)


def load_json_xz(path: Path) -> dict[str, Any]:
    with lzma.open(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def collect_market(market_id: int, report_path: str) -> list[dict[str, Any]]:
    # DB stores Windows absolute paths. Resolve by canonical local archive filename.
    path=REPORT_DIR / f'{market_id}_r2_hft_closed_loop_v1.json.xz'
    if not path.exists():
        candidate=Path(report_path)
        if candidate.exists(): path=candidate
    if not path.exists(): return []
    r=load_json_xz(path)
    order_meta=r.get('orderMeta') or {}
    fills=r.get('makerFillEvents') or []
    states=r.get('orderStateRows') or []
    decision_rows=r.get('decisionRows') or []
    clocks=[int(x.get('checkpointMs') or x.get('decisionMs') or 0) for x in states+decision_rows]
    end_ms=max([x for x in clocks if x>0], default=0)
    by_order={}
    for f in fills:
        oid=str(f.get('orderId') or '')
        at=int(f.get('atMs') or f.get('observedAtMs') or 0)
        if oid and at>0: by_order.setdefault(oid,[]).append(at)
    rows=[]
    for oid,meta in order_meta.items():
        sub=int(meta.get('placedAtMs') or 0)
        if not oid or sub<=0: continue
        fts=sorted(by_order.get(str(oid),[])); first=fts[0] if fts else None
        censor=max(end_ms, sub)
        side=str(meta.get('side') or ''); price=finite(meta.get('price'))
        for age in LANDMARKS_MS:
            t=sub+age
            if t>=censor: continue
            if first is not None and first<=t: continue
            horizon_end=min(censor,t+30_000)
            fill30=first is not None and t < first <= horizon_end
            eventual=first is not None and first>t
            rows.append({'marketId':market_id,'orderId':str(oid),'side':side,'price':price,'submitMs':sub,'landmarkAgeMs':age,'atMs':t,'fillWithin30s':bool(fill30),'eventualLateFill':bool(eventual),'timeToFillAfterLandmarkMs':None if first is None or first<=t else first-t,'censoredAtMs':censor})
    return rows


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--limit',type=int,default=120); ap.add_argument('--output',default='hft_r21_long_resting_patience_v1_report.json'); args=ap.parse_args()
    con=sqlite3.connect(HFT_DB); con.row_factory=sqlite3.Row
    runs=con.execute("select market_id,report_archive_path,completed_at_ms from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by completed_at_ms desc limit ?",(args.limit,)).fetchall(); con.close()
    rows=[]; markets=[]
    for rr in runs:
        m=int(rr['market_id']); got=collect_market(m,str(rr['report_archive_path'] or ''))
        if got: markets.append(m); rows.extend(got)
    stats=[]
    for age in LANDMARKS_MS:
        g=[x for x in rows if x['landmarkAgeMs']==age]
        n=len(g); f30=sum(x['fillWithin30s'] for x in g); ev=sum(x['eventualLateFill'] for x in g)
        times=sorted(x['timeToFillAfterLandmarkMs'] for x in g if x['timeToFillAfterLandmarkMs'] is not None)
        stats.append({'ageSec':age/1000,'atRiskOrders':n,'fillWithinNext30s':f30,'fillWithinNext30sRate':f30/n if n else None,'eventualLateFill':ev,'eventualLateFillRate':ev/n if n else None,'medianAdditionalWaitSec':(times[len(times)//2]/1000 if times else None),'maxAdditionalWaitSec':(max(times)/1000 if times else None)})
    candidates=[s for s in stats if s['atRiskOrders']>=10 and s['fillWithinNext30sRate'] is not None and s['fillWithinNext30sRate']<=0.10 and s['eventualLateFillRate']<=0.20]
    payload={'version':'HFT_R21_LONG_RESTING_PATIENCE_V1','researchOnly':True,'policyPromotionEligible':False,'source':'existing HftBacktest + Predict Tape V1 R2 closed-loop reports','markets':markets,'marketCount':len(markets),'landmarks':stats,'candidateReevaluationAgeSec':None if not candidates else candidates[0]['ageSec'],'rows':rows,'decision':'DATA_READY_FOR_MATCHED_REEVALUATION_CURRICULUM' if rows else 'NEED_DATA','note':'Patience support only; no cancellation/reprice and no Frozen R2 mutation.'}
    out=OUT/args.output; out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:payload[k] for k in ['version','marketCount','landmarks','candidateReevaluationAgeSec','decision']},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
