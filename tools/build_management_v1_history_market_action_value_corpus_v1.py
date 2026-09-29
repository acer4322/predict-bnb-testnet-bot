from __future__ import annotations
import argparse,json,math,os
from pathlib import Path

EPS=1e-12
HORIZONS=(500,1000,2000,5000,10000)

def f(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--replication',required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args()
    src=json.loads(Path(a.replication).read_text(encoding='utf-8'))
    rows=[]
    for r in src.get('rows',[]):
        if not r.get('valid'): continue
        s=r['stateSpec']; hist=s.get('history') or {}; inv=s.get('inventory') or {}; bp=s.get('branchPayoffs') or {}; debt=s.get('aggregateDebt') or {}
        live=s.get('liveRoles') or []
        counts={'core':0,'repair':0,'expand':0,'cancel':0,'up':0,'down':0}
        for x in live:
            role=str(x.get('role') or '')
            if 'CORE' in role: counts['core']+=1
            if 'REPAIR' in role: counts['repair']+=1
            if 'EXPAND' in role: counts['expand']+=1
            if x.get('cancelRequested'): counts['cancel']+=1
            side=str(x.get('side') or '')
            if side=='UP': counts['up']+=1
            elif side=='DOWN': counts['down']+=1
        up=float(inv.get('UP') or 0.0); dn=float(inv.get('DOWN') or 0.0); cost=float(s.get('cost') or 0.0)
        pu=float(bp.get('UP') or 0.0); pd=float(bp.get('DOWN') or 0.0)
        current_floor=min(pu,pd); current_best=max(pu,pd); current_gap=current_best-current_floor
        mc=s.get('marketCandidate') or {}; hc=s.get('historyCandidateDiagnostic') or {}; ql=s.get('qLadder') or {}
        market_side=str(s.get('marketProposalSide') or '')
        hist_side=str(s.get('historyForcedSide') or '')
        for branch in ('HISTORY_DIRECTION','HOLD'):
            intr=r['interventions'][branch] or {}
            events=[x for x in (intr.get('newSlotEvents') or []) if x.get('event')=='ROLE_SLOT_SUBMIT']
            ev=events[0] if events else {}
            action_role=str(ev.get('role') or ('HOLD' if branch=='HOLD' else s.get('historyForcedRole') or ''))
            action_side=str(ev.get('side') or '')
            row={
                'marketId':int(r['marketId']),'t':int(r['t']),'action':branch,
                'actionHistory':1 if branch=='HISTORY_DIRECTION' else 0,
                'actionHold':1 if branch=='HOLD' else 0,
                'actionHasSide':1 if action_side in ('UP','DOWN') else 0,
                'actionSideUp':1 if action_side=='UP' else 0,
                'actionRepair':1 if 'REPAIR' in action_role else 0,
                'actionExpand':1 if 'EXPAND' in action_role else 0,
                'actionPrice':f(ev.get('price')),'actionQty':f(ev.get('qty')),
                'marketSideUp':1 if market_side=='UP' else 0,
                'historySideUp':1 if hist_side=='UP' else 0,
                'historyAgeMs':f(hist.get('priorCleanAgeMs')),
                'historyRunLength':f(hist.get('priorCleanRunLength')),
                'recentCleanCount':f(hist.get('recentCleanCount')),
                'recentCleanUpRatio':f(hist.get('recentCleanUpRatio')),
                'recentRiskUpQty':f(hist.get('recentRiskUpQty')),
                'recentRiskDownQty':f(hist.get('recentRiskDownQty')),
                'recentRiskNet':float(hist.get('recentRiskUpQty') or 0.0)-float(hist.get('recentRiskDownQty') or 0.0),
                'upQty':up,'downQty':dn,'cost':cost,'absNet':abs(up-dn),'grossQty':up+dn,
                'currentUpPayoff':pu,'currentDownPayoff':pd,'currentFloor':current_floor,'currentBest':current_best,'currentGap':current_gap,
                'repairDebtUP':float(debt.get('repairUP') or 0.0),'repairDebtDOWN':float(debt.get('repairDOWN') or 0.0),'totalDebt':float(debt.get('repairUP') or 0.0)+float(debt.get('repairDOWN') or 0.0),
                'marketCandidatePrice':f(mc.get('price')),'marketCandidateQty':f(mc.get('qty')),
                'historyCandidatePrice':f(hc.get('price')),'historyCandidateQty':f(hc.get('qty')),
                'candidateCrossSum':(float(mc.get('price'))+float(hc.get('price'))) if mc.get('price') is not None and hc.get('price') is not None else None,
                'candidatePriceDiffHistoryMinusMarket':(float(hc.get('price'))-float(mc.get('price'))) if mc.get('price') is not None and hc.get('price') is not None else None,
                'freeSlots':f(s.get('freeSlots')),'liveSlots':len(live),'liveCoreCount':counts['core'],'liveRepairCount':counts['repair'],'liveExpandCount':counts['expand'],'liveCancelPendingCount':counts['cancel'],'liveUpCount':counts['up'],'liveDownCount':counts['down'],
                'qLadderPresent':1 if ql else 0,'qLadderSideUp':1 if str(ql.get('side') or '')=='UP' else 0,'qLadderRepair':1 if 'REPAIR' in str(ql.get('role') or '') else 0,
                'pendingActive':1 if bool(s.get('qPendingActive')) else 0,
            }
            tm=r['terminalDeltaVsMarketDirection'][branch]
            row.update({
                'dFloorTerminal':float(tm['floor']),'dBestTerminal':float(tm['best']),'dGapTerminal':float(tm['gap']),
                'dFillsTerminal':float(tm['fills']),'dBuyNotionalTerminal':float(tm['buyNotional']),'dActiveSubmitsTerminal':float(tm['activeSubmits']),'dManagedRepairQtyTerminal':float(tm['managedRepairQty'])
            })
            for h in HORIZONS:
                q=(r['localDeltaVsMarketDirection'].get(str(h)) or {}).get(branch) or {}
                row[f'dFloor{h}ms']=float(q.get('floor') or 0.0); row[f'dBest{h}ms']=float(q.get('best') or 0.0); row[f'dGap{h}ms']=float(q.get('gap') or 0.0)
            rows.append(row)
    out={
        'version':'MANAGEMENT_TRAINING_V1_HISTORY_MARKET_ACTION_VALUE_CORPUS_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,
        'sourceVersion':src.get('version'),'sourceStateCount':src.get('stateCount'),'rows':rows,
        'markets':len(set(int(x['marketId']) for x in rows)),'actionsPerMarket':2,
        'boundary':['one outcome-blind first eligible state per consumed market from exact-fork replication','counterfactual rows are HISTORY_DIRECTION and HOLD; MARKET_DIRECTION is reference value 0','all features are strict-past state/action descriptors only','future branch outcomes are targets only','no winner/settlement/Target future action features','realistic HFT/exact FIFO/max4/no dream fill/no NEW24-B/no 8781']
    }
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output)
    op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'rows':len(rows),'markets':out['markets']},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
