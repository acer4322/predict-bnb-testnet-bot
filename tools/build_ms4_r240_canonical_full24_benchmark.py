from __future__ import annotations
import json
from pathlib import Path
from collections import Counter, defaultdict

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'MS4_R240_CANONICAL_FULL24_BENCHMARK_20260906.json'
BATCHES=[
 'MS4_R240_STAGEA16_BATCH_A_RESULT_20260906.json',
 'MS4_R240_STAGEA16_BATCH_B_RESULT_20260906.json',
 'MS4_R240_FULL24_REMAINDER8_RESULT_20260906.json',
]
R238=P/'MS4_R238_PER_FILL_FAILURE_CAUSALITY_RESULT_20260906.json'
EXCEPTIONS={
 1945898:'near-flat; R240 zero-fill handoff occupancy/path warning',
 1946468:'near-flat tiny overflow tail',
 1946656:'true initial-exposure Core execution stall',
 1946748:'true initial-exposure Core execution stall',
 1946784:'broad Core Active destroys valuable option/path',
 1946756:'temporary-risk/sequence counterexample',
 1946683:'expensive Active Repair anatomy',
 1946899:'repayment-credit recycling/Floor counterexample',
}


def compact(r):
    return {
      'winnerPostHocOnly':r.get('winnerPostHocOnly'),
      'pnl':float(r.get('pnlDiagnosticOnly',0.0)),
      'floor':float(r.get('floor',0.0)),
      'best':float(r.get('best',0.0)),
      'fillEvents':int(r.get('fillEvents',0)),
      'filledQty':float(r.get('filledQty',0.0)),
      'submits':int(r.get('submits',0)),
      'roleSubmits':r.get('roleSubmits') or {},
      'roleFills':r.get('roleFills') or {},
      'scopeBirths':int(r.get('scopeBirths',0)),
      'scopeCompletions':int(r.get('scopeCompletions',0)),
      'scopeFlips':int(r.get('scopeFlips',0)),
      'scopeRiskCreditTotal':float(r.get('scopeRiskCreditTotal',0.0)),
      'scopeRiskCreditConsumed':float(r.get('scopeRiskCreditConsumed',0.0)),
      'scopeRiskCreditReserved':float(r.get('scopeRiskCreditReserved',0.0)),
      'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0)),
      'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),
      'handoffSubmits':int(r.get('r239HandoffSubmits',0)),
      'handoffFills':int(r.get('r239HandoffFills',0)),
      'handoffRepaidQty':float(r.get('r239HandoffRepaidQty',0.0)),
      'quarantinedHandoffRepairCredit':float(r.get('r240QuarantinedHandoffRepairCredit',0.0)),
    }


def agg(rows):
    n=len(rows); pnls=[x['pnl'] for x in rows]; floors=[x['floor'] for x in rows]
    roles_sub=Counter(); roles_fill=Counter()
    for x in rows:
        roles_sub.update(x.get('roleSubmits') or {}); roles_fill.update(x.get('roleFills') or {})
    return {
      'marketsCompleted':n,
      'wins':sum(x>0 for x in pnls),
      'winRate':sum(x>0 for x in pnls)/n if n else None,
      'totalPnl':sum(pnls),'avgPnl':sum(pnls)/n if n else None,
      'aggregateFloor':sum(floors),'avgFloor':sum(floors)/n if n else None,
      'worstMarketPnl':min(pnls) if pnls else None,
      'worstTerminalFloor':min(floors) if floors else None,
      'totalFillEvents':sum(x['fillEvents'] for x in rows),
      'totalFilledQty':sum(x['filledQty'] for x in rows),
      'totalSubmits':sum(x['submits'] for x in rows),
      'roleSubmits':dict(roles_sub),'roleFills':dict(roles_fill),
      'maxUnauthorizedOverflowQty':max((x['unauthorizedOverflowQty'] for x in rows),default=0.0),
      'maxRepairQuotaExcess':max((x['repairQuotaExcessMax'] for x in rows),default=0.0),
    }


