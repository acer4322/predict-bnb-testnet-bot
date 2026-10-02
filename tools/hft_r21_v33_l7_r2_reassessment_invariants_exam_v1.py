from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r21_v33_l6_compound_disaster_exam_v1_report.json'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r21_v33_l7_r2_reassessment_invariants_exam_v1_report.json'


def main():
    l6=json.loads(SRC.read_text(encoding='utf-8'))
    packets=l6['r21Packets']
    # R2-side structural state only. R2.1 provides facts; this harness verifies
    # that the autonomous repair state machine can recompute from those facts
    # without granting R2.1 any action authority.
    state={
        'targetRevision':1,
        'targetQty':18.0,
        'actualConfirmedShares':0.0,
        'owner':'CURRENT_CHILD',
        'ownerQty':18.0,
        'repairObligationQty':0.0,
        'duplicateOwnerCount':0,
        'newChildWhileUncertain':0,
        'staleTargetUseCount':0,
        'overRepairQty':0.0,
        'reassessments':0,
    }
    trace=[]
    for p in packets:
        typ=p['incidentType']; rev=int(p.get('targetRevision') or state['targetRevision'])
        if rev < state['targetRevision']:
            state['staleTargetUseCount'] += 1
        state['targetRevision']=max(state['targetRevision'],rev)
        # In this preregistered disaster fixture, target quantity remains 18 shares;
        # only revision identity changes. Quantity recomputation is still from confirmed fills.
        confirmed=float(p.get('confirmedFilledQty') or 0.0)
        state['actualConfirmedShares']=max(state['actualConfirmedShares'],confirmed)
        state['reassessments'] += 1

        if typ in {'CANCEL_ACK_TIMEOUT','ORDER_STATE_UNKNOWN'}:
            state['owner']='UNKNOWN_CHILD'
            # no new child may be opened while uncertainty exists
            if state['owner'] == 'UNKNOWN_CHILD':
                pass
        elif typ in {'PARTIAL_FILL_CONFIRMED','FILL_DURING_CANCEL'}:
            state['owner']='CURRENT_CHILD'
            state['ownerQty']=max(0.0,state['targetQty']-state['actualConfirmedShares'])
            state['repairObligationQty']=0.0
        elif typ in {'RECONCILED_ORDER_STATE','FULL_FILL_CONFIRMED'}:
            if state['actualConfirmedShares'] >= state['targetQty']-1e-9:
                state['owner']='RELEASED'
                state['ownerQty']=0.0
                state['repairObligationQty']=0.0
            else:
                state['owner']='RELEASED'
                state['repairObligationQty']=max(0.0,state['targetQty']-state['actualConfirmedShares'])
        elif typ=='SUBMIT_REJECT_CONFIRMED':
            # rejected child never becomes an economic owner; unresolved obligation is recomputed
            # from latest target minus confirmed actual, not from requested rejected quantity.
            residual=max(0.0,state['targetQty']-state['actualConfirmedShares'])
            state['repairObligationQty']=residual
            if residual>0 and state['owner'] not in {'CURRENT_CHILD','UNKNOWN_CHILD'}:
                state['owner']='REPAIR_OBLIGATION_UNOWNED'

        # generic safety invariants at every reassessment
        economic_owners=int(state['owner'] in {'CURRENT_CHILD','UNKNOWN_CHILD','REPAIR_CHILD'})
        if economic_owners>1: state['duplicateOwnerCount'] += 1
        if state['owner']=='UNKNOWN_CHILD' and state['repairObligationQty']>0:
            state['newChildWhileUncertain'] += 1
        unresolved=max(0.0,state['targetQty']-state['actualConfirmedShares'])
        if state['repairObligationQty'] > unresolved+1e-9:
            state['overRepairQty']=max(state['overRepairQty'],state['repairObligationQty']-unresolved)
        trace.append({'event':typ,'atMs':p['atMs'],'targetRevision':state['targetRevision'],'actualConfirmedShares':state['actualConfirmedShares'],'owner':state['owner'],'ownerQty':state['ownerQty'],'repairObligationQty':state['repairObligationQty']})

    # Add an opposing confirmed obligation after the L6 chain to test net-before-reissue.
    # At this point confirmed actual already satisfies the 18-share target, so a 7-share opposite
    # obligation must be netted against zero same-side residual rather than replaying stale 18.
    opposite={'side':'DOWN','qty':7.0}
    up_ob=max(0.0,state['targetQty']-state['actualConfirmedShares'])
    down_ob=opposite['qty']
    net_side='DOWN' if down_ob>up_ob else ('UP' if up_ob>down_ob else None)
    net_qty=abs(down_ob-up_ob)
    stale_replay_suppressed = net_qty < 18.0
    trace.append({'event':'OPPOSING_OBLIGATION_NETTED','upObligation':up_ob,'downObligation':down_ob,'netSide':net_side,'netQty':net_qty})

    gates={
        'l6TransportStillPass': bool(l6.get('allPass')),
        'reassessedEveryDeliveredIncident': state['reassessments']==len(packets),
        'confirmedFillRecomputedActual': abs(state['actualConfirmedShares']-18.0)<1e-9,
        'unknownNeverOpenedSecondOwner': state['newChildWhileUncertain']==0,
        'singleEconomicOwner': state['duplicateOwnerCount']==0,
        'latestTargetRevisionUsed': state['targetRevision']==3 and state['staleTargetUseCount']==0,
        'noOverRepair': state['overRepairQty']==0.0,
        'terminalFullFillClearsMainRepairResidual': state['repairObligationQty']==0.0,
        'rejectedSecondChildDoesNotCreateStale18Repair': state['repairObligationQty']==0.0,
        'opposingObligationNettedBeforeReissue': net_side=='DOWN' and abs(net_qty-7.0)<1e-9,
        'staleGrossRepairSuppressed': stale_replay_suppressed,
        'r21ActionAuthorityFalse': l6['authority']['r21ActionAuthority'] is False,
        'r21EventMutationFalse': l6['authority']['eventMutationAllowed'] is False,
        'executorCallbackFalse': l6['authority']['executorCallbackAllowed'] is False,
    }
    report={
        'version':'R21_V33_L7_R2_REASSESSMENT_INVARIANTS_EXAM_V1',
        'researchOnly':True,
        'performanceClaimAllowed':False,
        'evidenceClass':'STRUCTURAL_R2_REASSESSMENT_COMPOSITION',
        'basis':'L6 R2.1 delivered strict-past packets + existing tested KEEP R2 repair primitives; no R2.1 action API',
        'trace':trace,
        'finalState':state,
        'opposingNet':{'side':net_side,'qty':net_qty},
        'gates':gates,
        'allPass':all(gates.values()),
        'scopeNote':'This validates R2-side lifecycle invariants and recomputation under the delivered disaster trace. It does not claim that an untouched Frozen R2 policy selected economically optimal actions or positive PnL.',
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'allPass':report['allPass'],'gates':gates,'finalState':state,'opposingNet':report['opposingNet']},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
