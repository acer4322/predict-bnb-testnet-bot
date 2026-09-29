from __future__ import annotations
import json, math, sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_queue_option_counterfactual_v1 import run_recovery

SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_option_counterfactual_v1.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_stable_economic_intent_identity_v1.json'
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_STABLE_ECONOMIC_INTENT_IDENTITY_V1_20260827_1836'


def f(x):
    try:
        y=float(x)
        return y if math.isfinite(y) else None
    except Exception:
        return None


def canonical_from_source(r):
    q=r.get('queueFeatures') or {}
    cand=r.get('candidateAtMs'); age=f(q.get('orderAgeMs')); px=f(q.get('orderPrice'))
    rem=f(q.get('remainingQty')); cum=f(q.get('cumExecQty'))
    if cand is None or age is None or px is None or rem is None or cum is None:
        return None
    return {
        'marketId':int(r['marketId']),
        'side':str(r.get('side') or '').upper(),
        'submittedAtMs':int(round(int(cand)-age)),
        'orderPrice':round(px,8),
        'originalRequestedQty':round(cum+rem,8),
    }


def canonical_from_replay(rr):
    q=rr.get('candidateQueueFeatures') or {}
    cand=rr.get('candidateAtMs'); age=f(q.get('orderAgeMs')); px=f(q.get('orderPrice'))
    rem=f(q.get('remainingQty')); cum=f(q.get('cumExecQty'))
    side=rr.get('candidateRecoverySide')
    if cand is None or age is None or px is None or rem is None or cum is None or side is None:
        return None
    return {
        'marketId':int(rr['marketId']),
        'side':str(side).upper(),
        'submittedAtMs':int(round(int(cand)-age)),
        'orderPrice':round(px,8),
        'originalRequestedQty':round(cum+rem,8),
    }


def exact(a,b):
    return a is not None and b is not None and a==b


def main():
    src=json.loads(SRC.read_text(encoding='utf-8'))
    eligible=[r for r in src.get('rows',[]) if r.get('eligible') and r.get('queueFeatures')]
    source=[]
    for r in eligible:
        k=canonical_from_source(r)
        source.append({'marketId':int(r['marketId']),'sourceKey':k,'sourceOrderNum':(r.get('reinsertEvent') or {}).get('oldOrderNum')})
    complete=[x for x in source if x['sourceKey'] is not None]
    rows=[]
    for i,x in enumerate(complete,1):
        mid=x['marketId']
        rr=run_recovery(mid,queue_reinsert=False)
        rk=canonical_from_replay(rr)
        rows.append({
            'marketId':mid,
            'sourceKey':x['sourceKey'],
            'replayKey':rk,
            'exactMatch':exact(x['sourceKey'],rk),
            'sourceOrderNum':x['sourceOrderNum'],
            'replayOrderNum':rr.get('candidateOriginalChildNum'),
            'numericOrderNumMatch':bool(x['sourceOrderNum'] is not None and rr.get('candidateOriginalChildNum') is not None and int(x['sourceOrderNum'])==int(rr.get('candidateOriginalChildNum'))),
            'replayCandidateAtMs':rr.get('candidateAtMs')
        })
        print(json.dumps({'progress':i,'marketId':mid,'exactMatch':rows[-1]['exactMatch'],'numericOrderNumMatch':rows[-1]['numericOrderNumMatch']},ensure_ascii=False),flush=True)
    replay_complete=[r for r in rows if r['replayKey'] is not None]
    matches=[r for r in replay_complete if r['exactMatch']]
    repeat=[]
    for r in rows[:5]:
        rr=run_recovery(int(r['marketId']),queue_reinsert=False)
        k2=canonical_from_replay(rr)
        repeat.append({'marketId':r['marketId'],'exact':exact(r['replayKey'],k2),'first':r['replayKey'],'second':k2})
    repeat5=bool(len(repeat)==5 and all(z['exact'] for z in repeat))
    nsrc=len(source); ncomplete=len(complete); nrep=len(replay_complete)
    source_rate=ncomplete/max(1,nsrc); replay_cov=nrep/max(1,ncomplete); match_rate=len(matches)/max(1,nrep)
    support_ok=nsrc>=20 and source_rate>=.90 and replay_cov>=.80 and repeat5
    if support_ok and match_rate>=.80: status='TESTED_KEEP_SIGNAL'
    elif support_ok and match_rate<.50: status='TESTED_REJECTED'
    else: status='TESTED_INCONCLUSIVE'
    rep={
      'version':'R4_STABLE_ECONOMIC_INTENT_IDENTITY_V1','testId':TEST_ID,'createdAt':datetime.now(TZ).isoformat(),'status':status,
      'semanticNovelty':'Tests orderNum-independent economic-intent identity reconstructability across the historical queue-option source artifact and current receipt-clock replay. It does not construct future-fill labels and does not fit/reopen the continuous remaining-option belief.',
      'layerAssignment':{'sourceAndReplayOrderLifecycle':'EXECUTION_INFORMATION','canonicalEconomicIntentKey':'EXECUTION_DATA_PROVENANCE','output':'IDENTITY_RECONSTRUCTABILITY_DIAGNOSTIC','authority':'NOT_ACTION_AUTHORITY'},
      'canonicalKey':['marketId','side','submittedAtMs','orderPrice','originalRequestedQty'],
      'cohort':{'eligibleSourceRows':nsrc,'sourceCompleteRows':ncomplete,'sourceCompleteRate':source_rate,'currentReplayCompleteRows':nrep,'currentReplayCoverage':replay_cov,'special20260816Sealed':True,'echtgeldTraining':False,'dreamFill':False},
      'primaryResult':{'exactCanonicalMatches':len(matches),'exactCanonicalMatchRate':match_rate,'numericOrderNumMatches':sum(r['numericOrderNumMatch'] for r in rows),'numericOrderNumMatchRate':sum(r['numericOrderNumMatch'] for r in rows)/max(1,len(rows)),'fixedFirst5Repeat':repeat,'repeatExact5of5':repeat5},
      'decisionRule':{'minimumSourceRows':20,'sourceCompleteKeyRateRequired':.90,'currentReplayCoverageRequired':.80,'exactCanonicalMatchRateForKeep':.80,'rejectBelowExactMatchRate':.50,'fixedRepeatAuditRequired':'5/5'},
      'decision':status,
      'rows':rows,
      'guards':{'noFutureFillLabelConstruction':True,'noModelFit':True,'noActionAuthority':True,'noThresholdSweep':True,'no8781Change':True,'noLiveR3Change':True,'noNewEchtgeld':True}
    }
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'cohort':rep['cohort'],'primaryResult':{k:v for k,v in rep['primaryResult'].items() if k!='fixedFirst5Repeat'}},ensure_ascii=False))

if __name__=='__main__': main()
