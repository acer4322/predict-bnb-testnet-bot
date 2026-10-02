from __future__ import annotations

import json, math, sys, warnings
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as mod
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_r2_execution_candidate_v2_frozen as candidate
from tools import r2_execution_graduation_exam_steward_v1 as legacy_helpers
from tools.strategy_input_snapshot_replay_v1 import assess_strategy_input_snapshots

D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FREEZE=D/'r2_execution_graduation_candidate_freeze_v2.json'
REGISTRY=D/'r2_execution_graduation_exam_registry_v2.json'
EPS=1e-9


def verify_candidate_hashes(freeze:dict[str,Any])->dict[str,Any]:
    bad=[]
    for rel,expected in freeze['hashes'].items():
        p=ROOT/rel
        got=legacy_helpers.sha256(p) if p.exists() else None
        if got!=expected: bad.append({'path':rel,'expected':expected,'got':got})
    return {'ok':not bad,'filesChecked':len(freeze['hashes']),'bad':bad}



def archive_markets_after_true_open(freeze_ms:int):
    import sqlite3
    db=ROOT/'data/strategy_input_snapshot_archive_v1.db'
    con=sqlite3.connect(db);con.row_factory=sqlite3.Row
    try:
        rows=con.execute("""select market_id,count(*) n,min(sampled_at_ms) first_ms,max(sampled_at_ms) last_ms
            from strategy_input_snapshots_v1
            where controller_version=?
            group by market_id
            having min(sampled_at_ms)>?
            order by first_ms,market_id""",(mod.VERSION,int(freeze_ms))).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]

def quality_check_without_answer(mid:int)->dict[str,Any]:
    iq=assess_strategy_input_snapshots(mid,mod.VERSION)
    if not bool(iq.get('complete')):
        tail=legacy_helpers.terminal_one_sided_tail_proof(mid,iq)
        if not bool(tail.get('proven')):
            return {'eligible':False,'stage':'STRATEGY_INPUT','input':iq,'terminalTailProof':tail}
        iq=dict(iq);iq['rawStatus']=iq.get('status');iq['status']='COMPLETE_FAIL_CLOSED_ONE_SIDED_TAIL_V1';iq['completeRuntimeTrace']=True;iq['terminalTailProof']=tail
    rq=legacy_helpers.receipt_quality(mid)
    if int(rq.get('hashMismatchCount') or 0)!=0 or float(rq.get('hashMatchRate') or 0.0)<1.0:
        return {'eligible':False,'stage':'RECEIPT_FRONTIER','input':iq,'receipt':rq}
    try:
        events,rows,meta=tape_v1.build_archive_events(mid,trade_offset='mid')
        tq={'usable':len(events)>0,'events':len(events),'rows':len(rows),'normalizedTrades':int(meta.get('normalizedTrades') or 0),'firstReceivedMs':meta.get('firstReceivedMs'),'lastReceivedMs':meta.get('lastReceivedMs')}
    except Exception as exc:
        return {'eligible':False,'stage':'EXECUTION_TAPE','input':iq,'receipt':rq,'error':repr(exc)}
    if not tq['usable']:
        return {'eligible':False,'stage':'EXECUTION_TAPE','input':iq,'receipt':rq,'tape':tq}
    # IMPORTANT: no winner/settlement/PnL lookup occurs before the market is locked.
    return {'eligible':True,'input':iq,'receipt':rq,'tape':tq}


def running_score(reg:dict[str,Any])->dict[str,Any]:
    scored=[x for x in reg.get('examMarkets',[]) if x.get('scoreStatus')=='SCORED']
    pnls=[float(x['result']['realizedPnl']) for x in scored]
    wins=sum(x>EPS for x in pnls);losses=sum(x<-EPS for x in pnls)
    return {'scoredMarkets':len(scored),'totalRealizedPnl':sum(pnls),'wins':wins,'losses':losses,'winRate':wins/len(pnls) if pnls else None}


