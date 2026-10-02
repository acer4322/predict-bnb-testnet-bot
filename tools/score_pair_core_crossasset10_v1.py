"""Score immutable completed branches; winner labels never enter runtime selection."""
from pathlib import Path
import collections
import datetime
import hashlib
import importlib.util
import json
import statistics

BASE=Path('data/research/r4_v0/p0_provenance_v1')
RETURNS=Path('data/research/lan_worker_returns')
EPS=1e-9


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def stats(rows):
    rows=sorted(rows,key=lambda r:(r['windowStartMs'],r['marketId']))
    ps=[r['settledGross'] for r in rows if r['settledGross'] is not None]
    costs=sum(r['cost'] for r in rows)
    equity=peak=mdd=0.
    for p in ps:equity+=p;peak=max(peak,equity);mdd=max(mdd,peak-equity)
    return dict(markets=len(rows),knownOutcomes=len(ps),wins=sum(p>EPS for p in ps),losses=sum(p<-EPS for p in ps),zeros=sum(abs(p)<=EPS for p in ps),
        grossTotal=sum(ps) if len(ps)==len(rows) else None,medianGross=statistics.median(ps) if ps else None,
        worstGross=min(ps) if ps else None,bestGross=max(ps) if ps else None,
        chronologicalMaxDrawdown=mdd if len(ps)==len(rows) else None,leaveOneBestOut=sum(ps)-max(ps) if ps else None,
        winRateAllMarkets=sum(p>EPS for p in ps)/len(rows) if rows else None,
        cost=costs,fills=sum(r['fills'] for r in rows),submits=sum(r['submits'] for r in rows),alternations=sum(r['alternations'] for r in rows),
        traded=sum(r['fills']>0 for r in rows),takerMarkets=sum(r['takerExercised'] for r in rows),
        poolExercisedMarkets=sum(r['extraPassiveAdmissions']>0 for r in rows),
        minPathEndpoint=min(r['risk']['minEndpoint'] for r in rows),
        absNetIntegral=sum(r['risk']['absNetIntegral'] for r in rows),
        grossBreakEvenCostRate=sum(ps)/costs if ps and len(ps)==len(rows) and sum(ps)>0 and costs>0 else None,
        fullNetCost='UNRESOLVED')


def main():
    cohortp=BASE/'PAIR_CORE_CROSSASSET10_COHORT_V1_20260910.json'
    cohort=json.loads(cohortp.read_text(encoding='utf-8'))
    outcome_path=BASE/'PAIR_CORE_CROSSASSET10_OUTCOMES_POSTHOC_V1_20260910.json'
    labels=json.loads(outcome_path.read_text(encoding='utf-8'));winners={r['marketId']:r for r in labels['rows']}
    spec=importlib.util.spec_from_file_location('frozen_audit',Path('.lan_worker_v1/hft244_pair_crossasset10_20260910_v1/run_hft244_pair_crossasset10_worker_v1.py'))
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    sources=[];rows=[];comparisons=[];statuses=[]
    for asset in ('BTC','ETH'):
        p=RETURNS/f'hft244-pair-crossasset10-{asset.lower()}-20260910-v1/COMPACT.json'
        assert p.stat().st_size<2*1024**2;d=json.loads(p.read_text(encoding='utf-8-sig'))
        assert d['nativeSha256']==mod.NATIVE_SHA
        statuses.append(dict(asset=asset,verdict=d['verdict'],attemptedBE=d['attemptedBE'],error=d.get('error'),failedAt=d.get('failedAt')))
        sources.append(dict(path=p.as_posix(),sha256=sha(p),bytes=p.stat().st_size))
        expected={r['marketId'] for r in cohort['rows'] if r['asset']==asset}
        for r in d['rows']:
            assert r['marketId'] in expected and r['asset']==asset
            audit=mod.independent_audit(r['receipts'],r['native'],r['UP'],r['DOWN'],r['cost'])
            w=winners.get(r['marketId'],{}).get('winner')
            assert w in ('UP','DOWN',None)
            row={k:v for k,v in r.items() if k not in ('receipts','native','anatomy','mark','ready','events','directReceipts')}
            row.update(winnerPosthoc=w,settledGross=r[w] if w else None,selected=r['mark'] is not None,independentAudit=audit,
                       receiptsSha256=mod.sig(r['receipts']),anatomy=r['anatomy'])
            rows.append(row)
        comparisons.extend(d['comparisons'])
    assert len({(r['marketId'],r['arm']) for r in rows})==len(rows)
    grouped={a:{arm:stats([r for r in rows if r['arm']==arm and (a=='COMBINED_DESCRIPTIVE' or r['asset']==a)]) for arm in ('A','C','T','X')} for a in ('BTC','ETH','COMBINED_DESCRIPTIVE')}
    by={(r['marketId'],r['arm']):r for r in rows}
    for c in comparisons:
        mid=c['marketId'];c['winnerPosthoc']=winners.get(mid,{}).get('winner')
        for key,left,right in [('T_minus_C','T','C'),('X_minus_T','X','T'),('C_minus_A','C','A'),('T_minus_A','T','A'),('X_minus_A','X','A')]:
            l=by[mid,left];r=by[mid,right];c[key]['settledGross']=(l['settledGross']-r['settledGross']) if l['settledGross'] is not None and r['settledGross'] is not None else None
        u,v=c['T_minus_C']['UP'],c['T_minus_C']['DOWN']
        c['fixedTapeTerminalGeometry']='UNCHANGED' if abs(u)<EPS and abs(v)<EPS else 'BOTH_WORSE' if u<-EPS and v<-EPS else 'BOTH_BETTER' if u>EPS and v>EPS else 'TRADEOFF_OR_WEAK_DOMINANCE'
    utc8=datetime.timezone(datetime.timedelta(hours=8))
    periods={a:dict(start=datetime.datetime.fromtimestamp(min(r['windowStartMs'] for r in cohort['rows'] if r['asset']==a)/1000,utc8).isoformat(),end=datetime.datetime.fromtimestamp(max(r['windowEndMs'] for r in cohort['rows'] if r['asset']==a)/1000,utc8).isoformat()) for a in ('BTC','ETH')}
    complete=len(rows)==40 and len(comparisons)==10 and all(s['verdict']=='CROSSASSET_STRATUM_CORRECTNESS_PASS_NO_PROMOTION' for s in statuses)
    out=dict(version='PAIR_CORE_CROSSASSET10_SCORE_V1',complete=complete,statuses=statuses,sourceFiles=sources,cohortSha256=sha(cohortp),
        outcomesSha256=sha(outcome_path),periods=periods,assetCalendarConfounded=True,rows=rows,comparisons=comparisons,summary=grouped,
        audit=dict(nativeReceipts=sum(r['independentAudit']['receiptCount'] for r in rows),partialReceipts=sum(r['independentAudit']['partialReceipts'] for r in rows),
            maxError=max(r['independentAudit']['maxError'] for r in rows),frozenNativeSha256=mod.NATIVE_SHA),
        promotion=False,fullNetCost='UNRESOLVED',additionalHFTForScoring=0,training=0)
    p=BASE/'PAIR_CORE_CROSSASSET10_SCORE_V1_20260910.json';assert not p.exists(),'preserve score'
    p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:out[k] for k in ('complete','statuses','periods','summary','audit')},ensure_ascii=False))
    print('COMPARISONS',json.dumps(comparisons,ensure_ascii=False))
    print('SCORE',p.as_posix(),sha(p))


if __name__=='__main__':main()
