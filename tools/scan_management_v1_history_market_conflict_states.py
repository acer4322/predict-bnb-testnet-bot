from __future__ import annotations
import argparse, json, math, os, tempfile, zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS
base=v3b.base


def _eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):
        return set(a.keys())==set(b.keys()) and all(_eq(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):
        return len(a)==len(b) and all(_eq(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-10)
    return a==b


def _core(r):
    return {
        'submits':r.get('submits'),'fillEvents':r.get('fillEvents'),'filledQty':r.get('filledQty'),
        'upQty':r.get('upQty'),'downQty':r.get('downQty'),'buyNotional':r.get('buyNotional'),
        'floor':r.get('floor'),'best':r.get('best'),'fillSideAlternations':r.get('fillSideAlternations'),
        'twoSidedMaterialized':r.get('twoSidedMaterialized'),'roleSubmits':r.get('roleSubmits'),
        'roleFills':r.get('roleFills'),'roleFillQty':r.get('roleFillQty'),'reanchors':r.get('reanchors'),
        'quantityLedgerSummary':r.get('quantityLedgerSummary')
    }


class ConflictStateScanner(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape):
        super().__init__(tape)
        self.scan_events=[]

    def _pure_pair_candidate(self,side):
        used={round(float(x),10) for x in self._used_prices(side)}
        for raw in self._live_price_levels(side):
            p=base.v2.kprice(raw)
            if round(float(p),10) in used:
                continue
            if not self._pair_ok(side,p):
                continue
            return {'side':side,'price':float(p),'qty':float(1.0/p)}
        return None

    def _repair_role_for_side(self,side):
        return 'ECONOMIC_CORE' if self._core_for_side(side) is None else 'SATELLITE_REPAIR'

    def _repair_menu(self):
        out=[]
        managed_claim=bool(self.q_ladder is not None or self.q_pending_active is not None)
        for side in ('UP','DOWN'):
            debt=float(self._aggregate_for_repair_side(side))
            if debt<=EPS:
                continue
            cand=self._pure_pair_candidate(side)
            if cand is None:
                continue
            live_rep=[]
            for sid,key,o,role in self._live_role_rows(side=side):
                if role in v3b.REPAIR_ROLES:
                    live_rep.append({'slotId':int(sid),'key':key,'role':role,'price':float(o.get('price') or 0.0),
                                     'cum':float(o.get('cum') or 0.0),'cancelRequested':bool(o.get('cancelRequested'))})
            out.append({**cand,'role':self._repair_role_for_side(side),'aggregateOutstandingDebt':debt,
                        'managedClaimPresent':managed_claim,'liveRepairCarriers':live_rep,
                        'unclaimed':(not managed_claim and len(live_rep)==0)})
        return out

    def _clean_history(self,t):
        by={}
        for a in self.fill_accounting:
            tt=int(a.get('t') or 0)
            if tt>=int(t):
                continue
            if float(a.get('confirmedQty') or 0.0)<=EPS:
                continue
            by.setdefault(tt,[]).append(a)
        clean=[]
        for tt in sorted(by):
            rows=by[tt]
            sides={str(x.get('side')) for x in rows}
            if len(sides)!=1:
                continue
            if any(float(x.get('matchedRepairQty') or 0.0)>EPS for x in rows):
                continue
            overflow=sum(float(x.get('overflowQty') or 0.0) for x in rows)
            if overflow<=EPS:
                continue
            side=next(iter(sides))
            clean.append({'t':int(tt),'side':side,'riskQty':float(overflow),
                          'roles':sorted({str(x.get('role')) for x in rows})})
        if not clean:
            return None
        last=clean[-1]
        recent=clean[-3:]
        run=1
        for x in reversed(clean[:-1]):
            if x['side']!=last['side']:
                break
            run+=1
        up=sum(1 for x in recent if x['side']=='UP')
        risk_up=sum(float(x['riskQty']) for x in recent if x['side']=='UP')
        risk_dn=sum(float(x['riskQty']) for x in recent if x['side']=='DOWN')
        risk_side='UP' if risk_up>risk_dn+EPS else ('DOWN' if risk_dn>risk_up+EPS else 'TIE')
        return {
            'priorCleanSide':last['side'],'priorCleanAgeMs':int(t)-int(last['t']),
            'priorCleanRunLength':int(run),'recentCleanCount':len(recent),
            'recentCleanUpRatio':float(up/len(recent)),'recentRiskWeightedSide':risk_side,
            'recentRiskUpQty':float(risk_up),'recentRiskDownQty':float(risk_dn),
            'last3Clean':recent
        }

    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)>base.v2.NO_NEW_EXPOSURE_MS:
            side,role,_,_=base.MinimalPairRoleSim._role_decision(self,qv)
            history=self._clean_history(t)
            if role=='SATELLITE_EXPAND' and history is not None:
                native=self._pure_pair_candidate(side)
                repair=self._repair_menu()
                unclaimed=[x for x in repair if x['unclaimed']]
                free=max(0,int(self.max_slots)-len(self.slot_key))
                conflict=history['priorCleanSide']!=side
                if conflict and native is not None and free>0 and unclaimed:
                    self.scan_events.append({
                        't':int(t),'marketProposalSide':side,'baselineRole':role,
                        'history':history,'nativeReexpand':native,'repairAlternatives':unclaimed,
                        'legalActions':['HOLD','NATIVE_REEXPAND','NATIVE_REPAIR'],
                        'freeSlotsBeforeOpen':free,'slotCountBeforeOpen':len(self.slot_key),
                        'inventory':{'UP':float(self.inv['UP']),'DOWN':float(self.inv['DOWN'])},
                        'cost':float(self.cost),'branchPayoffs':{'UP':float(self.inv['UP']-self.cost),'DOWN':float(self.inv['DOWN']-self.cost)},
                        'aggregateDebt':{'repairUP':float(self._aggregate_for_repair_side('UP')),'repairDOWN':float(self._aggregate_for_repair_side('DOWN'))},
                        'qLadderPresent':self.q_ladder is not None,'qPendingActivePresent':self.q_pending_active is not None,
                    })
        return super()._open_one_option(t,qv,end)

    def run_scan(self):
        r=super().run_qty('__UNSCORED__')
        r['historyMarketConflictEvents']=self.scan_events[:10000]
        return r


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--bundle',required=True)
    ap.add_argument('--market-ids',required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    rows=[]
    with tempfile.TemporaryDirectory(prefix='mgmt_hist_conflict_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:
                if mid not in co:
                    raise RuntimeError(f'market missing from bundle: {mid}')
                z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz'
            A=v3b.FifoAggregateResponsibilityLadderV3B(tape)
            try:ra=A.run_qty('__UNSCORED__')
            finally:A.close()
            B=ConflictStateScanner(tape)
            try:rb=B.run_scan()
            finally:B.close()
            ca,cb=_core(ra),_core(rb)
            parity={k:_eq(ca[k],cb[k]) for k in ca}
            ev=rb.get('historyMarketConflictEvents') or []
            rows.append({'marketId':mid,'allBehaviorParity':all(parity.values()),'parity':parity,
                         'ledgerViolations':(rb.get('quantityLedgerSummary') or {}).get('invariantViolations'),
                         'eligibleCount':len(ev),'events':ev})
            print(json.dumps({'progress':i,'marketId':mid,'parity':all(parity.values()),'eligibleCount':len(ev)},ensure_ascii=False),flush=True)
    eligible=[]
    for row in rows:
        for ev in row['events']:
            for alt in ev['repairAlternatives']:
                eligible.append({'marketId':row['marketId'],'t':ev['t'],'marketProposalSide':ev['marketProposalSide'],
                                 'history':ev['history'],'nativeReexpand':ev['nativeReexpand'],'nativeRepair':alt,
                                 'inventory':ev['inventory'],'cost':ev['cost'],'branchPayoffs':ev['branchPayoffs'],
                                 'aggregateDebt':ev['aggregateDebt'],'legalActions':ev['legalActions']})
    eligible.sort(key=lambda x:(x['marketId'],x['t'],x['nativeRepair']['side'],x['nativeRepair']['price']))
    first_per_market=[];seen=set()
    for x in eligible:
        if x['marketId'] in seen:
            continue
        seen.add(x['marketId']);first_per_market.append(x)
    out={
        'version':'MANAGEMENT_TRAINING_V1_HISTORY_MARKET_CONFLICT_STATE_SCAN_V1','date':'2026-09-07',
        'researchOnly':True,'runtimeAuthority':False,'markets':mids,
        'allBehaviorParity':all(x['allBehaviorParity'] for x in rows),
        'rows':rows,'eligibleStateCount':len(eligible),'eligibleMarketCount':len(seen),
        'firstEligiblePerMarket':first_per_market,
        'smokeCandidates':first_per_market[:3],
        'selectionContract':[
            'strict-past current-V3B exact-FIFO state only',
            'prior clean risk clock requires single-side confirmed fills, zero matchedRepairQty, positive overflowQty',
            'market proposal is current V3B baseline depth-direction at a native SATELLITE_EXPAND decision clock',
            'require priorCleanSide != current marketProposalSide',
            'require native reexpand candidate, at least one unclaimed native repair alternative, and free physical slot',
            'action menu is HOLD / NATIVE_REEXPAND / NATIVE_REPAIR',
            'no winner/PnL/Target future input in state selection',
            'behavior-inert scanner parity checked against baseline V3B',
            'realistic HFT/exact-FIFO/max4; no NEW24-B/no 8781'
        ]
    }
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
    op.parent.mkdir(parents=True,exist_ok=True)
    op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity'],'eligibleStateCount':len(eligible),'eligibleMarketCount':len(seen),'smokeCandidates':out['smokeCandidates']},ensure_ascii=False),flush=True)

if __name__=='__main__':
    main()
