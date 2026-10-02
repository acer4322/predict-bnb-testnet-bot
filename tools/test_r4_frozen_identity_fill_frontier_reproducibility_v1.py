from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_queue_option_counterfactual_v1 import run_recovery

SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_option_counterfactual_v1.json'
QPROG=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_progress_proxy_v0.json'
CACHE=ROOT/'data/research/r4_v0/hourly/r4_frozen_identity_fill_frontier_reproducibility_v1_cache.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_frozen_identity_fill_frontier_reproducibility_v1.json'
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_FROZEN_IDENTITY_FILL_FRONTIER_REPRODUCIBILITY_V1_20260827_1738'


def eligible_rows():
    src=json.loads(SRC.read_text(encoding='utf-8'))
    qp=json.loads(QPROG.read_text(encoding='utf-8'))
    qmap={int(r['marketId']):r for r in qp.get('rows',[])}
    rows=[]
    for r in src.get('rows',[]):
        if not r.get('eligible') or not r.get('queueFeatures'): continue
        mid=int(r['marketId'])
        if mid not in qmap: continue
        ev=r.get('reinsertEvent') or {}
        if ev.get('oldOrderNum') is None or r.get('candidateAtMs') is None: continue
        rem=float((r.get('queueFeatures') or {}).get('remainingQty') or 0.0)
        if rem<=1e-9: continue
        rows.append({
            'marketId':mid,
            'candidateAtMs':int(r['candidateAtMs']),
            'orderNum':int(ev['oldOrderNum']),
            'side':str(r.get('side') or ''),
            'remainingQty':rem,
            'orderAgeMs':float((r.get('queueFeatures') or {}).get('orderAgeMs') or 0.0),
            'quoteOffsetTicks':float((r.get('queueFeatures') or {}).get('quoteOffsetTicks') or 0.0),
            'partialFillRatio':float((r.get('queueFeatures') or {}).get('partialFillRatio') or 0.0),
            'recoveryDeficit':float((r.get('queueFeatures') or {}).get('recoveryDeficit') or 0.0),
            'secondsLeft':float((r.get('queueFeatures') or {}).get('secondsLeft') or 0.0),
            **{k:qmap[mid].get(k) for k in ['initialVisibleDepth','currentVisibleDepth','depletionOverInitial','netDepletionOverInitial']}
        })
    return sorted(rows,key=lambda z:z['candidateAtMs'])


def frontier_for(r):
    rr=run_recovery(int(r['marketId']),queue_reinsert=False)
    fills=[]
    for ev in rr.get('fillLog') or []:
        if str(ev.get('role'))!='MAKER': continue
        if int(ev.get('orderNum') or -1)!=int(r['orderNum']): continue
        t=int(ev.get('eventMs') or 0)
        if t<=int(r['candidateAtMs']): continue
        fills.append((t,float(ev.get('shares') or 0.0)))
    def sh(h):
        return float(sum(q for t,q in fills if t<=int(r['candidateAtMs'])+h))
    f1,f3,f5=sh(1000),sh(3000),sh(5000)
    rem=float(r['remainingQty'])
    return {
        **r,
        'fillShares1s':f1,'fillShares3s':f3,'fillShares5s':f5,
        'realizedFraction5s':min(1.0,max(0.0,f5/rem)),
        'futureFillEvents5s':int(sum(1 for t,q in fills if t<=int(r['candidateAtMs'])+5000)),
        'replayCandidateAtMs':rr.get('candidateAtMs'),
        'replayCandidateOriginalChildNum':rr.get('candidateOriginalChildNum'),
        'replayCandidateDetectorMatchedFrozenKey':bool(rr.get('candidateAtMs')==r['candidateAtMs'] and rr.get('candidateOriginalChildNum')==r['orderNum'])
    }


def load_cache():
    if not CACHE.exists(): return {'version':'R4_FROZEN_IDENTITY_FILL_FRONTIER_CACHE_V1','rows':[]}
    return json.loads(CACHE.read_text(encoding='utf-8'))

def save_cache(d):
    CACHE.write_text(json.dumps(d,indent=2),encoding='utf-8')

def key(r): return (int(r['marketId']),int(r['candidateAtMs']),int(r['orderNum']))

def monotonic(r):
    a,b,c,rem=[float(r[x]) for x in ['fillShares1s','fillShares3s','fillShares5s','remainingQty']]
    return -1e-9<=a<=b+1e-9 and b<=c+1e-9 and c<=rem+1e-9

def exact_frontier(a,b):
    return key(a)==key(b) and all(abs(float(a[k])-float(b[k]))<=1e-9 for k in ['fillShares1s','fillShares3s','fillShares5s','realizedFraction5s'])