def save(reg:dict[str,Any])->None:
    reg.setdefault('examMarkets',[]).sort(key=lambda x:(int(x.get('firstSampledAtMs') or 0),int(x.get('marketId') or 0)))
    for i,row in enumerate(reg['examMarkets'],1): row['ordinal']=i
    reg['runningScore']=running_score(reg)
    target=int(reg.get('formalTargetMarkets') or 10);rs=reg['runningScore']
    if int(rs['scoredMarkets'])>=target:
        reg['formalExamComplete']=True
        reg['formalPassed']=bool(float(rs['totalRealizedPnl'])>0 and float(rs['winRate'])>=0.5)
    else:
        reg['formalExamComplete']=False;reg['formalPassed']=None
    REGISTRY.write_text(json.dumps(reg,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')


def score_locked(entry:dict[str,Any],reg:dict[str,Any])->bool:
    mid=int(entry['marketId'])
    # This is the first point where answer availability may be queried; market is already permanent in registry.
    w=legacy_helpers.settlement(mid)
    if w not in {'UP','DOWN'}:
        entry['scoreStatus']='LOCKED_SETTLEMENT_PENDING';entry['settlementAvailable']=False;save(reg);return False
    entry['settlementAvailable']=True;entry['scoreStatus']='LOCKED_READY_TO_SCORE';save(reg)
    r=candidate.run_market(mid)
    pnl=r['actualExecution']['realizedPnl']
    if pnl is None:
        entry['scoreStatus']='LOCKED_SETTLEMENT_PENDING';entry['settlementAvailable']=False;save(reg);return False
    entry['scoreStatus']='SCORED';entry['resultArtifactVersion']=r.get('version')
    entry['result']={
        'realizedPnl':pnl,'win':bool(float(pnl)>EPS),
        'paperMakerOrders':r['paperReference']['makerOrders'],'onlineMakerIntents':r['strategyRollout']['makerIntents'],
        'intentCountExact':int(r['paperReference']['makerOrders'])==int(r['strategyRollout']['makerIntents']),
        'makerRealizationRate':r['actualExecution']['makerRealizationRate'],'finalAbsTrackingError':r['actualExecution']['finalAbsTrackingError'],
        'combinedFinalAbsNet':r['actualExecution']['combinedFinalAbsNet'],'actionCounts':r['lifecycle']['actionCounts'],
        'zeroFillTakerChildren':int(r['lifecycle']['takerChildStateCounts'].get('TERMINAL_ZERO_FILL',0)),
        'cancelPendingAtDataEnd':r['lifecycle']['cancelPendingAtDataEnd'],
        'dreamFillUsedForPnl':bool(r.get('dreamFillUsedForPnl')),
        'targetRuntimeInput':bool(r.get('targetRuntimeInput')),'winnerRuntimeInput':bool(r.get('winnerRuntimeInput')),'futureLabelRuntimeInput':bool(r.get('futureLabelRuntimeInput')),
    }
    save(reg)
    print(json.dumps({'scored':mid,'ordinal':entry['ordinal'],'pnl':pnl,'running':reg['runningScore']},ensure_ascii=False),flush=True)
    return True


def main()->int:
    warnings.filterwarnings('ignore')
    freeze=json.loads(FREEZE.read_text(encoding='utf-8'));reg=json.loads(REGISTRY.read_text(encoding='utf-8'))
    hv=verify_candidate_hashes(freeze)
    if not hv['ok']:
        print(json.dumps({'ok':False,'status':'CANDIDATE_HASH_MISMATCH','hashAudit':hv},ensure_ascii=False));return 2
    # Score already-locked questions first. Their membership cannot change with the answer.
    for entry in reg.get('examMarkets',[]):
        if entry.get('scoreStatus') in {'QUALIFIED_NOT_YET_SCORED','LOCKED_SETTLEMENT_PENDING','LOCKED_READY_TO_SCORE'}:
            score_locked(entry,reg)
    target=int(reg.get('formalTargetMarkets') or 10)
    existing={int(x['marketId']):x for x in reg.get('examMarkets',[])}
    exclusions=reg.setdefault('qualityExclusions',[]);excluded={int(x['marketId']) for x in exclusions}
    rows=archive_markets_after_true_open(int(freeze['freezeEpochMs']));pending=[]
    for idx,mr in enumerate(rows):
        mid=int(mr['market_id'])
        if mid in existing or mid in excluded or len(existing)>=target: continue
        qc=quality_check_without_answer(mid)
        if not qc.get('eligible'):
            is_latest=idx==len(rows)-1;reasons=list((qc.get('input') or {}).get('reasons') or [])
            in_progress=is_latest and any(r in reasons for r in ['ROW_COUNT_LT_600','MISSING_TAIL_COVERAGE'])
            if in_progress:
                pending.append({'marketId':mid,'quality':qc});continue
            exclusions.append({'marketId':mid,'permanent':True,'quality':qc,'winnerPnlBlind':True});excluded.add(mid);continue
        entry={'ordinal':len(existing)+1,'marketId':mid,'firstSampledAtMs':int(mr['first_ms']),'qualificationLockedBeforeAnyAnswerLookup':True,
               'strategyInput':qc['input'],'receiptFrontier':qc['receipt'],'executionTape':qc['tape'],'settlementAvailable':None,'scoreStatus':'QUALIFIED_NOT_YET_SCORED'}
        reg.setdefault('examMarkets',[]).append(entry);existing[mid]=entry;save(reg)
        score_locked(entry,reg)
        if len(existing)>=target: break
    save(reg)
    print(json.dumps({'ok':True,'status':'FORMAL_COMPLETE' if reg.get('formalExamComplete') else 'WAITING_MORE_ELIGIBLE_MARKETS','hashAudit':hv,
                      'runningScore':reg['runningScore'],'formalPassed':reg.get('formalPassed'),'examMarketIds':[x['marketId'] for x in reg.get('examMarkets',[])],
                      'qualityExclusions':[x['marketId'] for x in exclusions],'pending':[x['marketId'] for x in pending],'registry':str(REGISTRY)},ensure_ascii=False,allow_nan=True))
    return 0

if __name__=='__main__': raise SystemExit(main())