def main():
    by={}
    for fn in BATCHES:
        d=json.load(open(P/fn,encoding='utf-8'))
        for r in d['rows']:
            mid=int(r['marketId']); cell=r['cell']
            if cell not in {'MS4_R28_CAP1_CONTROL','MS4_R240_HANDOFF_CREDIT_QUARANTINE'}: continue
            k=(mid,cell)
            if k in by: raise RuntimeError(f'duplicate {k}')
            by[k]=compact(r)
    mids=sorted({m for m,_ in by})
    if len(mids)!=24: raise RuntimeError(f'expected 24 markets, got {len(mids)}')
    fam={}
    r238=json.load(open(R238,encoding='utf-8'))
    for x in r238['markets']:
        fam[int(x['marketId'])]={'mechanismFamily':x.get('mechanismFamily'),'dominantDamageSubcomponent':x.get('dominantDamageSubcomponent'),'signatures':x.get('signatures') or []}
    markets=[]
    for m in mids:
        c=by[(m,'MS4_R28_CAP1_CONTROL')]; r=by[(m,'MS4_R240_HANDOFF_CREDIT_QUARANTINE')]
        markets.append({'marketId':m,'originalCap1Outcome':'WIN' if c['pnl']>0 else 'LOSS','failureAttribution':fam.get(m),'exceptionNote':EXCEPTIONS.get(m),'CAP1_CONTROL':c,'R240_CONTROL':r,
          'r240VsCap1':{'pnlDelta':r['pnl']-c['pnl'],'floorDelta':r['floor']-c['floor'],'bestDelta':r['best']-c['best'],'fillDelta':r['fillEvents']-c['fillEvents'],'submitDelta':r['submits']-c['submits']}})
    cap=[x['CAP1_CONTROL'] for x in markets]; r40=[x['R240_CONTROL'] for x in markets]
    wins=[x for x in markets if x['originalCap1Outcome']=='WIN']; losses=[x for x in markets if x['originalCap1Outcome']=='LOSS']
    family=defaultdict(lambda:{'marketIds':[],'cap1Pnl':0.0,'r240Pnl':0.0,'cap1Floor':0.0,'r240Floor':0.0})
    for x in losses:
        f=(x['failureAttribution'] or {}).get('mechanismFamily') or 'UNATTRIBUTED'
        a=family[f]; a['marketIds'].append(x['marketId']); a['cap1Pnl']+=x['CAP1_CONTROL']['pnl']; a['r240Pnl']+=x['R240_CONTROL']['pnl']; a['cap1Floor']+=x['CAP1_CONTROL']['floor']; a['r240Floor']+=x['R240_CONTROL']['floor']
    for a in family.values():
        a['pnlDelta']=a['r240Pnl']-a['cap1Pnl']; a['floorDelta']=a['r240Floor']-a['cap1Floor']
    out={'version':'MS4_R240_CANONICAL_FULL24_BENCHMARK_V1','date':'2026-09-06','researchOnly':True,
      'sourceBatches':BATCHES,'markets':markets,'aggregate':{'CAP1_CONTROL':agg(cap),'R240_CONTROL':agg(r40)},
      'originalWinnerCollateral':{'count':len(wins),'pnlDelta':sum(x['r240VsCap1']['pnlDelta'] for x in wins),'floorDelta':sum(x['r240VsCap1']['floorDelta'] for x in wins)},
      'originalLossRecovery':{'count':len(losses),'pnlDelta':sum(x['r240VsCap1']['pnlDelta'] for x in losses),'floorDelta':sum(x['r240VsCap1']['floorDelta'] for x in losses)},
      'familyStratified':dict(family),'exceptionMarkets':EXCEPTIONS,
      'boundary':['frozen consumed full24','same exact CAP1/R240 rows used in R240 testing','winner is scoring only','realistic HFT','no fresh data','no 8781']}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'output':str(OUT.relative_to(ROOT)),'cap1':out['aggregate']['CAP1_CONTROL'],'r240':out['aggregate']['R240_CONTROL'],'winnerCollateral':out['originalWinnerCollateral'],'lossRecovery':out['originalLossRecovery']},ensure_ascii=False))

if __name__=='__main__': main()