def finalize(rows,eligible):
    rows=sorted(rows,key=lambda z:z['candidateAtMs'])
    coverage=len(rows)/max(1,len(eligible))
    mono=sum(monotonic(r) for r in rows)
    positive=sum(float(r['fillShares5s'])>1e-12 for r in rows)
    detector_match=sum(bool(r.get('replayCandidateDetectorMatchedFrozenKey')) for r in rows)
    audit=[]
    for r in rows[:5]:
        rr=frontier_for(r)
        audit.append({'marketId':r['marketId'],'exact':exact_frontier(r,rr),'first':{k:r[k] for k in ['fillShares1s','fillShares3s','fillShares5s']},'second':{k:rr[k] for k in ['fillShares1s','fillShares3s','fillShares5s']}})
    audit_exact=bool(len(audit)==5 and all(x['exact'] for x in audit))
    keep=bool(coverage>=.90 and mono==len(rows) and audit_exact and positive>=3)
    if coverage<.90:
        status='BLOCKED'
    elif mono<len(rows) or not audit_exact:
        status='TESTED_REJECTED'
    elif positive<3:
        status='TESTED_INCONCLUSIVE'
    else:
        status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
    rep={
      'version':'R4_FROZEN_IDENTITY_FILL_FRONTIER_REPRODUCIBILITY_V1','testId':TEST_ID,'createdAt':datetime.now(TZ).isoformat(),'status':status,
      'semanticNovelty':'Tests deterministic offline label provenance, not queue-option action quality or a continuous prediction model: freeze the historical candidate checkpoint/order identity from the source artifact and read that exact resting child future receipt-clock confirmed fills even if the current replay candidate detector no longer reproduces the checkpoint.',
      'retestBasis':'MATERIAL_DATA_PIPELINE_FIX',
      'layerAssignment':{'receiptClockFillEvents':'EXECUTION_INFORMATION','frozenOrderCheckpointKey':'EXECUTION_DATA_PROVENANCE','outputFillFrontier':'OFFLINE_LABEL_TABLE_ONLY','authority':'NOT_ACTION_AUTHORITY'},
      'cohort':{'eligibleSourceRows':len(eligible),'materializedRows':len(rows),'coverage':coverage,'positive5sRows':positive,'candidateDetectorMatchedFrozenKeyRows':detector_match,'special20260816Sealed':True,'echtgeldTraining':False,'dreamFill':False},
      'frontierAudit':{'monotonicRows':mono,'allMonotonic':mono==len(rows),'fixedFirst5Replay':audit,'exactReplay5of5':audit_exact},
      'decisionRule':{'coverageRequired':.90,'positive5sRowsRequired':3,'monotonicRequired':True,'exactReplayAuditRequired':'5/5'},
      'decision':status,
      'interpretation':('Deterministic frozen-identity fill-frontier labels are now materializable and have enough nonzero 5s support to reopen the separately preregistered continuous remaining-option belief in a later cycle.' if status=='TESTED_KEEP_SIGNAL' else 'Frozen-identity frontier plumbing was evaluated under the preregistered data-provenance gate; no action authority follows.'),
      'rows':rows,
      'guards':{'noModelFit':True,'futureFillLabelsOfflineOnly':True,'noActionAuthority':True,'noThresholdSweep':True,'no8781Change':True,'noLiveR3Change':True,'noNewEchtgeld':True}
    }
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'cohort':rep['cohort'],'frontierAudit':rep['frontierAudit']},ensure_ascii=False))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--batch',type=int,default=8); ap.add_argument('--finalize',action='store_true'); args=ap.parse_args()
    eligible=eligible_rows(); cache=load_cache(); done={key(r) for r in cache.get('rows',[])}
    if not args.finalize:
        todo=[r for r in eligible if key(r) not in done][:max(1,args.batch)]
        for i,r in enumerate(todo,1):
            x=frontier_for(r); cache.setdefault('rows',[]).append(x); save_cache(cache)
            print(json.dumps({'progress':i,'marketId':r['marketId'],'orderNum':r['orderNum'],'fillShares5s':x['fillShares5s'],'detectorMatched':x['replayCandidateDetectorMatchedFrozenKey']},ensure_ascii=False),flush=True)
        print(json.dumps({'cachedRows':len(cache.get('rows',[])),'eligibleRows':len(eligible),'remaining':len(eligible)-len(cache.get('rows',[]))},ensure_ascii=False))
    else:
        finalize(cache.get('rows',[]),eligible)

if __name__=='__main__': main()
